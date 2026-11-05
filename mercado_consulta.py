#!/usr/bin/env python3
"""
mercado_consulta.py — leer mercado.db sin mentirse.

No recolecta nada: lee lo que junto mercado.py y responde preguntas. Aca vive
todo lo que separa un numero util de uno inventado. Tres sesgos, los tres
MEDIDOS sobre la base real antes de escribir una linea de esto:

1. REPUBLICACION. `PH City Tower $599` aparecio bajo 5 IDs distintos en 8 dias,
   cada uno "muriendo" a los 1-7. No se alquilo cinco veces: es el mismo
   apartamento re-publicado para volver a subir en el listado. Contado crudo
   diria "se coloca en 2 dias" cuando lleva 8+ sin colocarse: la senal se
   INVIERTE. Lo arregla `unidad` (--unidades para verlo).

2. CRAWL INCOMPLETO. El 2026-09-02 registro 26 desaparecidos contra una base de
   2-5, solo porque ese dia bajaron 469 anuncios en vez de 553. Una baja solo
   cuenta si hubo DOS crawls completos seguidos sin ver el anuncio.

3. CENSURA. 28% de los anuncios siguen vivos, varios desde el primer dia: su
   duracion real no se conoce todavia, solo se sabe que es mayor que lo visto.
   Promediar los que murieron da un numero mas chico que la verdad. Se usa
   Kaplan-Meier, que es la forma correcta de contar con datos incompletos.

   Y hay un cuarto efecto, mas sutil, que va en la direccion CONTRARIA:
   empezamos a mirar el 2026-08-25, asi que los apartamentos publicados antes
   que se alquilaron antes de esa fecha nunca los vimos. Los que sobreviven
   hasta que uno empieza a mirar son, por construccion, los mas lentos. Eso se
   llama truncamiento por izquierda y se corrige con entrada diferida: cada
   anuncio entra al conteo recien el dia que lo vimos por primera vez, no el
   dia que se publico.

Sin las cuatro correcciones, los numeros salen igual — solo que mal.
"""
import argparse, sqlite3, sys, unicodedata, re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "mercado.db"

# Cuantos dias puede pasar entre que un anuncio desaparece y reaparece con otro
# id para seguir considerandolo el mismo episodio. Mas que esto ya es un
# alquiler real seguido de una nueva vacancia.
GAP_REPUBLICACION = 7

# Crawls completos seguidos sin ver un anuncio antes de darlo de baja.
CRAWLS_PARA_BAJA = 2

# Piso de precio para venta: el listado de venta trae alquileres colados
# (medido: un "Alquiler de apartamento ... PH Explora" a $2300 entre 40).
PISO_VENTA = 5_000

# Piso de precio para que una huella sea creible. Un alquiler de $1 no es un
# alquiler barato: es un campo que no se pudo parsear. Sin este piso, las 41
# filas con precio < 50 comparten la huella "sin titulo|1.0" y se funden en una
# sola "unidad" de 31 anuncios que no tienen nada que ver entre si. Paso de
# verdad la primera vez que se corrio esto.
PISO_HUELLA = {"alquiler": 50, "venta": PISO_VENTA}

# Titulos que el sitio pone cuando el anunciante no puso ninguno, mas los
# genericos que no identifican nada. Medidos: "Sin Titulo" x26, "Sin titulo" x4,
# "sin titulo" x1 y hasta un "Sin Tiltulo" con el error de tipeo incluido.
# Se incluyen las PLANTILLAS ("alquiler de apartamento", "se alquila casa"),
# no solo los vacios: las comparten decenas de anuncios sin relacion, y son las
# que producian los unicos falsos positivos que sobrevivieron a las otras
# guardas — dos cabanas distintas del mismo agente, dos pisos distintos en
# Carrasquilla. Un titulo asi no identifica un apartamento.
_VERBO = r"(se )?(alquiler|alquilo|alquila|alquilar|venta|vendo|vende|vender|renta|rento)"
_COSA = r"(apartamento|apartamentos|apto|casa|casas|local|habitacion|cuarto|estudio)"
TITULOS_VACIOS = re.compile(
    rf"^(sin t[il]{{1,2}}tulo"
    rf"|casa propia"
    rf"|{_VERBO}"
    rf"|{_COSA}"
    rf"|{_VERBO}( de| en)? {_COSA}"
    rf"|{_COSA} en (alquiler|venta))$")
MIN_LARGO_TITULO = 14

# Cuanto puede cambiar el precio entre dos episodios de la MISMA unidad.
# Hace falta porque la republicacion mas informativa es justo la que baja el
# precio: `PH City Tower` volvio a salir a $599 despues de pedir $649, y una
# huella que incluya el precio exacto la pierde. Medido: sin esto, 1 de los 5
# episodios de esa unidad quedaba suelto y se contaba como un alquiler extra.
TOLERANCIA_PRECIO = 0.25

# Cuanto pueden diferir los m2 de dos episodios de la misma unidad. Los
# anunciantes redondean distinto (86.75 vs 87), pero 70 y 86.75 son otro piso.
TOLERANCIA_M2 = 0.03

# Una huella que matchea a mas de esto NO esta identificando un apartamento:
# esta identificando un precio popular. Medido en venta: 258 anuncios piden
# exactamente $150 000 y 220 piden $145 000, asi que cualquier huella que lleve
# el precio adentro colisiona en masa. Con la clave descartada entera se pierde
# alguna republicacion real; con la clave adentro se funden 80 apartamentos
# distintos en uno. Se prefiere quedarse corto: separar de mas solo infla un
# poco la demanda aparente, fundir de mas destruye el dato.
MAX_POR_HUELLA = 6

