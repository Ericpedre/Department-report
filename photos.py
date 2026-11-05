#!/usr/bin/env python3
"""
photos.py — deja las fotos en un formato que Claude pueda MIRAR.

Eric descarta apartamentos por como se ven. Ese juicio necesita la imagen real,
no la URL. Pero el CDN de Encuentra24 sirve SIEMPRE AVIF (ignora Accept y las
transformaciones f_jpg), y Pillow 11.1 no lee AVIF. ffmpeg si, y ya esta
instalado en la Pi. De ahi este paso.

Solo se bajan fotos de anuncios que hace falta juzgar: los nuevos, y los que
todavia no tienen veredicto de apariencia guardado. Un anuncio ya juzgado no
se vuelve a juzgar, porque su foto no cambia.

Corre una vez por perfil (--perfil). La carpeta fotos/ es COMPARTIDA y sus
archivos van por id de anuncio, asi que un anuncio que caiga en los dos
perfiles se baja y se convierte una sola vez.
"""
import argparse, json, shutil, subprocess, sys, urllib.request
from pathlib import Path

from perfiles import ORDEN, perfil as cargar_perfil, sufijo

ROOT = Path(__file__).resolve().parent
FOTOS = ROOT / "fotos"
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ANCHO = 560         # suficiente para opinar, barato en tokens y en la Pi

# Presupuesto de imagenes. Cada foto que Claude mira cuesta contexto, y el dia
# de siembra hay ~50 candidatos de golpe contra los ~3 nuevos de un dia normal.
# Por eso el presupuesto es distinto segun el dia:
POR_ANUNCIO_NUEVO = 3    # un anuncio nuevo merece edificio + interior
POR_ANUNCIO_SIEMBRA = 2  # en la siembra alcanza con menos
# El tope de anuncios sale del perfil (max_fotos_anuncios): el economico mueve
# 2 o 3 candidatos por dia y no tiene sentido darle el mismo presupuesto que al
# principal. En la siembra se recorta a 4/5 del tope del perfil.


def convertir(url, destino):
    """Baja el AVIF y lo pasa a JPEG con ffmpeg. True si quedo utilizable."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=45) as r:
            crudo = r.read()
    except Exception as e:
        print(f"    no se pudo bajar: {e}")
        return False

    tmp = destino.with_suffix(".avif")
    tmp.write_bytes(crudo)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
             "-vf", f"scale={ANCHO}:-2", "-q:v", "4", str(destino)],
            check=True, timeout=90,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:
        print(f"    ffmpeg fallo: {e}")
        return False
    finally:
        tmp.unlink(missing_ok=True)
    return destino.exists() and destino.stat().st_size > 0


def main():
    ap = argparse.ArgumentParser(description="baja y convierte fotos de un perfil")
    ap.add_argument("--perfil", choices=ORDEN, default="principal")
    args = ap.parse_args()
    p = cargar_perfil(args.perfil)
    suf = sufijo(args.perfil)

    if not shutil.which("ffmpeg"):
        print("ffmpeg no esta instalado: sin el no se pueden juzgar las fotos")
        return 2

    dia = json.loads((ROOT / f"candidatos_del_dia{suf}.json").read_text(encoding="utf-8"))

    # A quien hay que mirarle la cara: los nuevos primero; en la corrida de
    # siembra, los activos que todavia no tienen veredicto.
    siembra = bool(dia.get("primera_corrida"))
    pendientes = list(dia.get("nuevos", []))
    if siembra:
        pendientes += list(dia.get("activos", []))
    else:
        pendientes += [a for a in dia.get("activos", []) if "veredicto_previo" not in a]

    # Se prioriza lo que de verdad puede competir para ESTE perfil: el minimo
    # de recamaras que el perfil pide, y estacionamiento (que los dos exigen).
    minrec = p["min_recamaras"]

    def serio(a):
        return ((a.get("recamaras") or 0) >= minrec and
                (a.get("estacionamientos") or 0) >= 1)
    pendientes.sort(key=lambda a: (not serio(a), a.get("zona_prioridad", 99),
                                   a.get("precio") or 0))
    # En la siembra solo se le miran las fotos a los que de verdad compiten:
    # gastar contexto en un monoambiente sin estacionamiento no sirve de nada.
    tope = p["max_fotos_anuncios"]
    if siembra:
        pendientes = [a for a in pendientes if serio(a)][:max(1, tope * 4 // 5)]
        por_anuncio = POR_ANUNCIO_SIEMBRA
    else:
        pendientes = pendientes[:tope]
        por_anuncio = POR_ANUNCIO_NUEVO

    FOTOS.mkdir(exist_ok=True)
    indice, bajadas = {}, 0
    for a in pendientes:
        aid = a["id"]
        urls = []
        if a.get("foto"):
            urls.append(a["foto"])
        for u in (a.get("fotos") or []):
            if u not in urls:
                urls.append(u)
        urls = urls[:por_anuncio]

        archivos = []
        for i, u in enumerate(urls):
            destino = FOTOS / f"{aid}_{i}.jpg"
            if destino.exists() and destino.stat().st_size > 0:
                archivos.append(str(destino))
                continue
            print(f"  {aid} foto {i+1}/{len(urls)}")
            if convertir(u, destino):
                archivos.append(str(destino))
                bajadas += 1
        if archivos:
            indice[aid] = archivos

    (ROOT / f"fotos_del_dia{suf}.json").write_text(
        json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.perfil}: anuncios con fotos={len(indice)} imagenes nuevas={bajadas}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
