#!/usr/bin/env python3
"""
notify.py — el aviso por Pushover.

El detalle va en el reporte, no en el push: Pushover corta el mensaje alrededor
de los 1024 caracteres. Por eso el enlace se manda como PARAMETRO `url` con su
`url_title`, que en el celular sale como boton aparte, y no pegado al texto.

Uso:
    python3 notify.py --perfil principal   -> aviso normal del dia
    python3 notify.py --perfil economico   -> aviso de la busqueda barata
    python3 notify.py --incidente          -> aviso de que algo se rompio
"""
import argparse, json, os, sys, urllib.parse, urllib.request
from datetime import date
from pathlib import Path

from perfiles import ORDEN, perfil as cargar_perfil, sufijo

ROOT = Path(__file__).resolve().parent
API = "https://api.pushover.net/1/messages.json"
LIMITE = 1024


def marca(nombre):
    """Fecha del ultimo aviso que SALIO de verdad, POR PERFIL.

    No es "la ultima corrida": lo que importa es la ultima vez que Eric supo
    algo de ESTA busqueda. Si la Pi estuvo apagada, o corrio sin internet y el
    push nunca salio, esta fecha se queda atras y el proximo aviso lo denuncia.

    Va por perfil porque con una marca compartida el primer push del dia la
    sellaria y el segundo perfil no podria denunciar nunca su propio silencio.
    """
    return ROOT / "logs" / f"ultimo_aviso{sufijo(nombre)}.txt"


def dias_de_silencio(MARCA):
    """Dias sin que saliera un aviso. 0 = normal (ayer hubo, u hoy es el primero).

    Devuelve (dias, ultima_fecha). dias>=1 significa que hubo al menos un dia
    entero sin noticias, que es justo lo que antes pasaba en silencio.
    """
    try:
        ultima = date.fromisoformat(MARCA.read_text(encoding="utf-8").strip())
    except Exception:
        return 0, None          # primera vez, o marca ilegible: no se alarma
    hueco = (date.today() - ultima).days - 1
    return (hueco, ultima) if hueco >= 1 else (0, ultima)


def linea_silencio(MARCA):
    """El aviso del hueco, para poner AL PRINCIPIO del mensaje.

    Va primero a proposito: Pushover corta a los 1024 caracteres por el final,
    y esto no se puede perder en el recorte.
    """
    dias, ultima = dias_de_silencio(MARCA)
    if not dias:
        return ""
    d = "dia" if dias == 1 else "dias"
    return f"[!] {dias} {d} sin reporte (el ultimo fue el {ultima}). "


def marcar_aviso(MARCA):
    """Deja constancia de que hoy si saliste avisado."""
    try:
        MARCA.parent.mkdir(parents=True, exist_ok=True)
        MARCA.write_text(date.today().isoformat(), encoding="utf-8")
    except Exception as e:
        print("no se pudo guardar la marca del aviso:", e)


def credenciales():
    """Lee .env. Nunca van en el codigo ni en el repo (estan en .gitignore)."""
    env = ROOT / ".env"
    if not env.exists():
        return None, None, None
    v = {}
    for linea in env.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, val = linea.partition("=")
            v[k.strip()] = val.strip().strip('"').strip("'")
    return (v.get("PUSHOVER_TOKEN"), v.get("PUSHOVER_USER"),
            v.get("REPORTE_URL", "").rstrip("/"))


