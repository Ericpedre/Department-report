#!/usr/bin/env python3
"""
state.py — memoria del proceso. Tambien MECANICO: no opina.

Corre en DOS pasadas, y el orden importa:

  state.py --pasada-comun     UNA vez por corrida. Etiqueta zonas, actualiza la
                              base, calcula que desaparecio. Escribe dia_comun.json.
  state.py --perfil <nombre>  Una vez por perfil. Recorta dia_comun.json a lo que
                              le toca a ese perfil y arma sus dos archivos.

Por que partido: los desaparecidos se calculan comparando la base contra lo que
se vio HOY. Si cada perfil hiciera esa cuenta con su propio subconjunto, el
perfil economico marcaria como desaparecidos a todos los apartamentos de 2
recamaras del principal, y viceversa. La cuenta de desaparecidos tiene que
verlos a todos, una sola vez.

Sale un candidatos_del_dia*.json por perfil (completo, para render.py) y un
para_criterio*.json (recortado, es lo unico que ve Claude).
"""
import argparse, json, re, sqlite3, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path

from perfiles import ORDEN, perfil as cargar_perfil, sufijo

ROOT = Path(__file__).resolve().parent
DB = ROOT / "estado.db"
COMUN = ROOT / "dia_comun.json"

# Orden de PREFERENCIA PERSONAL de Eric. NO es un ranking de distancia a su
# trabajo. Ciudad Radial va ultima aunque su oficina quede ahi: es a proposito.
# No reordenar por conveniencia geografica.
#
# Son las MISMAS para los dos perfiles: Eric quiere el plan B barato en las
# mismas zonas, no en cualquier lado. Por eso viven aca y no en perfiles.py.
ZONAS = [
    "Versalles", "Juan Díaz", "Don Bosco", "San Antonio", "Brisas del Golf",
    "Parque Lefevre", "Costa del Este", "24 de Diciembre", "Ciudad Radial",
]

# Exclusion por LISTA NEGRA EXPLICITA, nunca por "no dice Ciudad de Panama".
# Motivo medido: San Antonio y Brisas del Golf reportan su locality como
# "Panama Provincia, SAN MIGUELITO, Rufina Alfaro, ...". Filtrar exigiendo
# "Ciudad de Panama" borraria dos zonas prioritarias de Eric.
EXCLUIDAS = [
    "Panamá Oeste", "Arraiján", "La Chorrera", "Chorrera", "Colón",
    "Panamá Pacifico", "Panamá Pacífico", "Coclé", "Veraguas", "Chiriquí",
    "Herrera", "Los Santos", "Bocas del Toro", "Darién", "Antón", "Río Hato",
]


def norm(s):
    """Sin acentos y en minusculas, para comparar 'Juan Díaz' con 'Juan Diaz'."""
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def clasificar_zona(locality):
    """Devuelve (zona, prioridad) o (None, None). Puro match de texto."""
    n = norm(locality)
    if any(norm(e) in n for e in EXCLUIDAS):
        return None, None
    # Una locality puede nombrar VARIAS zonas: "Juan Diaz, Urbanizacion Don
    # Bosco" contiene Juan Diaz (#2) y Don Bosco (#3). Se guarda la etiqueta
    # MAS ESPECIFICA (la que aparece mas al fondo de la jerarquia) pero se
    # ordena por la MEJOR prioridad de Eric. Si solo tomaramos la primera,
    # Don Bosco desapareceria absorbido por Juan Diaz.
    hits = [(i, z, n.rfind(norm(z))) for i, z in enumerate(ZONAS) if norm(z) in n]
    if not hits:
        return None, None
    etiqueta = max(hits, key=lambda h: h[2])[1]   # la mas especifica
    prioridad = min(h[0] for h in hits)           # la mas preferida
    return etiqueta, prioridad


def num(v):
    """Defensa: si el sitio cambia el tipo de un campo, no se rompe el orden."""
    if isinstance(v, bool) or v is None:
        return 0
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return 0


def cargar_descartados():
    """descartados.txt: un ID o URL por linea; '#' comenta. Editable por Eric.

    Es COMPARTIDO entre perfiles a proposito: un anuncio que Eric mato, murio
    para las dos busquedas. No tiene sentido que reaparezca en la otra.
    """
    f = ROOT / "descartados.txt"
    if not f.exists():
        return {}
    out = {}
    for linea in f.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = re.split(r"\s+", linea, maxsplit=1)
        m = re.search(r"(\d{6,})", partes[0])
        if m:
            out[m.group(1)] = partes[1].strip() if len(partes) > 1 else "descartado por Eric"
    return out


