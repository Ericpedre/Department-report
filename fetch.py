#!/usr/bin/env python3
"""
fetch.py — capa MECANICA del buscador de apartamentos.

Baja los listados de Encuentra24 y extrae los campos crudos. NO interpreta nada:
no decide si un apartamento sirve, no clasifica, no puntua. Solo transporta.
Todo el criterio ocurre despues, en Claude (ver prompt_criterio.md).

Sin dependencias externas: solo stdlib (la Pi tiene 905 MB de RAM).

REGLAS DURAS DESCUBIERTAS EN LA INVESTIGACION (no cambiar sin volver a medir):

  1. UN SOLO FILTRO POR URL. Combinar filtros con coma
     (?q=f_rooms.2-2,f_price.0-685) hace que el servidor DESCARTE EL q ENTERO
     y devuelva la categoria sin filtrar, con HTTP 200. El total salta de
     315 a 8110. Esa es la causa de los "resultados inconsistentes".
     Por eso filtramos SOLO por precio en la URL, y el resto localmente.

  2. EL HTTP 200 NO SIRVE COMO SENAL DE EXITO. Las URLs invalidas devuelven
     200 con "Ups... esta pagina no existe". Hay que validar el CONTENIDO.

  3. El barrio en el path se IGNORA (bytes identicos a la categoria pelada).
     Las zonas se filtran localmente sobre location.locality.

  4. El orden del listado es por tier pagado (platinum > gold > silver) y
     LUEGO fecha. No se puede leer "solo las primeras paginas": los duenos
     particulares baratos caen en silver, al fondo.
"""
import gzip, json, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

from perfiles import FILTROS_PRECIO

BASE = "https://www.encuentra24.com/panama-es"

# Que categorias se bajan y con que tope de precio cada una vive en perfiles.py,
# porque el tope depende de a que perfil alimenta la categoria (las casas solo
# sirven al economico, que corta en 350). Aca solo se transporta: recorrer()
# recibe el dict de filtros y la lista de categorias sale de sus claves.

# User-Agent normal y honesto. No suplantamos navegadores ni rotamos nada.
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DELAY = 3.0          # segundos entre requests: somos un visitante diario
MAX_PAGES = 40       # tope de seguridad por categoria
RETRIES = 2          # sin reintentos agresivos

# OJO: NO se puede detectar el soft-404 buscando "esta pagina no existe" en el
# HTML: esa frase aparece UNA VEZ en TODAS las paginas, incluidas las buenas
# (es la plantilla de error del cliente). El discriminante real es que la
# pagina inexistente NO trae "initialSearchUrl" ni "metadata" en el payload.
ROOT = Path(__file__).resolve().parent


class Blocked(Exception):
    """El sitio nos bloqueo. Se para y se avisa; NUNCA se evade."""