# `exactAddress` a veces trae el nombre del edificio ("PH Mystic Village") y a
# veces la direccion administrativa ("Carrasquilla, Panama, Provincia de
# Panama"). Lo segundo identifica un BARRIO, no un edificio: 234 de los 2127
# anuncios de venta que traen el campo son de esa forma, y usarlos como huella
# agrupaba Carrasquilla entera.
_ES_DIRECCION = re.compile(r"\bprovincia\b|\bcorregimiento\b|\bdistrito\b")


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------

def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def d(s):
    """'2026-09-18' o '2026-09-18T19:29:59.000Z' -> date."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def mediana(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def filas(cx, sql, args=()):
    cx.row_factory = sqlite3.Row
    return [dict(r) for r in cx.execute(sql, args)]


# --------------------------------------------------------------------------
# 1. Dias en que se puede confiar
# --------------------------------------------------------------------------

def dias_utiles(cx, operacion):
    """Dias con crawl COMPLETO en todas las categorias de esa operacion.

    Un dia en que una categoria vino a medias no sirve para declarar bajas: los
    anuncios que no aparecieron pueden estar vivos y simplemente no haberse
    bajado. Descartar el dia entero es lo conservador y lo barato.
    """
    por_dia = defaultdict(list)
    for r in filas(cx, "SELECT fecha, categoria, completo, bajados, total_declarado "
                       "FROM crawl WHERE operacion=? ORDER BY fecha", (operacion,)):
        por_dia[r["fecha"]].append(r)
    return sorted(f for f, rs in por_dia.items() if all(r["completo"] for r in rs))


def marcar_bajas(cx, operacion):
    """Escribe ausente_desde / baja_confirmada segun los dias utiles.

    Es DERIVADO: se recalcula entero cada vez. Si manana la regla cambia, se
    vuelve a correr sobre los datos ya guardados y no se pierde nada.
    """
    utiles = dias_utiles(cx, operacion)
    ls = filas(cx, "SELECT id, ultima_vez FROM listing WHERE operacion=?", (operacion,))
    n_baja = n_sospecha = 0
    for l in ls:
        post = [u for u in utiles if u > l["ultima_vez"]]
        ausente = post[0] if post else None
        baja = post[CRAWLS_PARA_BAJA - 1] if len(post) >= CRAWLS_PARA_BAJA else None
        cx.execute("UPDATE listing SET ausente_desde=?, baja_confirmada=? WHERE id=?",
                   (ausente, baja, l["id"]))
        n_baja += baja is not None
        n_sospecha += ausente is not None and baja is None
    cx.commit()
    return {"utiles": len(utiles), "bajas": n_baja, "sospechas": n_sospecha,
            "vivos": len(ls) - n_baja - n_sospecha}


# --------------------------------------------------------------------------
# 2. Unidad fisica: matar la republicacion
# --------------------------------------------------------------------------

def titulo_util(t):
    """Un titulo sirve como huella solo si identifica algo.

    'Sin Titulo' y 'Apartamento' los comparten decenas de anuncios sin ninguna
    relacion: usarlos como huella no agrupa republicaciones, funde el inventario.
    """
    return bool(t) and len(t) >= MIN_LARGO_TITULO and not TITULOS_VACIOS.match(t)


def huellas(l, operacion="alquiler"):
    """Claves por las que dos anuncios pueden ser el mismo apartamento.

    De mas a menos confiable. Todas incluyen recamaras/banos o el precio, para
    que dos apartamentos DISTINTOS del mismo edificio no se fundan en uno.
    Un precio implausible descarta la huella entera: es la senal de que el campo
    no se parseo, y un campo roto agrupa cualquier cosa con cualquier cosa.
    """
    out = []
    rec, ban, pre = l["recamaras"], l["banos"], l["precio_inicial"]
    if not pre or pre < PISO_HUELLA.get(operacion, 0):
        return out
    t = norm(l["titulo"])
    ed = norm(l["edificio"])
    if ed and rec is not None and not _ES_DIRECCION.search(ed):
        out.append(("edificio", f"{ed}|{rec}|{ban}|{pre}"))
    if l["lat"] and l["lon"] and rec is not None:
        out.append(("geo", f"{round(l['lat'], 4)}|{round(l['lon'], 4)}|{rec}|{pre}"))
    if titulo_util(t):
        out.append(("titulo", f"{t}|{pre}"))
        # Sin el precio, para captar la republicacion CON rebaja. Se paga con
        # una guarda al encadenar (ver `compatibles`): dos anuncios de titulo
        # parecido pero precios muy distintos no son el mismo apartamento.
        out.append(("titulo~", t))
        an = norm(l["anunciante_nombre"])
        if an:
            out.append(("anunciante", f"{an}|{t}"))
    return out


# Huellas que NO llevan el precio adentro: se les exige que los precios de los
# dos episodios sean parecidos, o agruparian un estudio de $300 con un 3
# recamaras de $700 que comparten un titulo generico.
SIN_PRECIO = {"titulo~", "anunciante"}


def _choca(a, b, campo):
    """True si los dos lo declaran y NO coinciden. Si uno no lo dice, no opina:
    las filas migradas de estado.db no tienen recamaras y no por eso son otro
    apartamento."""
    va, vb = a[campo], b[campo]
    return va is not None and vb is not None and va != vb


def compatibles(a, b, tipo):
    """Decide si dos anuncios encadenados por una huella son el mismo apartamento.

    gap negativo = estaban publicados a la vez (duplicado simultaneo);
    gap positivo chico = murio y volvio con otro id (republicacion).
    """
    if (d(b["primera_vez"]) - d(a["ultima_vez"])).days > GAP_REPUBLICACION:
        return False
    # La forma del apartamento se exige SIEMPRE: un 1 recamara y un 2 recamaras
    # no son el mismo piso por mucho que compartan titulo y precio. Las huellas
    # `edificio` y `geo` ya lo llevan en la clave; para `titulo` es lo unico que
    # lo separa de fundir dos pisos distintos del mismo anunciante.
    if _choca(a, b, "recamaras") or _choca(a, b, "banos"):
        return False
    # Y los metros, que son lo que de verdad distingue dos pisos del mismo
    # edificio al mismo precio. Sin esto se fundian 80 apartamentos de
    # Carrasquilla de 67, 70, 75 y 86.75 m2 en una sola "unidad".
    ma, mb = a["metros2"], b["metros2"]
    if ma and mb and abs(ma - mb) / max(ma, mb) > TOLERANCIA_M2:
        return False
    if tipo in SIN_PRECIO:
        pa, pb = a["precio_inicial"], b["precio_inicial"]
        if not pa or not pb:
            return False
        if abs(pb - pa) / max(pa, pb) > TOLERANCIA_PRECIO:
            return False
    return True


def derivar_unidades(cx, operacion):
    """Agrupa los ids que son el mismo apartamento fisico. Reconstruye la tabla.

    Dos anuncios se unen si comparten una huella Y sus ventanas de vida se
    solapan o estan a <= GAP_REPUBLICACION dias. El solapamiento es lo que
    distingue "lo re-publicaron para subir en el listado" de "se alquilo y medio
    ano despues se volvio a alquilar", que son cosas opuestas.
    """
    ls = filas(cx, "SELECT * FROM listing WHERE operacion=?", (operacion,))
    padre = {l["id"]: l["id"] for l in ls}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            padre[max(ra, rb)] = min(ra, rb)

    por_clave = defaultdict(list)
    for l in ls:
        for tipo, k in huellas(l, operacion):
            por_clave[(tipo, k)].append(l)

    usadas = {}
    descartadas = 0
    for (tipo, k), grupo in por_clave.items():
        if len(grupo) < 2:
            continue
        if len(grupo) > MAX_POR_HUELLA:
            descartadas += 1
            continue
        grupo.sort(key=lambda x: x["primera_vez"])
        for a, b in zip(grupo, grupo[1:]):
            if compatibles(a, b, tipo):
                unir(a["id"], b["id"])
                usadas[a["id"]] = usadas[b["id"]] = tipo

    cx.executescript("""
      DROP TABLE IF EXISTS unidad;
      CREATE TABLE unidad (unidad_id TEXT, id TEXT PRIMARY KEY, huella TEXT);
      CREATE INDEX ix_unidad ON unidad(unidad_id);
    """)
    cx.executemany("INSERT INTO unidad VALUES (?,?,?)",
                   [(raiz(l["id"]), l["id"], usadas.get(l["id"])) for l in ls])
    cx.commit()

    grupos = defaultdict(list)
    for l in ls:
        grupos[raiz(l["id"])].append(l)
    multi = {k: v for k, v in grupos.items() if len(v) > 1}

    # Dos fenomenos distintos, y conviene no sumarlos:
    #   secuencial  el anuncio muere y reaparece con otro id -> republicacion
    #               para volver a subir en el listado. Inventa alquileres.
    #   simultaneo  varios ids vivos a la vez -> el mismo piso publicado por
    #               tres agentes, o tres pisos iguales del mismo edificio.
    #               Infla el inventario, no la demanda.
    sec = sim = 0
    for v in multi.values():
        v = sorted(v, key=lambda x: x["primera_vez"])
        solapa = any(b["primera_vez"] <= a["ultima_vez"] for a, b in zip(v, v[1:]))
        sim += solapa
        sec += not solapa
    return {"anuncios": len(ls), "unidades": len(grupos),
            "con_republicacion": len(multi),
            "secuenciales": sec, "simultaneas": sim,
            "huellas_descartadas": descartadas,
            "ids_absorbidos": sum(len(v) - 1 for v in multi.values())}


def una_fila_por_unidad(cx, operacion, extra_where="", args=()):
    """Un anuncio por apartamento FISICO, para que las medianas no se ponderen mal.

    Medido en venta: el 18.8% de los anuncios son el mismo piso publicado por
    varias inmobiliarias (8 anuncios de "Carrasquilla $150 000, 81.93 m2, PH
    Coral towers" entre tres agencias). Contados como apartamentos distintos,
    la mediana se corre hacia los pisos que mas agencias publican, que no son
    una muestra al azar del mercado.
    """
    return filas(cx, f"""
        SELECT l.* FROM listing l
        JOIN (SELECT unidad_id, MIN(id) AS id FROM unidad GROUP BY unidad_id) r
          ON r.id = l.id
        WHERE l.operacion = ? AND l.precio_actual >= ? {extra_where}
    """, (operacion, PISO_HUELLA.get(operacion, 0)) + args)


def unidades_resumen(cx, operacion):
    """Una fila por unidad fisica: sus atributos y su vida COSIDA entre episodios.

    Excluye los precios implausibles. No es cosmetico: 15 de los 17 anuncios que
    la base tenia en Costa del Este eran filas con precio $1 y titulo vacio, y
    todas "se dieron de baja" el mismo dia. Sin este filtro, esa zona reportaba
    una mediana de 10 dias calculada casi enteramente sobre basura.
    """
    return filas(cx, """
        SELECT u.unidad_id,
               COUNT(*)                        AS episodios,
               MIN(l.publicado)                AS publicado,
               MIN(l.primera_vez)              AS primera_vez,
               MAX(l.ultima_vez)               AS ultima_vez,
               -- La unidad esta de baja solo si su ULTIMO episodio lo esta.
               CASE WHEN MAX(CASE WHEN l.baja_confirmada IS NULL THEN 1 ELSE 0 END) = 1
                    THEN NULL ELSE MAX(l.baja_confirmada) END AS baja_confirmada,
               CASE WHEN MAX(CASE WHEN l.baja_confirmada IS NULL THEN 1 ELSE 0 END) = 1
                    THEN NULL ELSE MAX(l.ausente_desde) END AS ausente_desde,
               MIN(l.precio_inicial)           AS precio_min,
               MAX(l.precio_inicial)           AS precio_max,
               MAX(l.zona)                     AS zona,
               MAX(l.distrito)                 AS distrito,
               MAX(l.corregimiento)            AS corregimiento,
               MAX(l.recamaras)                AS recamaras,
               MAX(l.banos)                    AS banos,
               MAX(l.estacionamientos)         AS estacionamientos,
               MAX(l.metros2)                  AS metros2,
               MAX(l.amoblado)                 AS amoblado,
               MAX(l.anunciante)               AS anunciante,
               MAX(l.anunciante_nombre)        AS anunciante_nombre,
               MAX(l.edificio)                 AS edificio,
               MAX(l.tier_max)                 AS tier_max,
               MAX(l.titulo)                   AS titulo,
               MAX(l.categoria)                AS categoria,
               MIN(l.origen)                   AS origen
        FROM unidad u JOIN listing l ON l.id = u.id
        WHERE l.operacion = ? AND l.precio_inicial >= ?
        GROUP BY u.unidad_id
    """, (operacion, PISO_HUELLA.get(operacion, 0)))


# --------------------------------------------------------------------------
# 3. Kaplan-Meier con entrada diferida
# --------------------------------------------------------------------------

def km(sujetos):
    """Kaplan-Meier con truncamiento por izquierda. stdlib pura.

    sujetos: lista de (entrada, salida, evento). Los tres en dias desde que el
    anuncio se PUBLICO (dateCreated del sitio), no desde que lo vimos.
      entrada  dias que ya llevaba publicado cuando lo vimos por primera vez
      salida   dias hasta que se dio de baja, o hasta hoy si sigue vivo
      evento   1 = se dio de baja (probablemente se alquilo), 0 = censurado

    `entrada` es lo que corrige el sesgo de haber empezado a mirar el 2026-08-25:
    un anuncio que ya llevaba 40 dias publicado no puede contarse como si
    hubiera estado en riesgo desde el dia 0, porque en esos 40 dias no lo
    habriamos visto alquilarse aunque hubiera pasado.

    Devuelve (curva, mediana, n, eventos).
    """
    sujetos = [s for s in sujetos if s[1] is not None and s[1] >= s[0]]
    if not sujetos:
        return [], None, 0, 0
    tiempos = sorted({s[1] for s in sujetos if s[2]})
    curva, S = [], 1.0
    for t in tiempos:
        # En riesgo: ya habia entrado y todavia no habia salido.
        n = sum(1 for e, x, _ in sujetos if e < t <= x)
        dd = sum(1 for e, x, ev in sujetos if ev and x == t)
        if n:
            S *= (1 - dd / n)
            curva.append((t, S, n, dd))
    med = next((t for t, s, _, _ in curva if s <= 0.5), None)
    return curva, med, len(sujetos), sum(1 for s in sujetos if s[2])


def sujetos_km(unidades, hasta):
    """Convierte unidades en sujetos de supervivencia. hasta = ultimo dia util.

    El momento del EVENTO es `ausente_desde` (el primer dia que no lo vimos), no
    `baja_confirmada` (que es dos crawls despues). `baja_confirmada` decide SI
    cuenta como baja; `ausente_desde` dice CUANDO paso. Usar la confirmacion
    como fecha le sumaria dos dias a cada duracion — parejo en todos los grupos,
    asi que las comparaciones aguantarian, pero las medianas absolutas no.
    """
    out = []
    for u in unidades:
        pub = d(u["publicado"]) or d(u["primera_vez"])
        prim = d(u["primera_vez"])
        if not pub or not prim:
            continue
        fin = (d(u["ausente_desde"]) or d(u["baja_confirmada"])) if u["baja_confirmada"] else hasta
        if not fin:
            continue
        entrada = max((prim - pub).days, 0)
        salida = (fin - pub).days
        out.append((entrada, salida, 1 if u["baja_confirmada"] else 0))
    return out


# --------------------------------------------------------------------------
# cortes
# --------------------------------------------------------------------------

def tramo_precio(p):
    if p is None:
        return "?"
    for hi in (300, 400, 500, 600, 700, 750):
        if p <= hi:
            return f"<=${hi}"
    return ">$750"


# Avisos por corte: hay cortes que miden algo distinto de lo que parece, y el
# numero sale igual de limpio en los dos casos. Van impresos con el resultado,
# no en la documentacion, porque nadie lee la documentacion al mirar una tabla.
CAVEATS = {
    "tier": "El tier es PUBLICIDAD PAGA. Un platinum se ve mas, asi que se coloca\n"
            "antes por visibilidad comprada, no porque el apartamento sea mejor.\n"
            "Esto NO dice que convenga pagar destaque: los que pagan son tambien\n"
            "los que mas apuro tienen y mejor precio ponen.",
    "anunciante": "Los agentes republican mas que los duenios, asi que sin la correccion\n"
                  "de `unidad` este corte los haria ver artificialmente rapidos.\n"
                  "Mira `unidades` si el numero te sorprende.",
    "amoblado": "`amoblado` viene en el 15% de los anuncios. 'sin amoblar/no dice' es\n"
                "sobre todo 'no dice': el grueso del corte es ausencia de dato.",
    "precio": "El precio es lo que PIDEN. Un anuncio que bajo de $750 a $650 y recien\n"
              "ahi se coloco figura en el tramo de $650. Ver `descuentos`.",
}

CORTES = {
    "zona":         lambda u: u["zona"] or "(fuera de zona)",
    "distrito":     lambda u: u["distrito"] or "?",
    "corregimiento": lambda u: u["corregimiento"] or "?",
    "recamaras":    lambda u: f"{u['recamaras']:.0f} rec" if u["recamaras"] is not None else "?",
    "banos":        lambda u: f"{u['banos']:g} ban" if u["banos"] is not None else "?",
    "precio":       lambda u: tramo_precio(u["precio_min"]),
    "amoblado":     lambda u: "amoblado" if u["amoblado"] else "sin amoblar/no dice",
    "anunciante":   lambda u: u["anunciante"] or "?",
    "tier":         lambda u: u["tier_max"] or "sin destacar",
    "categoria":    lambda u: (u["categoria"] or "?").replace("bienes-raices-", ""),
    "todo":         lambda u: "todo",
}


# --------------------------------------------------------------------------
# comandos
# --------------------------------------------------------------------------

def preparar(cx, operacion):
    """Recalcula lo derivado. Barato y siempre antes de cualquier numero."""
    b = marcar_bajas(cx, operacion)
    u = derivar_unidades(cx, operacion)
    return b, u


def cmd_calidad(cx, args):
    print("=== CALIDAD DE LOS DATOS ===")
    print("Ningun numero de los otros comandos significa nada sin esto.\n")

    for op in ("alquiler", "venta"):
        n = cx.execute("SELECT COUNT(*) FROM listing WHERE operacion=?", (op,)).fetchone()[0]
        if not n:
            continue
        print(f"--- {op} ---")
        dias = filas(cx, "SELECT fecha, MIN(completo) AS ok, SUM(bajados) AS bajados, "
                         "SUM(total_declarado) AS declarado FROM crawl WHERE operacion=? "
                         "GROUP BY fecha ORDER BY fecha", (op,))
        utiles = [x for x in dias if x["ok"]]
        print(f"  dias con crawl        : {len(dias)}")
        print(f"  dias UTILES (completos): {len(utiles)}")
        for x in dias:
            if not x["ok"]:
                decl = x["declarado"] if x["declarado"] else "?"
                print(f"    x {x['fecha']}: incompleto "
                      f"({x['bajados']} de {decl} declarados) — NO cuenta bajas")
        if dias:
            # Huecos: dias de calendario sin ninguna fila de crawl.
            ini, fin = d(dias[0]["fecha"]), d(dias[-1]["fecha"])
            tengo = {x["fecha"] for x in dias}
            faltan = [(ini + timedelta(days=i)).isoformat()
                      for i in range((fin - ini).days + 1)
                      if (ini + timedelta(days=i)).isoformat() not in tengo]
            if faltan:
                print(f"  dias SIN crawl        : {len(faltan)} -> {', '.join(faltan[:6])}"
                      + (" ..." if len(faltan) > 6 else ""))
            print(f"  ventana               : {ini} .. {fin} ({(fin - ini).days + 1} dias)")

        b, u = preparar(cx, op)
        print(f"  anuncios              : {b['vivos'] + b['bajas'] + b['sospechas']}")
        print(f"    bajas confirmadas   : {b['bajas']}")
        print(f"    ausentes sin confirmar: {b['sospechas']}  (faltan crawls utiles)")
        print(f"    vivos (censurados)  : {b['vivos']}")
        print(f"  unidades fisicas      : {u['unidades']} "
              f"({u['ids_absorbidos']} ids absorbidos en {u['con_republicacion']} unidades)")
        print(f"    republicacion secuencial : {u['secuenciales']}  "
              f"(inventa bajas: el anuncio muere y vuelve con otro id)")
        print(f"    duplicado simultaneo     : {u['simultaneas']}  "
              f"(infla el inventario: varios ids vivos a la vez)")
        if u["huellas_descartadas"]:
            print(f"    huellas descartadas por masivas: {u['huellas_descartadas']} "
                  f"(>{MAX_POR_HUELLA} anuncios; identifican un precio, no un piso)")
        if u["anuncios"]:
            pct = 100 * u["ids_absorbidos"] / u["anuncios"]
            verbo = "alquileres" if op == "alquiler" else "ventas"
            print(f"    -> {pct:.1f}% de los ids eran el mismo apartamento. "
                  f"Contarlos como {verbo} distintos inflaria la demanda {pct:.0f}%.")
        if op == "venta":
            mal = cx.execute("SELECT COUNT(*) FROM listing WHERE operacion='venta' "
                             "AND precio_actual < ?", (PISO_VENTA,)).fetchone()[0]
            print(f"  alquileres colados en venta: {mal} (se excluyen con piso ${PISO_VENTA:,})")
        print()

    print("Recorda: 'se dio de baja' NO es 'se alquilo'. Tambien se da de baja")
    print("el que se cansa y retira el anuncio, y el que se le vence la")
    print("publicacion. Es el mejor proxy disponible, no un hecho.")


def cmd_dias_mercado(cx, args):
    op = args.operacion
    preparar(cx, op)
    utiles = dias_utiles(cx, op)
    if not utiles:
        print("no hay ni un dia de crawl completo todavia")
        return
    hasta = d(utiles[-1])
    us = unidades_resumen(cx, op)
    if args.zona:
        us = [u for u in us if u["zona"] == args.zona]
    if args.solo_en_zona:
        us = [u for u in us if u["zona"]]
    if args.desde:
        us = [u for u in us if u["primera_vez"] >= args.desde]

    corte = CORTES[args.por]
    grupos = defaultdict(list)
    for u in us:
        grupos[corte(u)].append(u)

    print(f"=== DIAS HASTA DARSE DE BAJA — por {args.por} ({op}) ===")
    print("Mediana Kaplan-Meier desde la fecha de PUBLICACION del anuncio,")
    print("con entrada diferida y los vivos tratados como censurados.")
    print(f"Ventana observada: {utiles[0]} .. {utiles[-1]} ({len(utiles)} dias utiles)\n")
    if args.por in CAVEATS:
        print("OJO: " + CAVEATS[args.por] + "\n")
    print(f"{'grupo':<28} {'mediana':>9} {'n':>5} {'bajas':>6} {'vivos':>6}  nota")

    filas_out = []
    for k, g in sorted(grupos.items(), key=lambda x: -len(x[1])):
        s = sujetos_km(g, hasta)
        _, med, n, ev = km(s)
        filas_out.append((k, med, n, ev, n - ev))
    for k, med, n, ev, cens in filas_out:
        nota = ""
        if n < 20:
            nota = "n chico, no concluir"
        if med is None:
            nota = (nota + "; " if nota else "") + "mas de la mitad sigue viva"
        m = f"{med:.0f} d" if med is not None else "—"
        print(f"{k:<28} {m:>9} {n:>5} {ev:>6} {cens:>6}  {nota}")

    viejos = sum(1 for u in us if u["origen"] == "estado")
    if viejos and args.por not in ("zona", "todo", "precio"):
        print(f"\nAVISO: {viejos} de {len(us)} unidades vienen migradas de estado.db, que")
        print(f"guardaba 9 campos. No tienen '{args.por}', asi que caen en '?' — y son")
        print("justo las que ya se dieron de baja. Cruzar duracion contra atributos")
        print("recien sirve con lo que junte mercado.py de aca en adelante.")
    if len(utiles) < 60:
        print(f"\nAVISO: {len(utiles)} dias de observacion. Toda mediana mayor a ~"
              f"{len(utiles)} dias esta adivinada por la cola de la curva.")


def cmd_precio_m2(cx, args):
    op = args.operacion
    piso = PISO_HUELLA.get(op, 0)
    # Se traen TODAS las columnas que puede pedir un corte. Antes se armaba un
    # dict a mano con la mitad en None, asi que `--por amoblado` o `--por tier`
    # devolvian un solo grupo y parecian una respuesta.
    preparar(cx, op)
    rs = una_fila_por_unidad(cx, op, "AND l.metros2 > 5")
    for r in rs:
        r["precio_min"] = r["precio_actual"]
    corte = CORTES[args.por]
    g = defaultdict(list)
    for r in rs:
        g[corte(r)].append(r)

    print(f"=== PRECIO POR M2 — {op}, por {args.por} ===")
    print("Precio PEDIDO, no de cierre. m2 viene en el 60% de los anuncios de")
    print("alquiler y en el 100% de los de venta: el resto queda afuera.\n")
    print(f"{'grupo':<28} {'$/m2':>9} {'precio med':>12} {'m2 med':>8} {'n':>5}")
    for k, rows in sorted(g.items(), key=lambda x: -len(x[1])):
        pm2 = mediana([r["precio_actual"] / r["metros2"] for r in rows])
        nota = "  <- n chico" if len(rows) < 10 else ""
        print(f"{k:<28} {pm2:>9.2f} {mediana([r['precio_actual'] for r in rows]):>12,.0f} "
              f"{mediana([r['metros2'] for r in rows]):>8.0f} {len(rows):>5}{nota}")


def cmd_rentabilidad(cx, args):
    """Rentabilidad bruta anual, normalizada POR METRO CUADRADO.

    Por que por m2 y no dividiendo las medianas directas: el alquiler mediano y
    la venta mediana son apartamentos de TAMANOS DISTINTOS. En Betania el
    alquiler mediano es de 78 m2; si la venta mediana es de 45 m2, dividir uno
    por otro no compara el mismo producto y da una rentabilidad inventada (daba
    11.7%). Normalizando por m2 los dos lados, se comparan cosas comparables.

    La cuota de PH se toma del lado de VENTA a proposito: ahi viene en el 67% de
    los anuncios contra el 8% en alquiler, y ademas es un costo del PROPIETARIO,
    que es quien la paga si compra para alquilar.
    """
    def por_zona(op, piso):
        preparar(cx, op)
        rs = una_fila_por_unidad(cx, op, "AND l.metros2 > 5")
        g = defaultdict(list)
        for r in rs:
            k = r["zona"] or r["corregimiento"]
            if k and (not args.zona or k == args.zona):
                g[k].append(r)
        return g

    alq = por_zona("alquiler", PISO_HUELLA["alquiler"])
    ven = por_zona("venta", PISO_HUELLA["venta"])
    comunes = sorted(set(alq) & set(ven))

    print("=== RENTABILIDAD BRUTA ANUAL (estimada, por m2) ===")
    print("bruta = alquiler/m2 x 12 / venta/m2.   neta PH = le resta la cuota de PH.")
    print("Un anuncio por apartamento fisico: el 18.8% de las ventas son el mismo")
    print("piso publicado por varias inmobiliarias y contarlos corre la mediana.")
    print("Precios PEDIDOS, no de cierre: el portal no publica el cierre.")
    print("NO descuenta impuesto de inmueble, vacancia, reparaciones ni gestion,")
    print("asi que lo que Eric se llevaria al bolsillo es bastante menos que esto.\n")
    if not comunes:
        print("Todavia no hay ninguna zona con datos de alquiler Y de venta a la vez.")
        print("Hace falta una corrida completa de venta.py (155 pags, domingos 9:00).")
        return

    print(f"{'zona':<24} {'alq/m2':>7} {'ven/m2':>8} {'PH/m2':>6} {'bruta':>7} "
          f"{'netaPH':>7} {'m2 alq':>7} {'m2 ven':>7} {'n alq':>6} {'n ven':>6}")
    out = []
    for z in comunes:
        a, v = alq[z], ven[z]
        ra = mediana([r["precio_actual"] / r["metros2"] for r in a])
        rv = mediana([r["precio_actual"] / r["metros2"] for r in v])
        ph = mediana([r["mantenimiento"] / r["metros2"] for r in v if r["mantenimiento"]]) or 0
        if not rv:
            continue
        out.append((ra * 12 / rv * 100, z, ra, rv, ph,
                    (ra - ph) * 12 / rv * 100,
                    mediana([r["metros2"] for r in a]),
                    mediana([r["metros2"] for r in v]), len(a), len(v)))
    for bruta, z, ra, rv, ph, neta, m2a, m2v, na, nv in sorted(out, reverse=True):
        aviso = "  <- n chico" if min(na, nv) < 10 else ""
        print(f"{z[:24]:<24} {ra:>7.2f} {rv:>8,.0f} {ph:>6.2f} {bruta:>6.1f}% "
              f"{neta:>6.1f}% {m2a:>7.0f} {m2v:>7.0f} {na:>6} {nv:>6}{aviso}")

    print("\nComo leerlo: 'bruta 9%' quiere decir que un apartamento comprado al")
    print("precio pedido de esa zona rentaria 9% anual antes de TODOS los gastos.")
    print("Si los m2 medianos de los dos lados son muy distintos, la zona esta")
    print("comparando productos distintos aunque se haya normalizado: mirá esa columna.")


def cmd_inventario(cx, args):
    op = args.operacion
    print(f"=== INVENTARIO Y ABSORCION — {op} ===")
    print("activos = anuncios vistos ese dia. bajas = confirmadas ese dia.")
    print("absorcion = bajas / activos: cuanto del inventario se vacia por dia.\n")
    preparar(cx, op)
    where = "AND l.zona = ?" if args.zona else ""
    a = (args.zona,) if args.zona else ()
    piso = PISO_HUELLA.get(op, 0)
    act = filas(cx, f"""SELECT o.fecha, COUNT(*) n FROM observacion o
                        JOIN listing l ON l.id=o.id
                        WHERE l.operacion=? AND l.precio_inicial>=? {where}
                        GROUP BY o.fecha ORDER BY o.fecha""", (op, piso) + a)
    baj = dict(cx.execute(f"""SELECT l.baja_confirmada, COUNT(*) FROM listing l
                              WHERE l.operacion=? AND l.precio_inicial>=?
                                AND l.baja_confirmada IS NOT NULL {where}
                              GROUP BY 1""", (op, piso) + a).fetchall())
    print(f"{'fecha':<12} {'activos':>8} {'bajas':>7} {'absorcion':>10}")
    for r in act:
        b = baj.get(r["fecha"], 0)
        print(f"{r['fecha']:<12} {r['n']:>8} {b:>7} {100*b/r['n']:>9.1f}%")


def cmd_descuentos(cx, args):
    """Cuanto bajo el precio antes de desaparecer: la pista mas cercana al cierre."""
    op = args.operacion
    preparar(cx, op)
    rs = filas(cx, """SELECT id, titulo, zona, corregimiento, precio_inicial,
                             precio_actual, precio_min, primera_vez, baja_confirmada
                      FROM listing
                      WHERE operacion=? AND precio_inicial >= ?
                        AND precio_min < precio_inicial""",
                   (op, PISO_HUELLA.get(op, 0)))
    print("=== BAJADAS DE PRECIO ===")
    print("El portal solo publica el precio PEDIDO. Un anuncio que bajo el precio")
    print("y recien despues desaparecio es lo mas cerca que se puede llegar al")
    print("precio de cierre real sin el Registro Publico.\n")
    if not rs:
        print("Ningun anuncio bajo de precio todavia (hacen falta mas dias).")
        return
    rs.sort(key=lambda r: (r["precio_min"] - r["precio_inicial"]) / r["precio_inicial"])
    baj = [r for r in rs if r["baja_confirmada"]]
    pct = [100 * (r["precio_min"] - r["precio_inicial"]) / r["precio_inicial"] for r in rs]
    print(f"con bajada: {len(rs)}   de esos, dados de baja despues: {len(baj)}")
    print(f"bajada mediana: {mediana(pct):.1f}%\n")
    print(f"{'id':<11} {'de':>8} {'a':>8} {'%':>7} {'zona':<18} titulo")
    for r in rs[:args.limite]:
        p = 100 * (r["precio_min"] - r["precio_inicial"]) / r["precio_inicial"]
        z = r["zona"] or r["corregimiento"] or "?"
        print(f"{r['id']:<11} {r['precio_inicial']:>8,.0f} {r['precio_min']:>8,.0f} "
              f"{p:>6.1f}% {z[:18]:<18} {(r['titulo'] or '')[:44]}")


def cmd_rotacion(cx, args):
    op = args.operacion
    preparar(cx, op)
    rs = filas(cx, """SELECT l.edificio, l.zona, l.corregimiento,
                             COUNT(DISTINCT u.unidad_id) unidades,
                             COUNT(*) anuncios,
                             SUM(CASE WHEN l.baja_confirmada IS NOT NULL THEN 1 ELSE 0 END) bajas,
                             AVG(l.precio_inicial) precio
                      FROM listing l JOIN unidad u ON u.id=l.id
                      WHERE l.operacion=? AND l.edificio IS NOT NULL
                        AND l.precio_inicial >= ?
                      GROUP BY LOWER(l.edificio) HAVING unidades >= ?
                      ORDER BY unidades DESC""",
                   (op, PISO_HUELLA.get(op, 0), args.min_unidades))
    print("=== ROTACION POR EDIFICIO ===")
    print("Unidades FISICAS distintas publicadas del mismo edificio (ya sin")
    print("republicaciones). Mucha rotacion = se coloca rapido, pero tambien")
    print("puede ser senal de inquilinos que no se quedan.\n")
    print(f"{'edificio':<42} {'unid':>5} {'anun':>5} {'bajas':>6} {'precio':>8} zona")
    for r in rs[:args.limite]:
        z = r["zona"] or r["corregimiento"] or "?"
        print(f"{(r['edificio'] or '')[:42]:<42} {r['unidades']:>5} {r['anuncios']:>5} "
              f"{r['bajas']:>6} {r['precio']:>8,.0f} {z}")


def cmd_unidades(cx, args):
    """Muestra las republicaciones detectadas. Es la verificacion a ojo."""
    op = args.operacion
    preparar(cx, op)
    rs = filas(cx, """SELECT u.unidad_id, u.id, u.huella, l.titulo, l.precio_inicial,
                             l.primera_vez, l.ultima_vez, l.baja_confirmada, l.zona
                      FROM unidad u JOIN listing l ON l.id=u.id
                      WHERE l.operacion=?
                      ORDER BY u.unidad_id, l.primera_vez""", (op,))
    g = defaultdict(list)
    for r in rs:
        g[r["unidad_id"]].append(r)
    multi = {k: v for k, v in g.items() if len(v) > 1}
    print(f"=== UNIDADES CON REPUBLICACION ({len(multi)} de {len(g)}) ===\n")
    orden = sorted(multi.items(), key=lambda x: -len(x[1]))
    for uid, eps in orden[:args.limite]:
        e0 = eps[0]
        print(f"unidad {uid} — {len(eps)} anuncios — ${e0['precio_inicial']:,.0f} "
              f"— {e0['zona'] or '(fuera de zona)'} — huella={e0['huella']}")
        print(f"  {(e0['titulo'] or '')[:76]}")
        for e in eps:
            estado = f"baja {e['baja_confirmada']}" if e["baja_confirmada"] else "VIVO"
            print(f"    {e['id']}  {e['primera_vez']} -> {e['ultima_vez']}  {estado}")
        print()


COMANDOS = {
    "calidad": cmd_calidad, "dias-mercado": cmd_dias_mercado,
    "precio-m2": cmd_precio_m2, "rentabilidad": cmd_rentabilidad,
    "inventario": cmd_inventario, "descuentos": cmd_descuentos,
    "rotacion-edificio": cmd_rotacion, "unidades": cmd_unidades,
}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EMPEZA SIEMPRE POR ACA:
  %(prog)s calidad
      dias de crawl rotos, cuanto esta censurado, cuantas republicaciones.
      Ningun numero de los otros comandos significa nada sin esto.

CUANTO TARDA EN COLOCARSE (la pregunta original):
  %(prog)s dias-mercado --por zona --solo-en-zona
  %(prog)s dias-mercado --por recamaras
  %(prog)s dias-mercado --por precio
  %(prog)s dias-mercado --por anunciante     duenio vs agente
  %(prog)s dias-mercado --por recamaras --desde 2026-09-18
      dentro de unos meses: cortes por atributo sin lo migrado de estado.db
  %(prog)s dias-mercado --por tier           OJO: mide publicidad, no el apartamento

CUANTO CUESTA:
  %(prog)s precio-m2 --por corregimiento
  %(prog)s precio-m2 --operacion venta --por corregimiento
  %(prog)s rentabilidad                      alquiler vs venta, por zona

COMO SE MUEVE EL MERCADO:
  %(prog)s inventario --zona "Juan Diaz"     stock y tasa de absorcion por dia
  %(prog)s descuentos                        cuanto bajaron antes de desaparecer
  %(prog)s rotacion-edificio                 que PH publica mas unidades

VERIFICAR QUE NO MIENTE:
  %(prog)s unidades                          las republicaciones detectadas, a ojo
  python3 test_mercado.py                    pruebas con respuesta conocida
""")
    ap.add_argument("comando", choices=list(COMANDOS))
    ap.add_argument("--operacion", default="alquiler", choices=["alquiler", "venta"])
    ap.add_argument("--por", default="zona", choices=list(CORTES))
    ap.add_argument("--zona")
    ap.add_argument("--solo-en-zona", action="store_true",
                    help="solo las 9 zonas de Eric")
    ap.add_argument("--desde", metavar="YYYY-MM-DD",
                    help="solo anuncios vistos por primera vez desde esa fecha. "
                         "Sirve para dejar afuera lo migrado de estado.db, que "
                         "tiene la historia pero no los atributos")
    ap.add_argument("--limite", type=int, default=25)
    ap.add_argument("--min-unidades", type=int, default=2)
    args = ap.parse_args()

    if not DB.exists():
        print(f"no existe {DB.name}; corre mercado.py primero")
        return 2
    cx = sqlite3.connect(DB)
    try:
        COMANDOS[args.comando](cx, args)
    finally:
        cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