def _migrar_veredictos(cx):
    """La tabla de veredictos pasa de clave `id` a clave `(id, perfil)`.

    Hace falta porque un mismo anuncio puede caer en los DOS perfiles (un 2 rec
    a $340 califica en ambos) y con la clave vieja el veredicto de un perfil
    pisaba el del otro. SQLite no deja cambiar una PRIMARY KEY con ALTER, asi
    que es tabla nueva + copia + rename. Lo ya juzgado se hereda como
    'principal', que es lo unico que existia hasta ahora.
    """
    cols = [r[1] for r in cx.execute("PRAGMA table_info(veredictos)")]
    if not cols:
        cx.execute("""CREATE TABLE veredictos (
            id TEXT, perfil TEXT, fecha TEXT, clasificacion TEXT, razon TEXT,
            PRIMARY KEY (id, perfil))""")
        return
    if "perfil" in cols:
        return
    n = cx.execute("SELECT COUNT(*) FROM veredictos").fetchone()[0]
    cx.executescript("""
        CREATE TABLE veredictos_nueva (
            id TEXT, perfil TEXT, fecha TEXT, clasificacion TEXT, razon TEXT,
            PRIMARY KEY (id, perfil));
        INSERT OR REPLACE INTO veredictos_nueva (id,perfil,fecha,clasificacion,razon)
            SELECT id,'principal',fecha,clasificacion,razon FROM veredictos;
        DROP TABLE veredictos;
        ALTER TABLE veredictos_nueva RENAME TO veredictos;
    """)
    m = cx.execute("SELECT COUNT(*) FROM veredictos").fetchone()[0]
    print(f"  migracion: veredictos ahora tiene clave (id,perfil); {n} -> {m} filas")


