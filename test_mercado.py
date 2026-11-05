"""test_mercado.py — pruebas con RESPUESTA CONOCIDA del almacen de mercado.

No son pruebas de humo: cada una tiene una respuesta que se sabe de antemano
porque se miro el caso a mano en la base antes de escribir el codigo.

  PH City Tower      5 ids, uno de ellos a OTRO precio ($649 vs $599), tienen que
                     colapsar en 1 unidad. Es la republicacion con rebaja, que es
                     la que mas facil se escapa.
  Torres de Cantabria un id murio y otro sigue vivo: la unidad NO puede quedar
                     dada de baja. Ese seria un alquiler inventado.
  Filas basura       41 anuncios con precio < 50 y titulo "Sin Titulo". Si se
                     agrupan por esa huella, 31 apartamentos sin relacion se
                     funden en uno (paso de verdad la primera corrida).
  Recamaras          ninguna unidad puede mezclar 1 rec con 2 rec.
  2026-09-02         crawl cortado: no puede ser dia util ni confirmar bajas.
  Kaplan-Meier       contra un calculo hecho a mano, incluida la entrada diferida.

Corre: python3 test_mercado.py   (necesita mercado.db con la migracion hecha)
"""
import sqlite3, sys, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mercado_consulta as M

# Se prueba sobre una COPIA de mercado.db, nunca sobre la base real.
# Por que (hallazgo del 2026-11-05): M.preparar() llama a marcar_bajas(), que
# hace UPDATE listing + commit, asi que correr las pruebas ESCRIBIA en la base
# de produccion. Nunca corrompio nada —esas columnas son derivadas y se
# recalculan identicas sobre los mismos datos— pero convertia el chequeo de
# salud en una escritura, y eso si molesta: cada sesion de diagnostico corre
# esta suite, y al hacerlo movia el mtime de mercado.db, que es justo la senal
# que uno mira para contestar "¿algo escribio en produccion hoy?". El 05/11 me
# costo un rato descartar que el mtime de hoy fuera del crawl bloqueado.
# La copia tiene que ser de la base DE VERDAD y no un fixture: las aserciones
# son sobre los datos reales acumulados (los ids de PH City Tower, el crawl
# cortado del 2026-09-02). Con una base inventada no probarian nada.
_dir_copia = Path(tempfile.mkdtemp(prefix='test_mercado_'))
_copia = _dir_copia / 'mercado.db'
shutil.copy2(Path(__file__).resolve().parent / 'mercado.db', _copia)

cx = sqlite3.connect(_copia)
M.preparar(cx, 'alquiler')
cx.row_factory = sqlite3.Row
us = {u['unidad_id']: u for u in M.unidades_resumen(cx, 'alquiler')}
uid = dict(cx.execute('select id, unidad_id from unidad').fetchall())

fallos = []
def check(nombre, cond, detalle=''):
    print(('  OK   ' if cond else '  FALLA') + f' {nombre} {detalle}')
    if not cond: fallos.append(nombre)

print('=== PRUEBAS DE UNIDAD FISICA ===')
city = ['32931499','32890577','32949887','32960551','32971836']
g = {uid.get(i) for i in city}
check('PH City Tower: 5 ids -> 1 unidad', len(g)==1, f'(dio {len(g)})')

cant = ['32871457','31933239']
gc = {uid.get(i) for i in cant}
check('Torres de Cantabria: 2 ids -> 1 unidad', len(gc)==1, f'(dio {len(gc)})')
u = us.get(list(gc)[0]) if len(gc)==1 else None
check('Torres de Cantabria: sigue VIVA (no invento alquiler)',
      u is not None and u['baja_confirmada'] is None)

ac = ['32987554','32992254','33006214']
check('PH Acacias Versalles: 3 ids -> 1 unidad', len({uid.get(i) for i in ac})==1)

basura = M.filas(cx, '''select u.unidad_id, count(*) n from unidad u
    join listing l on l.id=u.id where l.precio_inicial<50 group by 1 having n>1''')
check('filas basura (precio<50) NO se agrupan', not basura, f'({len(basura)} grupos)')

# Falso positivo a vigilar: dos apartamentos DISTINTOS del mismo edificio.
mix = M.filas(cx, '''select u.unidad_id, count(distinct l.recamaras) r,
    count(distinct l.precio_inicial) p, count(*) n from unidad u
    join listing l on l.id=u.id where l.operacion='alquiler'
    group by 1 having r > 1''')
check('ninguna unidad mezcla recamaras distintas', not mix, f'({len(mix)} casos)')

print()
print('=== PRUEBAS DE CENSURA POR CRAWL ===')
utiles = M.dias_utiles(cx, 'alquiler')
check('2026-09-02 (crawl cortado) NO es dia util', '2026-09-02' not in utiles)
check('2026-09-01 y 2026-09-03 SI son utiles',
      '2026-09-01' in utiles and '2026-09-03' in utiles)
n02 = cx.execute("select count(*) from listing where baja_confirmada='2026-09-02'").fetchone()[0]
check('ninguna baja confirmada cae el 2026-09-02', n02==0, f'({n02})')

