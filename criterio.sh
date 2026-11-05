#!/usr/bin/env bash
# criterio.sh — invoca a Claude para la parte de CRITERIO.
# Se aisla en un script para que la linea de comando quede limpia y para poder
# correrlo a mano cuando haga falta depurar:
#
#   ./criterio.sh              -> perfil principal
#   ./criterio.sh economico    -> perfil economico
#
set -uo pipefail
cd "$(dirname "$0")"

PERFIL="${1:-principal}"
# El prompt de cada perfil sale de perfiles.py, para que no haya dos listas de
# archivos que mantener sincronizadas a mano.
PROMPT=$(python3 -c "import sys;from perfiles import perfil;print(perfil(sys.argv[1])['prompt'])" "$PERFIL") || {
    echo "perfil invalido: $PERFIL"; exit 2; }
[ -f "$PROMPT" ] || { echo "falta el prompt $PROMPT"; exit 2; }

# El modelo va fijado, no heredado del default del CLI: si algun dia cambia
# `model` en ~/.claude/settings.json, el buscador cambiaria de modelo en
# silencio y sin rastro en el repo. El juicio sobre los apartamentos es lo
# que da valor a todo esto; no puede depender de una config global.
exec claude -p "$(cat "$PROMPT")" \
    --model claude-opus-5 \
    --allowedTools Read Write Glob \
    --permission-mode acceptEdits \
    --output-format json
