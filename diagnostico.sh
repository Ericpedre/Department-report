#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
# Mismo criterio que criterio.sh: modelo fijado, no heredado.
exec claude -p "$(cat prompt_diagnostico.md)" \
    --model claude-opus-5 \
    --allowedTools Read Write Edit Bash Glob Grep \
    --permission-mode acceptEdits \
    --output-format json