def firma_bloqueo(e, url):
    """Describe COMO nos bloquearon, leyendo la respuesta que ya nos dieron.

    No cambia ni una request: solo mira el error que ya tenemos en la mano. Lo
    hace porque "HTTP 403" solo no distingue dos situaciones muy distintas, y
    la diferencia se paga en dias de crawl:

      - desafio JS (cf-mitigated: challenge, "Just a moment..."): Cloudflare
        quiere que un navegador ejecute JS. Puede ser transitorio.
      - bloqueo WAF ("Sorry, you have been blocked"): una regla nos denego en
        firme. Es mas grave y no se cae solo.

    El 2026-09-23 empezo como desafio y el 2026-09-30 ya era bloqueo WAF, pero
    el log de los dos dias decia exactamente lo mismo ("HTTP 403 en <url>"), asi
    que la escalada solo se vio curleando a mano. Con la firma escrita, el
    proximo cambio —para peor o para mejor— se ve solo mirando el log.

    El Ray ID se guarda porque es lo que el sitio pide incluir si uno les
    escribe para pedir acceso, que es la unica via limpia que nos queda.
    """
    h = getattr(e, "headers", None)
    def hdr(k):
        return (h.get(k) if h else None) or ""

    cuerpo = ""
    try:                                     # el cuerpo del error es opcional
        raw = e.read()
        if hdr("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        cuerpo = raw.decode("utf-8", "replace")[:4000]
    except Exception:
        pass

    if e.code == 429:
        clase = "rate limit"
    elif hdr("Cf-Mitigated") == "challenge" or "Just a moment" in cuerpo:
        clase = "desafio JS de Cloudflare"
    elif "you have been blocked" in cuerpo or "Attention Required" in cuerpo:
        clase = "bloqueo WAF de Cloudflare (regla en firme)"
    elif e.code == 503:
        clase = "503 del servidor"
    else:
        clase = f"HTTP {e.code} sin firma reconocida"

    datos = {"codigo": e.code, "clase": clase, "url": url,
             "server": hdr("Server"), "cf_ray": hdr("Cf-Ray"),
             "cf_mitigated": hdr("Cf-Mitigated"),
             "retry_after": hdr("Retry-After"),
             "titulo": (re.search(r"<title>([^<]{0,120})</title>", cuerpo)
                        or [None, ""])[1].strip()}
    detalle = ", ".join(f"{k}={v}" for k, v in
                        (("cf-ray", datos["cf_ray"]),
                         ("cf-mitigated", datos["cf_mitigated"]),
                         ("retry-after", datos["retry_after"]),
                         ("titulo", datos["titulo"])) if v)
    err = Blocked(f"HTTP {e.code} en {url} — {clase}"
                  + (f" [{detalle}]" if detalle else ""))
    err.datos = datos
    return err


class ParseError(Exception):
    """El HTML dejo de tener la forma esperada."""


def fetch(url):
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-PA,es;q=0.9",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 403/429 = nos estan bloqueando o pidiendo que bajemos el ritmo.
            # La respuesta correcta es PARAR y avisar, no insistir.
            if e.code in (403, 429, 503):
                raise firma_bloqueo(e, url)
            last = e
        except Exception as e:
            last = e
        if attempt < RETRIES:
            time.sleep(5 * (attempt + 1))
    raise ParseError(f"no se pudo bajar {url}: {last}")


def rsc_payload(html):
    """Reconstruye el payload RSC de Next.js (self.__next_f.push([1,"..."]))."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    if not chunks:
        raise ParseError("no se encontro el payload RSC (self.__next_f)")
    return "".join(json.loads(c) for c in chunks)


def _objects_around(payload, key='"price_value"'):
    """Extrae el objeto JSON balanceado que contiene cada aparicion de `key`."""
    out = []
    for m in re.finditer(re.escape(key), payload):
        depth, start = 0, None
        for j in range(m.start(), -1, -1):
            c = payload[j]
            if c == "}":
                depth += 1
            elif c == "{":
                if depth == 0:
                    start = j
                    break
                depth -= 1
        if start is None:
            continue
        depth = 0
        for j in range(start, len(payload)):
            c = payload[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        out.append(json.loads(payload[start:j + 1]))
                    except ValueError:
                        pass
                    break
    return out


def _clean(v):
    """Next.js serializa ausencias como '$undefined' y fechas como '$D<iso>'."""
    if v == "$undefined" or v == "":
        return None
    if isinstance(v, str) and v.startswith("$D"):
        return v[2:]
    return v


def _num(v):
    """rooms/bathrooms/parking llegan a veces como str y a veces como int.
    Se normaliza aqui para que nadie aguas abajo tenga que adivinar el tipo."""
    v = _clean(v)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(str(v).replace(",", "."))
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def _amount(v):
    """maintenanceCost / rent traen {'amount': {'value': N}}."""
    if isinstance(v, dict):
        return (v.get("amount") or {}).get("value")
    return None


def normalize(a, categoria=None):
    """Aplana un anuncio a los campos que le importan a Claude. Sin juicio.

    `categoria` es la categoria de ORIGEN, o sea de que listado se bajo. Es lo
    que despues permite que las casas no se cuelen en el perfil principal.
    Ojo: NO usar `subCategoryType` para eso — ese campo lo llena el anunciante
    y miente; la categoria de origen es un hecho de la URL que pedimos.
    """
    loc = a.get("location") or {}
    user = a.get("user") or {}
    return {
        "id": str(a.get("id")),
        "categoria": categoria,
        "titulo": _clean(a.get("title")),
        "url": "https://www.encuentra24.com" + (a.get("link") or ""),
        "precio": a.get("price_value"),
        "mantenimiento": _amount(a.get("maintenanceCost")),
        "recamaras": _num(a.get("rooms")),
        "banos": _num(a.get("bathrooms")),
        "estacionamientos": _num(a.get("parking")),
        "metros2": _num(a.get("square")) or _num(a.get("lotSize")),
        # `benefits` es la linea blanca como DATO, no como prosa.
        "equipamiento": a.get("benefits") or [],
        "amoblado": _clean(a.get("furnished")),
        "balcon": _clean(a.get("balcon")),
        "piscina": _clean(a.get("swimmingPool")),
        "pet_friendly": _clean(a.get("petFriendly")),
        "piso": _num(a.get("floor")),
        "tipo_piso": _clean(a.get("floorType")),
        "antiguedad": _num(a.get("age")),
        "edificio": _clean(a.get("exactAddress")),
        "tipo": _clean(a.get("subCategoryType")),
        "anunciante": _clean(a.get("advertiserType")),
        "anunciante_nombre": _clean(user.get("name")),
        "zona_cruda": _clean(loc.get("locality")),
        "region": _clean(a.get("regionName")),
        "lat": loc.get("lat") or None,
        "lon": loc.get("lon") or None,
        # La prosa entera, verbatim. Aqui NO se aplica ningun regex.
        "descripcion": _clean(a.get("description")),
        "publicado": _clean(a.get("dateCreated")),
        "actualizado": _clean(a.get("dateUpdated")),
        "destacado": _clean(a.get("featured")),
        "republicado": a.get("reposted"),
        "activo": a.get("active"),
        "foto": _clean(a.get("principalImage")),
        "fotos": (a.get("images") or [])[:6],
    }


def scrape_page(cat, page, filtros=None):
    """Baja una pagina y devuelve (anuncios, metadata, avisos_de_salud).

    `filtros` permite reusar todo esto para el crawl semanal de venta, que tiene
    su propio dict (perfiles.FILTROS_VENTA). Por defecto, el diario de siempre.
    """
    filtro = (filtros or FILTROS_PRECIO)[cat]
    url = f"{BASE}/{cat}?q={filtro}" + (f"&page={page}" if page > 1 else "")
    html = fetch(url)
    warn = []

    payload = rsc_payload(html)

    # Dos fallas distintas que ambas devuelven HTTP 200. Se separan porque
    # piden arreglos distintos: la primera es una URL que hay que corregir,
    # la segunda es una consulta mal formada.
    m = re.search(r'"initialSearchUrl":"([^"]*)"', payload)
    if not m:
        raise ParseError(
            f"soft-404: HTTP 200 pero la pagina no existe (sin initialSearchUrl "
            f"ni metadata). Probablemente cambio el slug de categoria: {url}")

    # GUARDA CRITICA: si el filtro se cayo en silencio, initialSearchUrl vuelve
    # a la categoria pelada. Sin esto creeriamos tener 455 anuncios baratos
    # cuando en realidad tenemos 8110 de cualquier precio.
    search_url = m.group(1)
    if "f_price" not in search_url:
        raise ParseError(
            f"el filtro de precio se cayo en silencio en {url} "
            f"(initialSearchUrl={search_url!r})")

    meta = {}
    mm = re.search(r'"metadata":\{"total":(\d+),"page":(\d+),'
                   r'"itemsPerPage":(\d+),"totalPages":(\d+)', payload)
    if mm:
        meta = {"total": int(mm.group(1)), "page": int(mm.group(2)),
                "por_pagina": int(mm.group(3)), "paginas": int(mm.group(4))}
    else:
        warn.append(f"{cat} p{page}: no se encontro metadata de paginacion")

    ads = [normalize(a, cat) for a in _objects_around(payload)]
    if not ads:
        warn.append(f"{cat} p{page}: 0 anuncios extraidos")

    return ads, meta, warn


def recorrer(filtros, max_pages=MAX_PAGES, rango_precio=(1, 5000)):
    """Recorre todas las paginas de cada categoria de `filtros`.

    Es el corazon mecanico del scraping, y esta separado de main() para que el
    crawl semanal de venta (venta.py) lo reuse TAL CUAL. Aca viven las guardas
    que costaron caro descubrir —soft-404 con HTTP 200, el filtro que se cae en
    silencio, el 403 que no se reintenta— y tener dos copias de eso seria la
    forma mas facil de que una se quede vieja sin que nadie lo note.

    `rango_precio` es lo unico que cambia de verdad entre alquiler y venta: un
    anuncio de $140 000 es basura en alquiler y normal en venta.
    """
    inicio = datetime.now(timezone.utc)
    anuncios, salud = {}, {
        "inicio": inicio.isoformat(), "categorias": {}, "avisos": [],
        "requests": 0, "bloqueado": False, "error_fatal": None,
    }

    try:
        for cat in filtros:
            ads, meta, warn = scrape_page(cat, 1, filtros)
            salud["requests"] += 1
            salud["avisos"] += warn
            total = meta.get("total", 0)
            paginas = min(meta.get("paginas", 1), max_pages)
            for a in ads:
                anuncios[a["id"]] = a
            vistos_p1 = len(ads)

            for pg in range(2, paginas + 1):
                time.sleep(DELAY)
                ads, _, warn = scrape_page(cat, pg, filtros)
                salud["requests"] += 1
                salud["avisos"] += warn
                # Se espera 20 por pagina salvo la ultima.
                if len(ads) != 20 and pg != paginas:
                    salud["avisos"].append(
                        f"{cat} p{pg}: {len(ads)} anuncios (se esperaban 20)")
                for a in ads:
                    anuncios[a["id"]] = a

            salud["categorias"][cat] = {
                "total_declarado": total, "paginas": paginas,
                "primera_pagina": vistos_p1,
            }
            time.sleep(DELAY)

    except Blocked as e:
        # Nos bloquearon: se para en seco. No se evade, no se reintenta.
        salud["bloqueado"] = True
        salud["error_fatal"] = str(e)
        # La firma queda aparte y estructurada para poder comparar un dia con
        # otro sin parsear el mensaje a ojo.
        salud["bloqueo"] = getattr(e, "datos", None)
    except (ParseError, Exception) as e:
        salud["error_fatal"] = f"{type(e).__name__}: {e}"

    # COLAPSO TOTAL. El 2026-09-22 el sitio contesto HTTP 200 con el payload RSC
    # bien formado —initialSearchUrl con f_price, metadata presente— pero con
    # "total":0 en las tres categorias. Como ninguna request fallo, error_fatal
    # quedo en None, state.py lo leyo como una corrida buena y marco
    # desaparecidos los 45 anuncios vivos de un saque. Un mercado de ~500
    # anuncios no se vacia en un dia: si no bajo NADA, fallo el crawl, no el
    # mercado (DECISIONES.md: "0 anuncios o desviacion fuerte = alarma").
    # Se marca fatal aca para que nadie aguas abajo lo confunda con un mercado
    # vacio.
    if not anuncios and not salud["error_fatal"]:
        salud["error_fatal"] = (
            f"colapso total: 0 anuncios en las {len(filtros)} categorias "
            f"({salud['requests']} requests, sin error de red). El sitio "
            f"contesto pero vino vacio; no se propaga como corrida buena")

    salud["fin"] = datetime.now(timezone.utc).isoformat()
    salud["anuncios_unicos"] = len(anuncios)

    # Sanidad de campos: si de golpe llegan vacios, el sitio cambio de forma.
    if anuncios:
        vals = list(anuncios.values())
        for campo in ("titulo", "precio", "recamaras", "zona_cruda", "foto"):
            vacios = sum(1 for a in vals if a.get(campo) in (None, "", []))
            if vacios > len(vals) * 0.30:
                salud["avisos"].append(
                    f"campo '{campo}' vacio en {vacios}/{len(vals)} anuncios")
        lo, hi = rango_precio
        raros = [a["id"] for a in vals
                 if not isinstance(a.get("precio"), (int, float))
                 or not (lo <= (a.get("precio") or 0) <= hi)]
        if raros:
            salud["avisos"].append(f"{len(raros)} precios fuera de rango sensato")

    return anuncios, salud


def main():
    anuncios, salud = recorrer(FILTROS_PRECIO)

    (ROOT / "candidatos.json").write_text(json.dumps(
        {"salud": salud, "anuncios": list(anuncios.values())},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"anuncios={len(anuncios)} requests={salud['requests']} "
          f"avisos={len(salud['avisos'])} bloqueado={salud['bloqueado']}")
    for w in salud["avisos"][:10]:
        print("  aviso:", w)
    if salud["error_fatal"]:
        print("  ERROR FATAL:", salud["error_fatal"])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
