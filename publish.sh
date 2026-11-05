#!/usr/bin/env bash
# publish.sh — empuja el reporte a GitHub Pages.
#
# El repo de publicacion es SEPARADO del repo de codigo: en publish/ vive un
# checkout del repo de GitHub Pages, y aca solo se commitea el HTML del dia.
# Asi el codigo (y sobre todo .env) nunca viaja al repo publico.
#
# La autenticacion es por DEPLOY KEY SSH, no por token: los PAT caducan y
# romperian esto en silencio dentro de unos meses.
set -uo pipefail
cd "$(dirname "$0")/publish" || { echo "no existe publish/"; exit 1; }

if [ ! -d .git ]; then
    echo "publish/ todavia no es un repo git; falta configurar GitHub Pages"
    exit 1
fi

git add -A
if git diff --cached --quiet; then
    echo "sin cambios que publicar"
    exit 0
fi
git commit -qm "reporte $(date +%F)"

publicado=0
for i in 1 2 3; do
    if git push -q origin HEAD 2>&1; then
        echo "publicado"
        publicado=1
        break
    fi
    echo "push fallo (intento $i/3); espero"
    sleep 20
done
[ "$publicado" = 1 ] || { echo "no se pudo publicar despues de 3 intentos"; exit 1; }

# Que el push nunca lleve un enlace muerto: si Pages no responde, se avisa.
# Paso una vez de verdad: el repo tenia todo pero Pages no estaba activado,
# asi que el contenido existia y la URL daba 404.
URL=$(grep -E '^REPORTE_URL=' ../.env 2>/dev/null | cut -d= -f2- | tr -d '"' | sed 's:/*$::')
if [ -n "$URL" ]; then
    sleep 10   # Pages tarda un poco en servir el commit nuevo

    # Rutas publicas de cada perfil, sacadas de perfiles.py para que no haya
    # una segunda lista que mantener a mano.
    RUTAS=$(cd .. && python3 -c "
from perfiles import ORDEN, PERFILES
for n in ORDEN:
    s = PERFILES[n]['salida']
    print(n, f'/{s}/latest/' if s else '/latest/')
")

    falla_principal=0
    while read -r pf ruta; do
        [ -z "$pf" ] && continue
        CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 25 "$URL$ruta")
        if [ "$CODE" = "200" ]; then
            echo "verificado: $URL$ruta responde 200"
        else
            echo "OJO: $URL$ruta devuelve HTTP $CODE"
            echo "  el reporte de '$pf' se subio, pero la URL publica no lo sirve."
            echo "  revisar que GitHub Pages este activado (Settings > Pages)."
            # Solo el principal hace fallar la publicacion: que el economico
            # todavia no tenga pagina (primer dia) no es motivo para dar la
            # corrida entera por rota.
            [ "$pf" = "principal" ] && falla_principal=1
        fi
    done <<< "$RUTAS"

    [ "$falla_principal" = 1 ] && exit 2
fi
exit 0
