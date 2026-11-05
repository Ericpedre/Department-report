#!/usr/bin/env python3
"""
mercado.py — almacen historico del mercado. MECANICO: no opina, no llama a Claude.

Por que existe: la corrida diaria baja ~502 anuncios con ~25 campos cada uno y
`state.py` persiste 9 columnas de los ~51 que caen en las zonas de Eric. El resto
se sobrescribe en candidatos.json todas las mananas a las 7:00. El dato que hace
falta para estudiar el mercado YA cruza el cable; lo unico que faltaba era
guardarlo. Por eso esto no agrega un solo request.

Guarda TODO lo que se bajo, incluido lo que esta fuera de zona (zona queda NULL,
zona_cruda conserva el texto del sitio). Filtrar despues siempre se puede;
recuperar lo que no se guardo, nunca.

REGLA DURA: esto es ACCESORIO. La corrida que le manda el reporte a Eric no puede
romperse por el almacen. Cualquier excepcion se registra y devuelve 0, salvo que
se pida --estricto (que es como corren las pruebas).

Se usa de dos formas:
  python3 mercado.py                      ingesta diaria desde candidatos.json
  from mercado import ingestar            lo llama venta.py con sus anuncios
"""
import argparse, gzip, json, shutil, sqlite3, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

# La clasificacion de zona NO se duplica: es la misma funcion que decide el
# reporte diario. Si Eric cambia sus zonas, cambian en los dos lados a la vez.
from state import clasificar_zona

ROOT = Path(__file__).resolve().parent
DB = ROOT / "mercado.db"
HISTORICO = ROOT / "historico"

# Orden de los tiers pagados, de menos a mas visible. Se guarda el MAXIMO visto:
# un anuncio que subio a platinum compro visibilidad, y eso confunde cualquier
# medicion de "cuanto tarda en colocarse" si no se controla.
TIERS = ["silver", "gold", "platinum"]

ESQUEMA = """
-- Un anuncio: sus atributos y la ventana de vida OBSERVADA.
-- Los atributos se llenan por COALESCE (nunca se pisa un valor ya visto), porque
-- la cobertura del sitio es despareja: metros2 viene en el 60% de los anuncios y
-- a veces aparece recien al segundo dia.
CREATE TABLE IF NOT EXISTS listing (
  id TEXT PRIMARY KEY,
  operacion TEXT,            -- 'alquiler' | 'venta'
  categoria TEXT,            -- de que listado salio: hecho de la URL que pedimos,
                             -- NO subCategoryType, que lo llena el anunciante y miente
  titulo TEXT, url TEXT, descripcion TEXT,
  zona_cruda TEXT, zona TEXT, zona_prioridad INTEGER,
  provincia TEXT, distrito TEXT, corregimiento TEXT, region TEXT,
  lat REAL, lon REAL,
  recamaras REAL, banos REAL, estacionamientos REAL, metros2 REAL,
  equipamiento TEXT,         -- JSON del array `benefits`
  amoblado TEXT, balcon TEXT, piscina TEXT, pet_friendly TEXT,
  piso REAL, tipo_piso TEXT, antiguedad TEXT, edificio TEXT, tipo TEXT,
  anunciante TEXT, anunciante_nombre TEXT,
  mantenimiento REAL,
  publicado TEXT,            -- dateCreated del sitio: la fecha REAL de publicacion.
                             -- Es el t=0 correcto. Medir desde primera_vez haria
                             -- ver mas rapidos a los anuncios que ya estaban
                             -- publicados cuando empezamos a mirar.
  n_fotos INTEGER, largo_desc INTEGER,
  primera_vez TEXT, ultima_vez TEXT,
  ausente_desde TEXT,        -- primer dia que no se vio (sospecha)
  baja_confirmada TEXT,      -- dia en que la baja se dio por buena (ver mercado_consulta)
  precio_inicial REAL, precio_actual REAL, precio_min REAL,
  tier_max TEXT,
  origen TEXT                -- 'scrape' | 'estado' (migrado de estado.db, campos parciales)
);

-- El hecho crudo: una fila por (anuncio, dia en que se vio). Todo lo demas se
-- deriva de aca, asi que si una heuristica esta mal se recalcula sin perder nada.
CREATE TABLE IF NOT EXISTS observacion (
  id TEXT, fecha TEXT, precio REAL, tier TEXT, actualizado TEXT,
  PRIMARY KEY (id, fecha)
);

-- Calidad del crawl por dia y categoria. Sin esto no se puede censurar, y sin
-- censurar un dia de crawl incompleto se lee como una ola de alquileres:
-- el 2026-09-02 registro 26 desaparecidos contra una base de 2-5 SOLO porque
-- bajaron 469 anuncios en vez de 553.
CREATE TABLE IF NOT EXISTS crawl (
  fecha TEXT, operacion TEXT, categoria TEXT,
  total_declarado INTEGER, bajados INTEGER, paginas INTEGER, requests INTEGER,
  completo INTEGER,          -- 1 si bajados >= 95% de total_declarado
  avisos TEXT,
  PRIMARY KEY (fecha, categoria)
);

CREATE INDEX IF NOT EXISTS ix_obs_fecha ON observacion(fecha);
CREATE INDEX IF NOT EXISTS ix_listing_op ON listing(operacion, zona);
CREATE INDEX IF NOT EXISTS ix_listing_baja ON listing(baja_confirmada);
"""


