#!/usr/bin/env bash
# install.sh — deja el buscador programado a las 7:00 en una Pi limpia.
#
# Enlaza (no copia) las unidades del repo a ~/.config/systemd/user/, para que
# haya UNA sola fuente de verdad: se edita systemd/*.service aca y basta un
# `systemctl --user daemon-reload`. Es idempotente: se puede correr de nuevo.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DESTINO="$HOME/.config/systemd/user"
ESPERADO="$HOME/Proyects/loopsAi"

# Las unidades usan %h/Proyects/loopsAi. Si el repo esta en otro lado, los
# enlaces quedarian bien pero apuntando a rutas que no existen: mejor parar
# aca y decirlo, que descubrirlo el dia que no llegue el reporte.
if [ "$REPO" != "$ESPERADO" ]; then
    echo "ERROR: el repo esta en $REPO pero las unidades esperan $ESPERADO." >&2
    echo "  Mové el repo, o cambiá las rutas en systemd/apartamentos.service." >&2
    exit 1
fi

mkdir -p "$DESTINO" "$REPO/logs"

for unidad in apartamentos.service apartamentos.timer venta.service venta.timer; do
    ln -sfn "$REPO/systemd/$unidad" "$DESTINO/$unidad"
    echo "enlazado: $DESTINO/$unidad -> $REPO/systemd/$unidad"
done

systemctl --user daemon-reload
systemctl --user enable --now apartamentos.timer
# Crawl semanal de venta: alimenta el almacen de mercado, no el reporte diario.
systemctl --user enable --now venta.timer

# El gestor de usuario debe arrancar al bootear aunque nadie inicie sesion;
# sin esto el timer no existiria hasta el primer login.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "activando linger (el gestor de usuario debe correr sin login)"
    loginctl enable-linger "$USER" || {
        echo "OJO: no se pudo activar linger. Corré: sudo loginctl enable-linger $USER" >&2
    }
fi

# Un solo disparador: si quedo el job viejo en cron, correria dos veces.
if crontab -l 2>/dev/null | grep -q '^[^#]*run\.sh'; then
    echo "OJO: todavia hay un job de cron para run.sh. Sacalo con 'crontab -e'." >&2
fi

echo
systemctl --user list-timers apartamentos.timer venta.timer --no-pager
