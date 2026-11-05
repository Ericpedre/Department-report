#!/usr/bin/env python3
"""
render.py — vuelca los veredictos de Claude en el HTML del dia.

Este script es MECANICO a proposito: no decide nada, no ordena por gusto, no
escribe prosa. Las palabras son todas de Claude (veredictos.json); aca solo se
arma el andamiaje. Se separa asi para que un HTML mal cerrado no pueda tumbar
el reporte de un dia entero.

Las fotos se enlazan directo al CDN de Encuentra24 (hotlink verificado: 200 sin
referer). Asi el repo de GitHub Pages queda liviano y no crece 3 MB por dia.
"""
import argparse, html, json, sqlite3, sys
from datetime import datetime
from pathlib import Path

from perfiles import ORDEN, PERFILES, perfil as cargar_perfil, sufijo

ROOT = Path(__file__).resolve().parent
PUB = ROOT / "publish"

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--tx:#14171a;--mut:#5b6570;--bd:#e3e7ec;
--ok:#0f7b3f;--okbg:#e8f6ee;--rev:#8a6100;--revbg:#fdf3dc;--no:#8a2233;--nobg:#fbeaec;
--star:#5b02c5;--starbg:#f1e8ff}
@media(prefers-color-scheme:dark){:root{--bg:#12151a;--card:#1a1f26;--tx:#e8ecf1;
--mut:#98a3b0;--bd:#2b333d;--ok:#5cd08a;--okbg:#12301f;--rev:#e0b34d;--revbg:#332a12;
--no:#f08a9a;--nobg:#331a1f;--star:#c9a4ff;--starbg:#2a1c42}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--tx);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:21px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:14px}
.resumen{background:var(--card);border:1px solid var(--bd);border-left:3px solid var(--star);
padding:12px 14px;border-radius:8px;margin-bottom:18px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
margin:22px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--bd)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;
margin-bottom:10px;overflow:hidden;display:flex;gap:12px}
.card.star{border-color:var(--star);box-shadow:0 0 0 1px var(--star)}
.foto{width:132px;min-width:132px;height:132px;object-fit:cover;background:var(--bd)}
.body{padding:10px 12px 10px 0;flex:1;min-width:0}
.t{font-weight:600;margin-bottom:3px}
.t a{color:inherit;text-decoration:none}
.t a:hover{text-decoration:underline}
.precio{font-size:19px;font-weight:700}
.datos{color:var(--mut);font-size:13px;margin:3px 0}
.razon{font-size:13.5px;margin-top:6px}
.tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 7px;
border-radius:99px;text-transform:uppercase;letter-spacing:.04em;margin-right:5px}
.cumple{background:var(--okbg);color:var(--ok)}
.revisar{background:var(--revbg);color:var(--rev)}
.descartado{background:var(--nobg);color:var(--no)}
.gana{background:var(--starbg);color:var(--star)}
.incluye{font-size:12.5px;color:var(--mut);margin-top:4px}
.vacio{background:var(--card);border:1px dashed var(--bd);padding:22px;
text-align:center;color:var(--mut);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:5px 8px;border-bottom:1px solid var(--bd);text-align:left}
.inc{background:var(--nobg);border:1px solid var(--no);padding:10px 14px;
border-radius:8px;margin-bottom:12px;font-size:13.5px}
.inc summary{cursor:pointer;color:var(--no);font-size:14px}
.inc p{margin:8px 0}
.inc.avisos{background:var(--revbg);border-color:var(--rev)}
.inc.avisos summary{color:var(--rev)}
footer{margin-top:28px;padding-top:12px;border-top:1px solid var(--bd);
color:var(--mut);font-size:12px}
.scroll{overflow-x:auto}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def destacado(v, p):
    """Valor del campo con que ESTE perfil marca un hallazgo bueno.

    El principal lo marca con `supera_mystic` (booleano); el economico con
    `ahorro_mensual` (numero de dolares). Devuelve el valor si corresponde
    destacar, o None. Un ahorro de 0 no destaca nada.
    """
    val = (v or {}).get(p["campo_destacado"])
    return val if val else None


