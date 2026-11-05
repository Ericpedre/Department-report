#!/usr/bin/env python3
"""
venta.py — crawl SEMANAL de apartamentos en venta. MECANICO, como todo lo demas.

Por que existe: la busqueda diaria mide alquileres, o sea el NUMERADOR de una
inversion (cuanto renta y que tan rapido se coloca). Sin precio de venta no hay
denominador, y sin denominador no hay rentabilidad: solo precios sueltos.

Por que semanal y no diario:
  - Los precios de venta se mueven por mes, no por dia. Un crawl diario pagaria
    155 paginas para ver el mismo numero.
  - 155 paginas a 3 s son ~8 minutos. Metido en la corrida de las 7:00 alargaria
    el camino critico que le manda el reporte a Eric, para nada.
Corre los domingos a las 9:00, con su propio timer y su propio flock.

No escribe candidatos.json ni toca estado.db: su unica salida es mercado.db.
Todo el trabajo pesado lo hace fetch.recorrer(), que ya trae las guardas caras:
soft-404 con HTTP 200, el filtro de precio que se cae en silencio, y el 403 que
se respeta en vez de reintentarse.
"""
import argparse, json, sqlite3, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

import mercado
from fetch import recorrer
from perfiles import FILTROS_VENTA

ROOT = Path(__file__).resolve().parent
CRUDO = ROOT / "venta_crudo.json"

# 155 paginas medidas el 2026-09-18 (3097 anuncios <= $150k). El tope de 40 que
# usa el diario dejaria fuera tres cuartos del inventario en silencio.
MAX_PAGES = 200

# Un apartamento de $8000 no existe; uno de $140 000 es lo normal. Sin esto,
# la guarda de sanidad marcaria los 3097 como "precios fuera de rango".
RANGO_PRECIO = (5_000, 2_000_000)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="por defecto hoy (hora local)")
    ap.add_argument("--max-paginas", type=int, default=MAX_PAGES,
                    help="tope de seguridad; bajalo para probar sin barrer todo")
    ap.add_argument("--estricto", action="store_true",
                    help="devolver != 0 si falla (asi corren las pruebas)")
    args = ap.parse_args()

    fecha = args.fecha or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    print(f"===== venta {fecha} ({args.max_paginas} pags max) =====")

    try:
        anuncios, salud = recorrer(FILTROS_VENTA,
                                   max_pages=args.max_paginas,
                                   rango_precio=RANGO_PRECIO)

        # Se deja el crudo en disco ANTES de ingestar: si la ingesta se rompe,
        # las 155 paginas que ya se bajaron no se tiran a la basura.
        #
        # Pero NO se pisa un crudo bueno con uno vacio. Con el bloqueo de
        # Cloudflare puesto, `recorrer` vuelve con 0 anuncios y error_fatal en
        # la PRIMERA request, y escribir eso borraria el ultimo crudo bueno.
        # Hoy (2026-10-18) no habria costado nada —el del 2026-09-18 ya estaba
        # en historico/venta/—, pero el camino de perdida real existe: si una
        # semana la ingesta falla, el crudo queda SIN archivar y es la unica
        # copia de esas 155 paginas; el domingo siguiente, bloqueado, lo
        # borraba. Con anuncios en la mano se escribe igual, aunque el crawl
        # haya salido incompleto: eso es justamente lo que el crudo protege.
        if anuncios or not salud["error_fatal"]:
            CRUDO.write_text(json.dumps(
                {"salud": salud, "anuncios": list(anuncios.values())},
                ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            print(f"  crudo NO escrito: 0 anuncios y error fatal; se conserva "
                  f"{CRUDO.name} anterior intacto")

        print(f"anuncios={len(anuncios)} requests={salud['requests']} "
              f"avisos={len(salud['avisos'])} bloqueado={salud['bloqueado']}")
        for w in salud["avisos"][:10]:
            print("  aviso:", w)

        if salud["error_fatal"]:
            # Lo mismo que hace el diario: un crawl roto no se guarda, porque
            # quedaria un domingo con 0 anuncios que despues se leeria como
            # "se vendio todo el inventario de Panama".
            print("  ERROR FATAL:", salud["error_fatal"])
            return 2

        cx = sqlite3.connect(mercado.DB)
        try:
            mercado.init_db(cx)
            r = mercado.ingestar(cx, list(anuncios.values()), salud, "venta", fecha)
        finally:
            cx.close()

        ruta, tam = mercado.archivar(CRUDO, "venta", fecha)
        print(f"mercado[venta] {fecha}: ingestados={r['ingestados']} "
              f"en_base={r['en_base']} nuevos={r['nuevos']} "
              f"revividos={r['revividos']} archivo={ruta.name} ({tam // 1024} KB)")
        return 0

    except Exception as e:
        print(f"venta: FALLO: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1 if args.estricto else 0


if __name__ == "__main__":
    sys.exit(main())