def init_db(cx):
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS anuncios (
        id TEXT PRIMARY KEY,
        primera_vez TEXT, ultima_vez TEXT, desaparecido TEXT,
        precio_inicial REAL, precio_actual REAL,
        titulo TEXT, zona TEXT, url TEXT
    );
    CREATE TABLE IF NOT EXISTS precios (
        id TEXT, fecha TEXT, precio REAL
    );
    CREATE TABLE IF NOT EXISTS salud (
        fecha TEXT PRIMARY KEY, anuncios INTEGER, en_zona INTEGER,
        nuevos INTEGER, desaparecidos INTEGER, avisos TEXT, requests INTEGER
    );
    -- Que perfiles ya tuvieron su corrida de siembra. Antes la siembra se
    -- deducia de "la tabla anuncios esta vacia", que dejo de servir cuando
    -- aparecio un segundo perfil: la tabla ya esta llena, pero el perfil
    -- economico nunca corrio y su primer dia SI es una siembra.
    CREATE TABLE IF NOT EXISTS perfiles (
        nombre TEXT PRIMARY KEY, sembrado TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_anuncios_desap ON anuncios(desaparecido);
    """)
    # Veredictos de apariencia cacheados: juzgar una foto cuesta, y la foto
    # de un anuncio no cambia. Se juzga una vez y se recuerda.
    _migrar_veredictos(cx)

    # Base que ya venia corriendo antes de que existieran los perfiles: el
    # principal NO es una siembra, lleva meses andando. Sin esto su proxima
    # corrida se tragaria todas las novedades del dia creyendo que es el dia 1.
    hay_perfiles = cx.execute("SELECT COUNT(*) FROM perfiles").fetchone()[0]
    if not hay_perfiles:
        antes = cx.execute("SELECT MIN(primera_vez) FROM anuncios").fetchone()[0]
        if antes:
            cx.execute("INSERT INTO perfiles VALUES ('principal',?)", (antes,))
            print(f"  migracion: perfil 'principal' marcado sembrado desde {antes}")
    cx.commit()


# --------------------------------------------------------------------------
# Pasada comun: corre UNA vez por corrida
# --------------------------------------------------------------------------

def pasada_comun(cx, hoy):
    data = json.loads((ROOT / "candidatos.json").read_text(encoding="utf-8"))
    salud, anuncios = data["salud"], data["anuncios"]

    if salud.get("error_fatal"):
        print("fetch fallo, no se actualiza el estado:", salud["error_fatal"])
        return 2

    # Segunda linea de defensa, por si fetch dejara pasar un crawl vacio: sin
    # anuncios no hay nada que aprender y si mucho que romper (todo lo vivo
    # quedaria marcado desaparecido). Preferimos no tocar la base.
    if not anuncios:
        print("fetch no trajo ningun anuncio; no se toca el estado")
        return 2

    descartados = cargar_descartados()

    en_zona, fuera = [], 0
    for a in anuncios:
        z, pri = clasificar_zona(a.get("zona_cruda"))
        if z is None:
            fuera += 1
            continue
        a["zona"], a["zona_prioridad"] = z, pri
        en_zona.append(a)

    vistos_antes = {r[0] for r in cx.execute("SELECT id FROM anuncios")}
    # Base vacia = primera corrida absoluta. Se siembra el estado y nada se
    # marca como novedad: Eric ya venia buscando a mano y el dia 1 seria una
    # avalancha de cosas que ya vio.
    siembra_global = not vistos_antes

    ids_hoy = {a["id"] for a in en_zona}
    nuevos = 0
    bajados = []

    for a in en_zona:
        aid = a["id"]
        if aid in descartados:
            a["descartado_por_eric"] = True
            a["razon_descarte"] = descartados[aid]
            bajados.append(aid)
            cx.execute("UPDATE anuncios SET ultima_vez=?, desaparecido=NULL WHERE id=?",
                       (hoy, aid))
            continue

        prev = cx.execute("SELECT precio_actual FROM anuncios WHERE id=?",
                          (aid,)).fetchone()
        if aid not in vistos_antes:
            cx.execute("""INSERT INTO anuncios
                (id,primera_vez,ultima_vez,precio_inicial,precio_actual,titulo,zona,url)
                VALUES (?,?,?,?,?,?,?,?)""",
                (aid, hoy, hoy, a["precio"], a["precio"], a["titulo"], a["zona"], a["url"]))
            a["es_nuevo"] = not siembra_global
            if a["es_nuevo"]:
                nuevos += 1
        else:
            if prev and prev[0] is not None and a["precio"] != prev[0]:
                a["precio_anterior"] = prev[0]
            cx.execute("""UPDATE anuncios SET ultima_vez=?, precio_actual=?,
                          desaparecido=NULL WHERE id=?""", (hoy, a["precio"], aid))
            a["es_nuevo"] = False
        cx.execute("INSERT INTO precios VALUES (?,?,?)", (aid, hoy, a["precio"]))

    # Desaparecidos = probablemente alquilados. Sirve para medir que tan rapido
    # se mueve el mercado y si Eric esta tardando en reaccionar.
    # Se calcula contra ids_hoy COMPLETO (todos los perfiles): si se hiciera por
    # perfil, cada uno declararia muerto lo del otro.
    # Un crawl incompleto se lee como una ola de alquileres (DECISIONES.md, el
    # caso del 2026-09-02: 26 desaparecidos contra una base de 2-5, y fue el
    # crawl). Antes de dar a nadie por muerto, se mira la proporcion: en 25 dias
    # medidos el maximo real fue ~20% de la base viva en un dia. Si de golpe
    # falta mas de la mitad, el sospechoso es el crawl, no el mercado: se avisa
    # y NO se marca a nadie. Perder un dia de senal se recupera manana; marcar
    # desaparecida media base es irreversible sin tocar la base a mano.
    # PERO la proporcion sola no alcanza como discriminante, y creerlo deja la
    # base trabada: tras el bloqueo de Cloudflare del 2026-09-23 quedaron 45
    # activos con ultima_vez del 21. El dia que se recupere el acceso, buena
    # parte va a estar alquilada de verdad; con el umbral pelado eso da >50%,
    # el guarda se niega a marcar a nadie, y como esos anuncios no vuelven a
    # aparecer nunca, se niega otra vez al dia siguiente y para siempre: 45
    # zombis vivos en el reporte y un aviso diario. La premisa del guarda es
    # "un mercado de ~500 no se vacia en UN dia"; despues de una semana sin
    # crawl esa premisa no aplica.
    # El discriminante que si sirve es el VOLUMEN del crawl, que es lo que de
    # verdad distingue los dos casos: un crawl mutilado trae 3 anuncios donde
    # siempre trae ~500, y ahi la mortandad es un artefacto. Si el crawl vino
    # con volumen normal, no esta mutilado y las bajas son dato real, aunque
    # sean muchas.
    UMBRAL_MORTANDAD = 0.50
    UMBRAL_VOLUMEN = 0.70        # del volumen tipico reciente
    hist = [r[0] for r in cx.execute(
        "SELECT anuncios FROM salud WHERE anuncios > 0 ORDER BY fecha DESC LIMIT 30")]
    tipico = sorted(hist)[len(hist) // 2] if hist else 0
    crawl_sano = bool(tipico) and len(anuncios) >= tipico * UMBRAL_VOLUMEN

    activos = list(cx.execute("""SELECT id,titulo,zona,precio_actual,primera_vez,url
                                 FROM anuncios WHERE desaparecido IS NULL"""))
    faltan = [r for r in activos if r[0] not in ids_hoy]

    desaparecidos = []
    if (not crawl_sano and len(activos) >= 10
            and len(faltan) > len(activos) * UMBRAL_MORTANDAD):
        aviso = (f"mortandad implausible: faltan {len(faltan)} de {len(activos)} "
                 f"activos ({len(faltan) * 100 // len(activos)}%) y el crawl trajo "
                 f"{len(anuncios)} anuncios contra {tipico} tipicos. Se asume crawl "
                 f"incompleto y NO se marcan desaparecidos")
        print("  AVISO:", aviso)
        salud.setdefault("avisos", []).append(aviso)
    else:
        for r in faltan:
            cx.execute("UPDATE anuncios SET desaparecido=? WHERE id=?", (hoy, r[0]))
            dias = None
            try:
                dias = (datetime.strptime(hoy, "%Y-%m-%d")
                        - datetime.strptime(r[4], "%Y-%m-%d")).days
            except Exception:
                pass
            desaparecidos.append({"id": r[0], "titulo": r[1], "zona": r[2],
                                  "precio": r[3], "dias_visible": dias, "url": r[5]})

        # Si hubo dias sin crawl (bloqueo, Pi apagada), las bajas de la primera
        # corrida buena son el HUECO acumulado de todos esos dias, no una ola de
        # alquileres de hoy. Se deja dicho en salud para que la velocidad del
        # mercado no se lea mal despues.
        ultimo_bueno = cx.execute(
            "SELECT MAX(fecha) FROM salud WHERE anuncios > 0 AND fecha < ?",
            (hoy,)).fetchone()[0]
        if desaparecidos and ultimo_bueno:
            try:
                hueco = (datetime.strptime(hoy, "%Y-%m-%d")
                         - datetime.strptime(ultimo_bueno, "%Y-%m-%d")).days
            except Exception:
                hueco = 0
            if hueco > 1:
                aviso = (f"primera corrida buena tras {hueco} dias sin crawl "
                         f"(ultimo: {ultimo_bueno}): los {len(desaparecidos)} "
                         f"desaparecidos son el hueco acumulado de esos dias, no "
                         f"bajas de hoy. No leerlo como velocidad del mercado")
                print("  AVISO:", aviso)
                salud.setdefault("avisos", []).append(aviso)

    cx.execute("""INSERT OR REPLACE INTO salud VALUES (?,?,?,?,?,?,?)""",
               (hoy, len(anuncios), len(en_zona), nuevos, len(desaparecidos),
                json.dumps(salud.get("avisos", []), ensure_ascii=False),
                salud.get("requests", 0)))
    cx.commit()

    COMUN.write_text(json.dumps({
        "fecha": hoy,
        "siembra_global": siembra_global,
        "total_bajados": len(anuncios),
        "fuera_de_zona": fuera,
        "salud": salud,
        "en_zona": en_zona,
        "desaparecidos": desaparecidos,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"comun: en_zona={len(en_zona)} (de {len(anuncios)}) nuevos={nuevos} "
          f"desaparecidos={len(desaparecidos)} descartados_eric={len(bajados)}")
    return 0


# --------------------------------------------------------------------------
# Pasada por perfil
# --------------------------------------------------------------------------

def pasada_perfil(cx, hoy, nombre):
    p = cargar_perfil(nombre)
    if not COMUN.exists():
        print(f"falta {COMUN.name}: corre primero state.py --pasada-comun")
        return 2
    comun = json.loads(COMUN.read_text(encoding="utf-8"))

    # Un perfil nuevo tiene su PROPIA siembra aunque la base ya este llena:
    # nunca vio estos anuncios, asi que su dia 1 no puede ser una avalancha
    # de "novedades" que en realidad llevan meses publicadas.
    fila = cx.execute("SELECT sembrado FROM perfiles WHERE nombre=?",
                      (nombre,)).fetchone()
    siembra = fila is None

    veredictos = {r[0]: {"clasificacion": r[1], "razon": r[2]}
                  for r in cx.execute(
                      "SELECT id,clasificacion,razon FROM veredictos WHERE perfil=?",
                      (nombre,))}

    cats = set(p["categorias"])
    tope = p["precio_max"]

    nuevos, activos, bajados = [], [], []
    for a in comun["en_zona"]:
        # El recorte del perfil: de que listado salio, y cuanto cuesta.
        if a.get("categoria") not in cats:
            continue
        if num(a.get("precio")) > tope:
            continue
        a = dict(a)

        if a.get("descartado_por_eric"):
            bajados.append(a)
            continue

        if a.get("es_nuevo") and not siembra:
            nuevos.append(a)
        else:
            a["es_nuevo"] = False
            # Se arrastra el veredicto de apariencia ya emitido POR ESTE PERFIL.
            if a["id"] in veredictos:
                a["veredicto_previo"] = veredictos[a["id"]]
            activos.append(a)

    cx.execute("INSERT OR REPLACE INTO perfiles VALUES (?,?)",
               (nombre, fila[0] if fila else hoy))
    cx.commit()

    orden = lambda a: (a.get("zona_prioridad", 99), num(a.get("precio")))
    stats = {
        "total_bajados": comun["total_bajados"],
        "en_zona": len(nuevos) + len(activos) + len(bajados),
        "fuera_de_zona": comun["fuera_de_zona"],
        "nuevos": len(nuevos),
        "activos": len(activos),
        "desaparecidos": len(comun["desaparecidos"]),
        "descartados_por_eric": len(bajados),
    }

    salida = {
        "fecha": hoy,
        "perfil": nombre,
        "primera_corrida": siembra,
        "stats": stats,
        "salud": comun["salud"],
        "nuevos": sorted(nuevos, key=orden),
        "activos": sorted(activos, key=orden),
        "desaparecidos": comun["desaparecidos"],
        "descartados_por_eric": bajados,
    }
    # Archivo COMPLETO: lo usa render.py, que necesita url, foto y todo.
    (ROOT / f"candidatos_del_dia{sufijo(nombre)}.json").write_text(
        json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")

    # Archivo RECORTADO: lo usa Claude. Un anuncio ya juzgado no necesita
    # viajar entero — su descripcion y sus fotos solo servian para formarse una
    # opinion, y esa opinion ya existe en la base. Se le deja lo justo para
    # comparar precios y escribir el resumen del dia. Medido: mandar todo
    # costaba ~$1 por dia incluso sin un solo anuncio nuevo.
    def compacto(a):
        return {k: a.get(k) for k in
                ("id", "titulo", "precio", "zona", "recamaras", "banos",
                 "estacionamientos", "metros2")
                if a.get(k) is not None}

    juzgar, ya_vistos = [], []
    for a in activos:
        if "veredicto_previo" in a and "precio_anterior" not in a:
            c = compacto(a)
            c["ya_juzgado"] = a["veredicto_previo"].get("clasificacion")
            ya_vistos.append(c)
        else:
            juzgar.append(a)

    (ROOT / f"para_criterio{sufijo(nombre)}.json").write_text(json.dumps({
        "fecha": hoy,
        "perfil": nombre,
        "primera_corrida": siembra,
        "stats": stats,
        "salud": {"avisos": comun["salud"].get("avisos", [])},
        "nuevos": salida["nuevos"],
        "a_juzgar": sorted(juzgar, key=orden),
        "ya_juzgados": sorted(ya_vistos, key=lambda a: a.get("precio") or 0),
        "desaparecidos": comun["desaparecidos"],
        "descartados_por_eric": [{"id": b["id"], "titulo": b.get("titulo"),
                                  "razon": b.get("razon_descarte")} for b in bajados],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{nombre}: en_zona={stats['en_zona']} nuevos={stats['nuevos']} "
          f"activos={stats['activos']} a_juzgar={len(juzgar)} "
          f"ya_juzgados={len(ya_vistos)}{' [SIEMBRA]' if siembra else ''}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pasada-comun", action="store_true",
                    help="actualiza la base y calcula desaparecidos (una vez)")
    ap.add_argument("--perfil", choices=ORDEN,
                    help="recorta dia_comun.json para ese perfil")
    args = ap.parse_args()

    if not args.pasada_comun and not args.perfil:
        ap.error("elegi --pasada-comun o --perfil <nombre>")

    hoy = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    cx = sqlite3.connect(DB)
    init_db(cx)
    try:
        if args.pasada_comun:
            rc = pasada_comun(cx, hoy)
            if rc:
                return rc
        if args.perfil:
            return pasada_perfil(cx, hoy, args.perfil)
        return 0
    finally:
        cx.close()


if __name__ == "__main__":
    sys.exit(main())
