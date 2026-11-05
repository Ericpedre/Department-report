#!/usr/bin/env bash
# condicion.sh — decide si el disparo de hoy del timer se convierte en corrida.
#
# Va como ExecCondition= de apartamentos.service: exit 0 = corre, exit 1 = se
# saltea limpio (systemd no lo cuenta como fallo, no hay autodiagnostico ni
# aviso). El timer sigue disparando todos los dias entre 7 y 8; este script es
# el que aplica la pausa y el intervalo de programacion.conf.
#
# Lo usan las dos unidades, con un argumento:
#   condicion.sh            buscador diario (apartamentos.service)
#   condicion.sh venta      crawl semanal de venta (venta.service)
# La PAUSA es una sola y vale para las dos: es el sitio el que nos bloqueo, no
# una busqueda. El intervalo y la marca son de cada una.
#
# Corrida manual que ignora todo esto: ./run.sh o ./venta.py directo.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$REPO/programacion.conf"

PAUSA_HASTA=""
INTERVALO_DIAS=1
INTERVALO_VENTA_DIAS=1
# shellcheck source=/dev/null
[ -f "$CONF" ] && . "$CONF"

if [ "${1:-}" = "venta" ]; then
    MARCA="$REPO/logs/.ultima_venta"
    INTERVALO_DIAS=$INTERVALO_VENTA_DIAS
else
    MARCA="$REPO/logs/.ultima_corrida"
fi

HOY=$(date +%F)

# Comparacion de fechas ISO como texto: YYYY-MM-DD ordena igual que la fecha.
if [ -n "$PAUSA_HASTA" ] && [[ ! "$HOY" > "$PAUSA_HASTA" ]]; then
    echo "$(date '+%F %T') pausa hasta $PAUSA_HASTA: hoy no se corre"
    exit 1
fi

if [ -f "$MARCA" ]; then
    ultima=$(cat "$MARCA")
    dias=$(( ( $(date -d "$HOY" +%s) - $(date -d "$ultima" +%s) ) / 86400 ))
    if [ "$dias" -lt "$INTERVALO_DIAS" ]; then
        echo "$(date '+%F %T') ultima corrida $ultima (hace $dias d), intervalo $INTERVALO_DIAS d: hoy no se corre"
        exit 1
    fi
fi

# Se marca al AUTORIZAR, no al terminar: un dia bloqueado o fallido tambien
# gasta su turno. Si no, un 403 haria que se reintente al dia siguiente y el
# intervalo dejaria de espaciar justo cuando mas importa.
mkdir -p "$REPO/logs"
echo "$HOY" > "$MARCA"
echo "$(date '+%F %T') corrida autorizada (intervalo $INTERVALO_DIAS d)"
exit 0
