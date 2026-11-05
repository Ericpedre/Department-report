#!/usr/bin/env python3
"""
cache.py — guarda los veredictos del dia en estado.db.

Sin esto, cada corrida vuelve a juzgar los ~96 anuncios de siempre y a mirar
todas las fotos de nuevo. Medido: $4.02 y 61 turnos en una sola corrida, o sea
unos $120/mes para volver a opinar lo mismo. Un anuncio ya juzgado no cambia de
cara: se opina una vez y se recuerda.

Corre despues de criterio.sh y antes de render.py, una vez por perfil.

Los veredictos van con clave (id, perfil): un mismo anuncio puede caer en los
dos perfiles y juzgarse distinto en cada uno, porque la vara no es la misma.
"""
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

from perfiles import ORDEN, perfil as cargar_perfil

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="guarda los veredictos de un perfil")
    ap.add_argument("--perfil", choices=ORDEN, default="principal")
    args = ap.parse_args()

    vp = ROOT / cargar_perfil(args.perfil)["veredictos"]
    if not vp.exists():
        print(f"no hay {vp.name} que guardar")
        return 0
    v = json.loads(vp.read_text(encoding="utf-8"))
    hoy = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    cx = sqlite3.connect(ROOT / "estado.db")
    n = 0
    for x in v.get("veredictos", []):
        if not x.get("id"):
            continue
        cx.execute(
            "INSERT OR REPLACE INTO veredictos (id,perfil,fecha,clasificacion,razon) "
            "VALUES (?,?,?,?,?)",
            (str(x["id"]), args.perfil, hoy, x.get("clasificacion"), x.get("razon")))
        n += 1
    cx.commit()
    total = cx.execute("SELECT COUNT(*) FROM veredictos WHERE perfil=?",
                       (args.perfil,)).fetchone()[0]
    print(f"{args.perfil}: veredictos guardados: {n} (cache del perfil: {total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