print()
print('=== PRUEBA DE KAPLAN-MEIER (respuesta a mano) ===')
# 5 sujetos, sin truncamiento: eventos en 2 y 5, censurados en 3 y 6.
# t=2: n=5, d=1 -> S=0.8   t=5: en riesgo {5,6} = 2, d=1 -> S=0.8*0.5=0.4
s = [(0,2,1),(0,3,0),(0,5,1),(0,6,0),(0,5,1)]
# con dos eventos en t=5: n en riesgo en t=5 = sujetos con salida>=5 = 3
curva, med, n, ev = M.km(s)
esperado = [(2, 0.8), (5, 0.8*(1-2/3))]
ok = len(curva)==2 and abs(curva[0][1]-0.8)<1e-9 and abs(curva[1][1]-esperado[1][1])<1e-9
check('curva KM coincide con el calculo a mano', ok, f'{[(t,round(x,4)) for t,x,_,_ in curva]}')
check('mediana = 5 (primer t con S<=0.5)', med==5, f'(dio {med})')

# Truncamiento por izquierda: un sujeto que entra en t=4 no puede estar en
# riesgo en t=2, asi que no debe cambiar el primer escalon.
s2 = s + [(4,9,0)]
c2, _, _, _ = M.km(s2)
check('entrada diferida: el sujeto que entra en t=4 no altera el escalon t=2',
      abs(c2[0][1]-0.8)<1e-9, f'{round(c2[0][1],4)}')
check('entrada diferida: SI entra en el riesgo de t=5',
      c2[1][2] == 4, f'(n en riesgo t=5 = {c2[1][2]}, esperado 4)')

print()
print('=== PRUEBA DEL CRUDO DE VENTA (no pisar lo bueno con lo vacio) ===')
# Agregada el 2026-10-18: con el bloqueo de Cloudflare puesto, `recorrer`
# vuelve con 0 anuncios y error_fatal en la primera request. `venta.py`
# escribe el crudo ANTES de mirar error_fatal, asi que ese dia habria pisado
# venta_crudo.json con un archivo de 0 anuncios. Lo que se prueba es el
# guarda: vacio+fatal NO escribe, pero un crawl que SI trajo paginas se
# guarda igual aunque haya terminado roto, que es para lo que existe el crudo.
import json as _json, tempfile as _tmpf, shutil as _sh
import mercado as _mk, venta as _vt

_tmp = Path(_tmpf.mkdtemp(prefix='test_venta_'))
_vt.CRUDO, _mk.DB, _mk.HISTORICO = (_tmp / 'venta_crudo.json',
                                    _tmp / 'mercado.db', _tmp / 'historico')

def _stub(ads, fatal, reqs):
    salud = {'inicio': 'x', 'fin': 'x', 'categorias': {}, 'avisos': [],
             'requests': reqs, 'bloqueado': bool(fatal), 'error_fatal': fatal,
             'anuncios_unicos': len(ads)}
    return lambda filtros, max_pages=None, rango_precio=None: (
        {a['id']: a for a in ads}, salud)

def _correr(ads, fatal, reqs):
    _vt.CRUDO.write_text(_json.dumps(
        {'salud': {'requests': 155, 'error_fatal': None},
         'anuncios': [{'id': 'viejo'}]}), encoding='utf-8')
    _vt.recorrer = _stub(ads, fatal, reqs)
    sys.argv = ['venta.py', '--fecha', '2026-10-18', '--max-paginas', '1']
    rc = _vt.main()
    return rc, [a['id'] for a in _json.loads(_vt.CRUDO.read_text())['anuncios']]

try:
    rc, ids = _correr([], 'HTTP 403 — desafio JS de Cloudflare', 1)
    check('bloqueado: devuelve 2 y NO ingesta', rc == 2, f'(dio {rc})')
    check('bloqueado: el crudo bueno queda intacto', ids == ['viejo'], f'(quedo {ids})')

    rc, ids = _correr([{'id': 'n1'}, {'id': 'n2'}], 'ParseError: algo', 40)
    check('crawl parcial: el crudo SI se escribe', ids == ['n1', 'n2'], f'(quedo {ids})')
    check('crawl parcial: igual no ingesta (devuelve 2)', rc == 2, f'(dio {rc})')

    _ads = [{'id': f'a{i}', 'titulo': 't', 'precio': 120000, 'recamaras': 2,
             'zona_cruda': 'z', 'foto': 'f', 'url': 'u'} for i in range(5)]
    rc, ids = _correr(_ads, None, 155)
    check('crawl normal: devuelve 0 y escribe los 5', rc == 0 and len(ids) == 5,
          f'(rc={rc}, {len(ids)} anuncios)')
finally:
    _sh.rmtree(_tmp, ignore_errors=True)

cx.close()
_sh.rmtree(_dir_copia, ignore_errors=True)

print()
print(('TODAS LAS PRUEBAS PASARON' if not fallos else f'FALLARON: {fallos}'))
sys.exit(1 if fallos else 0)
