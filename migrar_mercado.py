#!/usr/bin/env python3
"""
migrar_mercado.py — arrastra a mercado.db lo poco que se salvo de antes.

Corre UNA vez. Es idempotente, asi que volver a correrlo no duplica nada.

QUE SE PUEDE RECUPERAR Y QUE NO. El scraping rico (los ~502 anuncios diarios con
25 campos) nunca se guardo: candidatos.json se sobrescribe todas las mananas.
Eso se perdio y no vuelve. Lo que si sobrevivio en estado.db:

  - 180 anuncios de las 9 zonas de Eric, con 9 campos y sus fechas de vida
  - 2422 observaciones de precio, una por anuncio y dia
  - 25 dias de salud del crawl

O sea: se recupera la VENTANA DE VIDA (que es lo caro de esperar, 25 dias) pero
no los atributos (metros2, publicado, tier, anunciante...). Por eso las filas
migradas quedan marcadas `origen='estado'`: sirven para medir cuanto tarda un
anuncio en darse de baja, NO para cruzar eso contra caracteristicas.

Un detalle que importa: sin `publicado`, estas filas arrancan su reloj el dia que
las vimos, no el dia que se publicaron. Eso las hace parecer mas rapidas de lo
que fueron. La serie honesta arranca con el primer dia de mercado.py.
"""
import json, sqlite3, sys
from pathlib import Path

import mercado

ROOT = Path(__file__).resolve().parent
ESTADO = ROOT / "estado.db"

# Caida dia contra dia del total bajado que hace sospechar de un crawl cortado.
# Medido sobre los 25 dias: todos varian menos del 3% salvo el 2026-09-02, que
# cayo 15.2% en anuncios y 13.8% en requests a la vez — y registro 26
# desaparecidos contra una base de 2-5. Es el unico dia que esta regla descarta.
CAIDA_SOSPECHOSA = -0.10


def main():
    if not ESTADO.exists():
        print("no hay estado.db; nada que migrar")
        return 0

    src = sqlite3.connect(ESTADO)
    cx = sqlite3.connect(mercado.DB)
    mercado.init_db(cx)

    # --- salud -> crawl -----------------------------------------------------
    # estado.db no guardo `total_declarado` por categoria, asi que no se puede
    # calcular `completo` como lo hace mercado.py. Se usa el unico proxy que hay:
    # una caida brusca del total bajado. Queda en una categoria aparte para que
    # `calidad` muestre que estos dias tienen informacion mas pobre.
    salud = list(src.execute("SELECT fecha, anuncios, requests FROM salud ORDER BY fecha"))
    n_incompletos = []
    prev = None
    for fecha, anuncios, requests in salud:
        completo = 1
        if prev and prev > 0 and (anuncios - prev) / prev < CAIDA_SOSPECHOSA:
            completo = 0
            n_incompletos.append(fecha)
        cx.execute("INSERT OR IGNORE INTO crawl VALUES (?,?,?,?,?,?,?,?,?)",
                   (fecha, "alquiler", "(historico estado.db)", None, anuncios,
                    None, requests, completo,
                    json.dumps(["migrado de estado.db: sin total_declarado por categoria"])))
        prev = anuncios

    # --- anuncios -> listing ------------------------------------------------
    n_l = 0
    for r in src.execute("""SELECT id, primera_vez, ultima_vez, desaparecido,
                                   precio_inicial, precio_actual, titulo, zona, url
                            FROM anuncios"""):
        aid, prim, ult, desap, pini, pact, tit, zona, url = r
        # INSERT OR IGNORE: si mercado.py ya vio este anuncio hoy, su fila es
        # mejor (tiene los 25 campos). La migracion nunca pisa lo bueno con lo malo.
        cur = cx.execute("""INSERT OR IGNORE INTO listing
            (id, operacion, titulo, url, zona, primera_vez, ultima_vez,
             precio_inicial, precio_actual, precio_min, origen)
            VALUES (?,'alquiler',?,?,?,?,?,?,?,?, 'estado')""",
            (aid, tit, url, zona, prim, ult, pini, pact, min(pini or 0, pact or 0) or None))
        n_l += cur.rowcount
        # La ventana de vida SI se completa aunque la fila ya existiera: es el
        # dato caro (25 dias de espera) y mercado.py solo conoce desde hoy.
        cx.execute("""UPDATE listing
                      SET primera_vez = MIN(primera_vez, ?),
                          precio_inicial = COALESCE(?, precio_inicial),
                          precio_min = MIN(COALESCE(precio_min, ?), ?)
                      WHERE id=?""", (prim, pini, pini, pini or 1e12, aid))

    # --- precios -> observacion ---------------------------------------------
    n_o = 0
    for aid, fecha, precio in src.execute("SELECT id, fecha, precio FROM precios"):
        cur = cx.execute("INSERT OR IGNORE INTO observacion VALUES (?,?,?,NULL,NULL)",
                         (aid, fecha, precio))
        n_o += cur.rowcount

    cx.commit()
    tot_l = cx.execute("SELECT COUNT(*) FROM listing").fetchone()[0]
    tot_o = cx.execute("SELECT COUNT(*) FROM observacion").fetchone()[0]
    src.close(); cx.close()

    print(f"migrados: listing +{n_l} (total {tot_l}), observacion +{n_o} (total {tot_o}), "
          f"crawl {len(salud)} dias")
    print(f"dias marcados INCOMPLETOS por la regla de caida {CAIDA_SOSPECHOSA:.0%}: "
          f"{', '.join(n_incompletos) or 'ninguno'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