def init_db(cx):
    cx.executescript(ESQUEMA)
    cx.commit()


def _partes(zona_cruda):
    """'Panama Provincia, Ciudad de Panama, Pueblo Nuevo, Hato Pintado'
    -> (provincia, distrito, corregimiento). Puro split, sin interpretar.

    Sirve para agrupar los ~450 anuncios diarios que quedan fuera de las 9 zonas
    de Eric: sin esto son una bolsa de texto libre y no se pueden comparar.
    """
    p = [x.strip() for x in (zona_cruda or "").split(",") if x.strip()]
    return (p[0] if len(p) > 0 else None,
            p[1] if len(p) > 1 else None,
            p[2] if len(p) > 2 else None)


def _mejor_tier(a, b):
    """El tier mas alto de los dos. None (sin destacar) pierde contra cualquiera."""
    ia = TIERS.index(a) if a in TIERS else -1
    ib = TIERS.index(b) if b in TIERS else -1
    return a if ia >= ib else b


def _fila(a, operacion, fecha):
    """Aplana un anuncio normalizado de fetch.py a las columnas de `listing`."""
    prov, dist, corr = _partes(a.get("zona_cruda"))
    zona, prioridad = clasificar_zona(a.get("zona_cruda"))
    desc = a.get("descripcion") or ""
    return {
        "id": str(a.get("id")),
        "operacion": operacion,
        "categoria": a.get("categoria"),
        "titulo": a.get("titulo"),
        "url": a.get("url"),
        "descripcion": a.get("descripcion"),
        "zona_cruda": a.get("zona_cruda"),
        "zona": zona,
        "zona_prioridad": prioridad,
        "provincia": prov, "distrito": dist, "corregimiento": corr,
        "region": a.get("region"),
        "lat": a.get("lat"), "lon": a.get("lon"),
        "recamaras": a.get("recamaras"), "banos": a.get("banos"),
        "estacionamientos": a.get("estacionamientos"), "metros2": a.get("metros2"),
        "equipamiento": json.dumps(a.get("equipamiento") or [], ensure_ascii=False),
        "amoblado": str(a["amoblado"]) if a.get("amoblado") is not None else None,
        "balcon": str(a["balcon"]) if a.get("balcon") is not None else None,
        "piscina": str(a["piscina"]) if a.get("piscina") is not None else None,
        "pet_friendly": str(a["pet_friendly"]) if a.get("pet_friendly") is not None else None,
        "piso": a.get("piso"), "tipo_piso": a.get("tipo_piso"),
        # TEXT a proposito: en alquiler `age` son ANOS de antiguedad (2) y en
        # venta es el ANO de construccion ('1983'). Lo desambigua la consulta,
        # segun `operacion`; guardarlo como numero perderia esa distincion.
        "antiguedad": str(a["antiguedad"]) if a.get("antiguedad") is not None else None,
        "edificio": a.get("edificio"), "tipo": a.get("tipo"),
        "anunciante": a.get("anunciante"),
        "anunciante_nombre": a.get("anunciante_nombre"),
        "mantenimiento": a.get("mantenimiento"),
        "publicado": a.get("publicado"),
        "n_fotos": len(a.get("fotos") or []),
        "largo_desc": len(desc),
        "primera_vez": fecha, "ultima_vez": fecha,
        "precio_inicial": a.get("precio"), "precio_actual": a.get("precio"),
        "precio_min": a.get("precio"),
        "tier_max": a.get("destacado"),
        "origen": "scrape",
    }