def tarjeta(a, v, p):
    cls = (v or {}).get("clasificacion", "revisar")
    gana = destacado(v, p)
    tags = f'<span class="tag {esc(cls)}">{esc(cls)}</span>'
    if gana:
        etq = p["tag_destacado"]
        etq = etq.format(esc(gana)) if "{}" in etq else etq
        tags += f'<span class="tag gana">{etq}</span>'
    if (v or {}).get("hay_que_amoblar"):
        tags += '<span class="tag revisar">hay que amoblar</span>'

    ap = (v or {}).get("apariencia")
    datos = [f"{a.get('recamaras') or '?'} rec", f"{a.get('banos') or '?'} baños",
             f"{a.get('estacionamientos') or 0} est"]
    if a.get("metros2"):
        datos.append(f"{a['metros2']} m²")
    if a.get("zona"):
        datos.append(esc(a["zona"]))
    if ap:
        datos.append(f"se ve {esc(ap).replace('_', ' ')}")

    ant = ""
    if a.get("precio_anterior"):
        ant = f' <span class="datos">(antes ${esc(a["precio_anterior"])})</span>'
    mant = ""
    if a.get("mantenimiento"):
        mant = f' + ${esc(a["mantenimiento"])} mant.'

    foto = a.get("foto") or ""
    img = (f'<img class="foto" src="{esc(foto)}" alt="" loading="lazy">'
           if foto else '<div class="foto"></div>')
    inc = (v or {}).get("incluye")
    return f"""<div class="card{' star' if gana else ''}">{img}<div class="body">
<div class="t"><a href="{esc(a.get('url'))}" target="_blank" rel="noopener">{esc(a.get('titulo') or 'Sin título')}</a></div>
<div class="precio">${esc(a.get('precio'))}{mant}</div>{ant}
<div class="datos">{' · '.join(datos)}</div>
{tags}
<div class="razon">{esc((v or {}).get('razon', ''))}</div>
{f'<div class="incluye">Incluye: {esc(inc)}</div>' if inc else ''}
</div></div>"""


def seccion(titulo, items, ver, vacio_txt, p):
    if not items:
        return f"<h2>{esc(titulo)}</h2><div class='vacio'>{esc(vacio_txt)}</div>"
    cuerpo = "".join(tarjeta(a, ver.get(a["id"]), p) for a in items)
    return f"<h2>{esc(titulo)} ({len(items)})</h2>{cuerpo}"


def otro_reporte(nombre):
    """Enlace al reporte del OTRO perfil, para poder saltar de uno al otro.

    Ahora hay dos busquedas y dos avisos: si Eric entra por el push del
    economico, tiene que poder llegar al principal sin volver al celular.
    Las dos paginas viven a la misma profundidad respecto de su propia raiz,
    asi que el salto siempre sube dos niveles y baja al otro.
    """
    otros = [n for n in ORDEN if n != nombre]
    if not otros:
        return ""
    o = PERFILES[otros[0]]
    salida = o["salida"]
    subir = "../.." if PERFILES[nombre]["salida"] else ".."
    destino = f"{subir}/{salida}/latest/" if salida else f"{subir}/latest/"
    return f' · <a href="{esc(destino)}">ver {esc(o["titulo"].lower())} →</a>'