def enviar(titulo, mensaje, url=None, url_title=None, prioridad=0, MARCA=None):
    token, user, _ = credenciales()
    if not token or not user:
        print("faltan credenciales de Pushover en .env; no se envia nada")
        return False
    if len(mensaje) > LIMITE:
        mensaje = mensaje[:LIMITE - 3] + "..."
    datos = {"token": token, "user": user, "title": titulo,
             "message": mensaje, "priority": prioridad}
    if url:
        datos["url"] = url
        datos["url_title"] = url_title or "Ver reporte"
    try:
        req = urllib.request.Request(
            API, data=urllib.parse.urlencode(datos).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = json.loads(r.read())
        if ok.get("status") == 1:
            print(f"pushover enviado ({len(mensaje)} chars)")
            if MARCA:
                marcar_aviso(MARCA)
            return True
        print("pushover rechazo el envio:", ok)
    except Exception as e:
        print("pushover fallo:", e)
    return False


def url_perfil(base, nombre):
    """URL publica del reporte de un perfil. El principal cuelga de la raiz."""
    if not base:
        return None
    salida = cargar_perfil(nombre)["salida"]
    return f"{base}/{salida}/latest/" if salida else f"{base}/latest/"


def recortar(texto, tope):
    """Corta `texto` a `tope` caracteres como maximo, con puntos al final.

    Corta en el ultimo espacio para no partir una palabra al medio, pero SOLO
    si ese espacio esta cerca del corte: si el texto no tiene espacios por un
    tramo largo (una URL, un volcado), retroceder hasta el espacio anterior se
    come el mensaje entero. Con tope 1024 y un `pendiente` sin espacios eso
    devolvia 16 caracteres y tiraba la pregunta a la basura.
    """
    if len(texto) <= tope:
        return texto
    duro = texto[:max(0, tope - 3)]
    corte = duro.rsplit(" ", 1)[0]
    if len(corte) >= len(duro) - 15:      # el espacio estaba cerca: sirve
        duro = corte
    return duro.rstrip() + "..."


def cuerpo_incidente(inc, silencio):
    """Arma el mensaje del incidente SIN pasarse del tope de Pushover.

    El orden de sacrificio es lo que importa. Si no entra todo, lo que se
    recorta es el DETALLE (que fallo, que corregi), nunca la pregunta: el
    detalle completo queda en el incidente archivado y en el reporte, pero lo
    que Eric tiene que decidir es lo unico que el push viene a pedirle.

    Antes el mensaje se armaba entero y `enviar` lo cortaba por el final, que
    es exactamente donde vive la pregunta. Medido sobre los incidentes del
    2026-09-28, 29 y 30: salieron los tres cortados en 1024 y se perdieron
    entre 430 y 480 caracteres de `pendiente`, con las opciones adentro.
    """
    fallo = inc.get("fallo", "desconocido")
    correccion = inc.get("correccion", "") if inc.get("resuelto") else ""
    pendiente = inc.get("pendiente", "")

    pregunta = f"Necesito de vos: {pendiente}" if pendiente else ""
    # Lo que queda para el detalle DESPUES de reservarle lugar a la pregunta.
    libre = LIMITE - len(silencio) - len(pregunta) - len("Falló: \n")
    if correccion:
        libre -= len("Ya lo corregí: \n")

    # Piso de 80 por parte: un detalle de una linea igual orienta, y sin piso
    # una pregunta larga dejaria el mensaje sin decir que se rompio.
    if correccion:
        fallo = recortar(fallo, max(80, libre // 2))
        correccion = recortar(correccion, max(80, libre - len(fallo)))
    else:
        fallo = recortar(fallo, max(80, libre))

    cuerpo = silencio + f"Falló: {fallo}\n"
    if correccion:
        cuerpo += f"Ya lo corregí: {correccion}\n"
    cuerpo += pregunta
    cuerpo = cuerpo.strip()

    # Ultimo recurso: si ni con el detalle en el piso entra la pregunta, se
    # recorta ella, pero aca y no en el tail ciego de `enviar`.
    return cuerpo if len(cuerpo) <= LIMITE else recortar(cuerpo, LIMITE)


def aviso_incidente():
    """Un incidente rompe la corrida ENTERA, no un perfil.

    Por eso se avisa UNA sola vez y se cuelga de la marca del principal: si
    fallo fetch o state, ninguno de los dos reportes existe.
    """
    ip = ROOT / "incidente.json"
    inc = json.loads(ip.read_text(encoding="utf-8")) if ip.exists() else {}
    _, _, base = credenciales()
    MARCA = marca("principal")
    resuelto = inc.get("resuelto")
    titulo = "Apartamentos: corregido solo" if resuelto else "Apartamentos: necesito algo tuyo"
    return enviar(titulo, cuerpo_incidente(inc, linea_silencio(MARCA)),
                  url=url_perfil(base, "principal"),
                  url_title="Ver el incidente",
                  prioridad=0 if resuelto else 1, MARCA=MARCA)


def aviso_dia(nombre):
    p = cargar_perfil(nombre)
    suf = sufijo(nombre)
    MARCA = marca(nombre)
    dia = json.loads((ROOT / f"candidatos_del_dia{suf}.json").read_text(encoding="utf-8"))
    vp = ROOT / p["veredictos"]
    v = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    _, _, base = credenciales()

    tp = p["titulo_push"]
    s = dia.get("stats", {})
    n = s.get("nuevos", 0)
    if dia.get("primera_corrida"):
        titulo = f"{tp}: siembra ({s.get('en_zona',0)} en tus zonas)"
    elif n:
        titulo = f"{tp}: {n} nuevo{'s' if n != 1 else ''} hoy"
    else:
        titulo = f"{tp}: sin novedades"

    partes = [v.get("linea_push") or "Sin hallazgos destacables hoy."]
    # Que cuenta como hallazgo destacable depende del perfil: el principal lo
    # mide contra Mystic Village, el economico en dolares ahorrados por mes.
    campo = p["campo_destacado"]
    destacados = [x.get(campo) for x in v.get("veredictos", []) if x.get(campo)]
    if destacados:
        numeros = [d for d in destacados if isinstance(d, (int, float))
                   and not isinstance(d, bool)]
        partes.append(p["frase_push"].format(
            n=len(destacados), mejor=max(numeros) if numeros else "?"))
    if s.get("desaparecidos"):
        partes.append(f"{s['desaparecidos']} desaparecieron (probablemente alquilados).")
    if (dia.get("salud") or {}).get("avisos"):
        partes.append("Hay avisos en el reporte.")

    # Si la URL publica no responde, es mejor decirlo en el propio aviso que
    # mandar un boton que no abre nada.
    enlace = url_perfil(base, nombre)
    if enlace:
        try:
            with urllib.request.urlopen(enlace, timeout=15) as r:
                if r.status != 200:
                    raise OSError(f"HTTP {r.status}")
        except Exception:
            partes.append("(El reporte no se esta publicando: revisa GitHub Pages.)")
            enlace = None

    hueco = linea_silencio(MARCA)
    if hueco:
        # Un hueco no es una novedad mas: se anuncia en el titulo y sube de
        # prioridad, porque significa que hubo dias sin saber nada.
        titulo = f"{tp}: volví tras un silencio"
    return enviar(titulo, hueco + " ".join(partes), url=enlace,
                  url_title="Ver reporte del día",
                  prioridad=1 if hueco else 0, MARCA=MARCA)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="manda el aviso por Pushover")
    ap.add_argument("--perfil", choices=ORDEN, default="principal")
    ap.add_argument("--incidente", action="store_true",
                    help="avisa de una corrida rota (una vez, no por perfil)")
    args = ap.parse_args()
    ok = aviso_incidente() if args.incidente else aviso_dia(args.perfil)
    sys.exit(0 if ok else 1)