# Columnas que se rellenan si estaban vacias pero NUNCA se pisan: si el sitio
# corrige un m2 el primer valor sigue siendo el que el mercado vio primero, y la
# serie de precios (que si cambia) ya vive entera en `observacion`.
_COALESCE = [
    "categoria", "titulo", "url", "descripcion", "zona_cruda", "zona",
    "zona_prioridad", "provincia", "distrito", "corregimiento", "region",
    "lat", "lon", "recamaras", "banos", "estacionamientos", "metros2",
    "amoblado", "balcon", "piscina", "pet_friendly", "piso", "tipo_piso",
    "antiguedad", "edificio", "tipo", "anunciante", "anunciante_nombre",
    "mantenimiento", "publicado",
]


def ingestar(cx, anuncios, salud, operacion, fecha):
    """Guarda un dia de scraping. Idempotente: correrlo dos veces no cambia nada.

    Devuelve un dict con el resumen, para que el log diga algo util.
    """
    sets = ", ".join(f"{c}=COALESCE(listing.{c}, excluded.{c})" for c in _COALESCE)
    nuevos = revividos = 0

    for a in anuncios:
        f = _fila(a, operacion, fecha)

        # El tier se resuelve en Python porque SQLite no conoce el orden
        # silver < gold < platinum. Se guarda el MAXIMO visto en toda la vida
        # del anuncio: lo que importa es si en algun momento compro visibilidad.
        prev = cx.execute(
            "SELECT tier_max, ausente_desde FROM listing WHERE id=?",
            (f["id"],)).fetchone()
        if prev is None:
            nuevos += 1
        elif prev[1] is not None:
            revividos += 1
        f["tier_max"] = _mejor_tier(prev[0] if prev else None, f["tier_max"])

        cols = ", ".join(f)
        ph = ", ".join(f":{c}" for c in f)
        cx.execute(f"""
            INSERT INTO listing ({cols}) VALUES ({ph})
            ON CONFLICT(id) DO UPDATE SET
              {sets},
              ultima_vez    = excluded.ultima_vez,
              precio_actual = excluded.precio_actual,
              precio_min    = MIN(COALESCE(listing.precio_min, excluded.precio_actual),
                                  COALESCE(excluded.precio_actual, listing.precio_min)),
              tier_max      = excluded.tier_max,
              -- Reaparecio con el MISMO id: no se alquilo, se habia caido del
              -- listado. La sospecha de baja queda sin efecto.
              ausente_desde   = NULL,
              baja_confirmada = NULL
            """, f)

        cx.execute("INSERT OR IGNORE INTO observacion VALUES (?,?,?,?,?)",
                   (f["id"], fecha, a.get("precio"), a.get("destacado"),
                    a.get("actualizado")))

    # Conteo por categoria sobre lo REALMENTE bajado. Ojo: fetch.py deduplica por
    # id entre categorias (el ultimo listado gana), asi que un anuncio que sale en
    # dos listados cuenta una sola vez. Es un desvio chico, y el umbral de 95%
    # del campo `completo` lo absorbe.
    por_cat = {}
    for a in anuncios:
        por_cat[a.get("categoria")] = por_cat.get(a.get("categoria"), 0) + 1

    for cat, info in (salud.get("categorias") or {}).items():
        bajados = por_cat.get(cat, 0)
        declarado = info.get("total_declarado") or 0
        completo = 1 if declarado and bajados >= declarado * 0.95 else 0
        cx.execute("INSERT OR REPLACE INTO crawl VALUES (?,?,?,?,?,?,?,?,?)",
                   (fecha, operacion, cat, declarado, bajados,
                    info.get("paginas"), salud.get("requests"), completo,
                    json.dumps(salud.get("avisos", []), ensure_ascii=False)))

    cx.commit()
    total = cx.execute("SELECT COUNT(*) FROM listing WHERE operacion=?",
                       (operacion,)).fetchone()[0]
    return {"ingestados": len(anuncios), "en_base": total,
            "nuevos": nuevos, "revividos": revividos,
            "categorias": len(salud.get("categorias") or {})}