def main():
    ap = argparse.ArgumentParser(description="arma el HTML del reporte de un perfil")
    ap.add_argument("--perfil", choices=ORDEN, default="principal")
    args = ap.parse_args()
    p = cargar_perfil(args.perfil)
    suf = sufijo(args.perfil)
    raiz = PUB / p["salida"] if p["salida"] else PUB

    dia = json.loads((ROOT / f"candidatos_del_dia{suf}.json").read_text(encoding="utf-8"))
    vpath = ROOT / p["veredictos"]
    vd = json.loads(vpath.read_text(encoding="utf-8")) if vpath.exists() else {}

    # Los veredictos vienen de dos lados: la base (lo ya juzgado en dias
    # anteriores) y el veredictos*.json de hoy. Lo de hoy pisa a lo viejo. Asi
    # Claude no tiene que re-emitir 96 opiniones cada dia.
    # Se leen SOLO los de este perfil: el mismo anuncio puede estar juzgado
    # distinto en el otro, porque la vara no es la misma.
    ver = {}
    try:
        cx = sqlite3.connect(ROOT / "estado.db")
        for r_ in cx.execute(
                "SELECT id,clasificacion,razon FROM veredictos WHERE perfil=?",
                (args.perfil,)):
            ver[str(r_[0])] = {"id": str(r_[0]), "clasificacion": r_[1],
                               "razon": r_[2], "apariencia": None}
        cx.close()
    except Exception as e:
        print("no se pudo leer el cache de veredictos:", e)
    for v in vd.get("veredictos", []):
        if v.get("id"):
            ver[str(v["id"])] = v

    incidentes = ""
    ipath = ROOT / "incidente.json"
    if ipath.exists():
        inc = json.loads(ipath.read_text(encoding="utf-8"))
        # Plegado: el detalle de un incidente es largo y no puede tapar los
        # apartamentos, que es a lo que Eric entra. El titular dice lo esencial.
        ok = inc.get("resuelto")
        titular = ("Incidente resuelto solo" if ok
                   else "Incidente: necesito algo tuyo")
        incidentes = f"""<details class="inc"{'' if ok else ' open'}>
<summary><b>{esc(titular)}</b> — {esc(inc.get('fecha',''))}</summary>
<p><b>Qué falló:</b> {esc(inc.get('fallo'))}</p>
<p><b>Qué investigué:</b> {esc(inc.get('investigacion'))}</p>
<p><b>Qué corregí:</b> {esc(inc.get('correccion'))}</p>
<p><b>Pendiente de vos:</b> {esc(inc.get('pendiente') or 'nada')}</p></details>"""

    avisos = (dia.get("salud") or {}).get("avisos") or []
    if avisos:
        incidentes += ('<details class="inc avisos"><summary><b>Avisos de la corrida</b>'
                       f' ({len(avisos)})</summary><ul>'
                       + "".join(f"<li>{esc(w)}</li>" for w in avisos[:8])
                       + "</ul></details>")

    nuevos = dia.get("nuevos", [])
    activos = dia.get("activos", [])
    # El orden lo pone Claude via clasificacion; dentro de cada grupo, precio.
    rank = {"cumple": 0, "revisar": 1, "descartado": 2}
    def k(a):
        v = ver.get(a["id"]) or {}
        return (rank.get(v.get("clasificacion"), 1), a.get("precio") or 0)
    # Tres bloques de verdad: "cumple" es solo lo que cumple. "revisar" NO va
    # aca: va al fondo junto con los descartados, que es donde Eric espera
    # encontrar lo que tiene que decidir el.
    def clas(a):
        return (ver.get(a["id"]) or {}).get("clasificacion", "revisar")
    cumplen = sorted([a for a in activos if clas(a) == "cumple"], key=k)
    fondo = sorted([a for a in activos if clas(a) != "cumple"], key=k)

    s = dia.get("stats", {})
    desap = dia.get("desaparecidos", [])
    tabla = ""
    if desap:
        filas = "".join(
            f"<tr><td>{esc(d.get('titulo'))}</td><td>${esc(d.get('precio'))}</td>"
            f"<td>{esc(d.get('zona'))}</td><td>{esc(d.get('dias_visible'))} días</td></tr>"
            for d in desap[:15])
        tabla = ("<h2>Desaparecidos (probablemente alquilados)</h2>"
                 "<div class='scroll'><table><tr><th>Anuncio</th><th>Precio</th>"
                 f"<th>Zona</th><th>Estuvo visible</th></tr>{filas}</table></div>")

    resumen = vd.get("resumen_dia") or "Sin resumen: el paso de criterio no dejó veredictos."
    fecha = dia.get("fecha", datetime.now().strftime("%Y-%m-%d"))
    titulo_n = f"{len(nuevos)} nuevos hoy" if nuevos else "Sin novedades hoy"
    if dia.get("primera_corrida"):
        titulo_n = "Corrida de siembra: se registró lo que ya existía"

    doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(p['titulo'])} {esc(fecha)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{esc(p['titulo'])} — {esc(fecha)}</h1>
<div class="sub">{esc(titulo_n)} · {s.get('en_zona',0)} en tus zonas de {s.get('total_bajados',0)} revisados ·
{esc(p['vara'])}{otro_reporte(args.perfil)}</div>
{incidentes}
<div class="resumen">{esc(resumen)}</div>
{seccion('Nuevos hoy', nuevos, ver, 'Nada nuevo hoy. El proceso corrió bien.', p)}
{seccion('Activos que cumplen todo', cumplen, ver, 'Ningún activo cumple todo ahora mismo. Mirá el bloque de abajo.', p)}
{seccion('A revisar y descartados', fondo, ver, 'Nada para revisar hoy.', p)}
{tabla}
<footer>Generado por la Raspberry Pi el {esc(datetime.now().strftime('%Y-%m-%d %H:%M'))} (hora de Panamá).
Para que algo deje de aparecer, agregá su ID a <code>descartados.txt</code>.
La decisión final de apariencia es tuya: yo solo opino.</footer>
</div></body></html>"""

    raiz.mkdir(parents=True, exist_ok=True)
    for destino in (raiz / fecha / "index.html", raiz / "latest" / "index.html"):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(doc, encoding="utf-8")

    # El historico se arma sobre la raiz de ESTE perfil. La carpeta del otro
    # perfil (publish/economico/) no empieza con digito, asi que no se cuela
    # en el listado del principal.
    dias = sorted((d.name for d in raiz.iterdir()
                   if d.is_dir() and d.name[0].isdigit()), reverse=True)
    lista = "".join(f'<li><a href="./{d}/">{d}</a></li>' for d in dias[:120])
    (raiz / "index.html").write_text(
        f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(p['titulo'])} — histórico</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{esc(p['titulo'])} en Panamá</h1><div class="sub">Búsqueda diaria automática · {esc(p['vara'])}</div>
<div class="resumen"><a href="./latest/"><b>Ver el reporte más reciente →</b></a></div>
<h2>Histórico</h2><ul>{lista}</ul></div></body></html>""", encoding="utf-8")

    print(f"{args.perfil}: reporte {fecha} | nuevos={len(nuevos)} "
          f"cumplen={len(cumplen)} fondo={len(fondo)} veredictos={len(ver)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