def archivar(entrada, operacion, fecha):
    """Copia comprimida del scraping crudo del dia.

    Es el seguro contra el esquema equivocado: cuando en seis meses aparezca un
    campo que hoy no se guardo, se re-deriva de aca en vez de esperar otro ano.
    ~1 MB -> ~120 KB; unos 45 MB al ano, con 5.7 GB libres en la Pi.
    """
    destino = HISTORICO / operacion
    destino.mkdir(parents=True, exist_ok=True)
    salida = destino / f"{fecha}.json.gz"
    with open(entrada, "rb") as src, gzip.open(salida, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, length=256 * 1024)   # en streaming: la Pi tiene 905 MB
    return salida, salida.stat().st_size


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", default="candidatos.json")
    ap.add_argument("--operacion", default="alquiler", choices=["alquiler", "venta"])
    ap.add_argument("--fecha", help="por defecto hoy (hora local)")
    ap.add_argument("--sin-archivo", action="store_true",
                    help="no escribir el .json.gz (para pruebas)")
    ap.add_argument("--estricto", action="store_true",
                    help="devolver != 0 si falla. Por defecto falla suave, porque "
                         "esto NO puede tumbar la corrida diaria de Eric.")
    args = ap.parse_args()

    fecha = args.fecha or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    try:
        entrada = ROOT / args.entrada
        data = json.loads(entrada.read_text(encoding="utf-8"))
        salud, anuncios = data["salud"], data["anuncios"]

        if salud.get("error_fatal"):
            # Un fetch roto no se guarda: quedaria un dia con 0 anuncios que
            # despues se leeria como "desaparecieron todos".
            print(f"mercado: fetch fallo ({salud['error_fatal']}); no se ingesta")
            return 0

        cx = sqlite3.connect(DB)
        try:
            init_db(cx)
            r = ingestar(cx, anuncios, salud, args.operacion, fecha)
        finally:
            cx.close()

        msg = (f"mercado[{args.operacion}] {fecha}: ingestados={r['ingestados']} "
               f"en_base={r['en_base']} nuevos={r['nuevos']} "
               f"revividos={r['revividos']} cats={r['categorias']}")
        if not args.sin_archivo:
            ruta, tam = archivar(entrada, args.operacion, fecha)
            msg += f" archivo={ruta.name} ({tam // 1024} KB)"
        print(msg)
        return 0

    except Exception as e:
        # Falla suave a proposito: el reporte de Eric vale mas que un dia de datos.
        print(f"mercado: FALLO NO FATAL: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1 if args.estricto else 0


if __name__ == "__main__":
    sys.exit(main())
