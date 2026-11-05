#!/usr/bin/env bash
# run.sh — la corrida diaria completa. Esto es lo que dispara el timer a las 7:00.
#
# Orden: bajar -> comparar contra ayer (UNA vez) -> y despues, POR CADA PERFIL:
#        recortar -> fotos -> CRITERIO (Claude) -> cachear -> reporte.
#        Al final: publicar una vez, y un aviso por perfil.
#
# Hay DOS busquedas sobre un mismo scraping (ver perfiles.py):
#   principal  el reemplazo equivalente del apartamento de Eric
#   economico  el plan B barato: 1 rec, <= $350, casas incluidas
#
# Si algo se rompe, no se muere en silencio: se invoca a Claude en modo
# diagnostico, y pase lo que pase se publica un reporte y se manda el aviso.
# Eric prefiere saber que corrio a no recibir nada y no saber si fallo.
set -uo pipefail
cd "$(dirname "$0")"

HOY=$(date +%F)
LOG="logs/${HOY}.log"
mkdir -p logs diag publish
exec > >(tee -a "$LOG") 2>&1
echo "===== corrida $(date '+%F %T %Z') ====="

PERFILES=$(python3 -c "from perfiles import ORDEN;print(' '.join(ORDEN))")
echo "perfiles: $PERFILES"

# Una sola corrida a la vez. Si ayer quedo colgada, no se encima otra.
exec 9>/tmp/apartamentos.lock
if ! flock -n 9; then echo "ya hay una corrida en curso; salgo"; exit 0; fi

# La Pi tiene 905 MB y el escritorio se come buena parte. Claude necesita
# ~250-400 MB. Si no hay aire, se espera en vez de morir por OOM.
esperar_ram() {
    for i in 1 2 3 4 5 6; do
        libre=$(free -m | awk 'NR==2{print $7}')
        [ "$libre" -ge 320 ] && return 0
        echo "  RAM disponible ${libre}MB; espero 60s (intento $i/6)"
        sleep 60
    done
    echo "  sigo sin RAM (${libre}MB), lo intento igual"
    return 0
}

# FALLO = se rompio algo COMPARTIDO (fetch o la pasada comun). Sin eso no hay
# nada que reportar en ningun perfil, asi que dispara el autodiagnostico.
FALLO=""
# FALLOS_PERFIL = se rompio una via sola. La otra tiene que publicar igual.
FALLOS_PERFIL=""

paso() {
    local nombre="$1"; shift
    echo "--- $nombre ---"
    if ! "$@"; then FALLO="$nombre"; echo "FALLO en $nombre"; return 1; fi
    return 0
}

# Igual que `paso`, pero NO marca la corrida entera como rota: anota el perfil
# y sigue. Que el economico falle no puede dejar a Eric sin el reporte
# principal, que es el que de verdad le importa.
paso_perfil() {
    local nombre="$1"; shift
    echo "--- $nombre ---"
    if ! "$@"; then
        FALLOS_PERFIL="$FALLOS_PERFIL $nombre"
        echo "FALLO en $nombre (sigo con lo demas)"
        return 1
    fi
    return 0
}

# Paso ACCESORIO: no marca la corrida como rota, no dispara autodiagnostico y
# NUNCA corta la cadena. El almacen de mercado (mercado.py) alimenta el estudio
# de compra, que es del ano que viene; el reporte de hoy vale mas que un dia de
# datos. Si se cae, se anota en el log y se sigue como si nada.
paso_accesorio() {
    local nombre="$1"; shift
    echo "--- $nombre ---"
    "$@" || echo "$nombre fallo (es accesorio, la corrida sigue)"
    return 0
}

# Toda la via de un perfil. Si se cae en la mitad, se corta SOLO esta via.
correr_perfil() {
    local pf="$1"
    echo "===== perfil: $pf ====="
    paso_perfil "state-$pf"    python3 state.py --perfil "$pf" || return 1
    paso_perfil "photos-$pf"   python3 photos.py --perfil "$pf" || return 1
    esperar_ram
    # OJO con la redireccion: tiene que envolver SOLO a criterio.sh. Si envuelve
    # al helper, el "--- criterio ---" del echo cae adentro del .json y lo deja
    # imparseable. Ese archivo es para depurar a mano, y asi no servia.
    echo "--- criterio-$pf ---"
    if ! ./criterio.sh "$pf" > "/tmp/criterio_out_${pf}.json"; then
        FALLOS_PERFIL="$FALLOS_PERFIL criterio-$pf"
        echo "FALLO en criterio-$pf (sigo con lo demas)"
        return 1
    fi
    paso_perfil "cache-$pf"    python3 cache.py --perfil "$pf" \
        || echo "no se pudo cachear veredictos de $pf"
    paso_perfil "render-$pf"   python3 render.py --perfil "$pf" \
        || echo "el render de $pf fallo"
    return 0
}

# Version que funcionaba, por si la autocorreccion rompe algo.
git add -A >/dev/null 2>&1
git commit -qm "estado antes de la corrida $HOY" >/dev/null 2>&1 || true

# ---- lo compartido: si esto falla, no hay corrida ---------------------------
paso "fetch" python3 fetch.py && \
paso_accesorio "mercado" python3 mercado.py && \
paso "state-comun" python3 state.py --pasada-comun

if [ -z "$FALLO" ]; then
    for pf in $PERFILES; do correr_perfil "$pf"; done
fi

# ---- autodiagnostico -------------------------------------------------------
if [ -n "$FALLO" ]; then
    echo "===== autodiagnostico ====="
    {
        echo "paso que fallo: $FALLO"
        echo "fecha: $HOY"
        echo "--- ultimas lineas del log ---"
        tail -40 "$LOG"
    } > estado_fallo.txt

    esperar_ram
    # Mismo script que se probo a mano, para que produccion y prueba no diverjan.
    ./diagnostico.sh > /tmp/diag_out.json 2>&1 || true

    # Si el diagnostico dejo el sistema andando, se reintenta la corrida.
    if [ -f incidente.json ] && grep -q '"resuelto": *true' incidente.json; then
        echo "el diagnostico dice que lo resolvio; reintento la corrida"
        FALLO=""
        paso "fetch-retry" python3 fetch.py && \
        paso_accesorio "mercado-retry" python3 mercado.py && \
        paso "state-comun-retry" python3 state.py --pasada-comun
        if [ -z "$FALLO" ]; then
            for pf in $PERFILES; do correr_perfil "$pf"; done
        fi
    fi

    # El diagnostico tiene que dejar incidente.json SIEMPRE, resuelto o no. Si
    # no lo dejo (se quedo sin RAM, se colgo, o quien lo escribio se lo olvido),
    # antes se seguia de largo y el aviso de abajo salia por el camino del DIA,
    # armado con el candidatos_del_dia*.json viejo que state.py no alcanzo a
    # reescribir. Paso de verdad el 2026-10-28: la corrida murio en el 403 de
    # Cloudflare y a Eric le llegaron dos push armados con el reporte del
    # 2026-09-22 —justo el dia del falso positivo de 45 desaparecidos— uno de
    # ellos titulado "volvi tras un silencio", como si la busqueda hubiera
    # vuelto. Llevaba 35 dias caida.
    #
    # Un aviso que miente es peor que no avisar, pero callarse tampoco sirve:
    # se arma un incidente minimo con lo que ya se sabe y el aviso sale por el
    # camino del incidente, que dice la verdad —se rompio y nadie lo reviso— y
    # ademas sale con prioridad alta por tener resuelto en false.
    if [ ! -f incidente.json ]; then
        echo "el diagnostico no dejo incidente.json; armo uno minimo"
        python3 - "$FALLO" "$HOY" > incidente.json <<'PY'
import json, pathlib, sys

fallo, hoy = sys.argv[1], sys.argv[2]
try:
    crudo = pathlib.Path("estado_fallo.txt").read_text(encoding="utf-8")[:1500]
except Exception:
    crudo = "(no se pudo leer estado_fallo.txt)"
json.dump({
    "fecha": hoy,
    "fallo": f"Fallo el paso '{fallo}' y el autodiagnostico no dejo "
             "incidente.json: nadie diagnostico esta corrida.",
    "investigacion": "Sin investigar: el diagnostico no llego a escribir su "
                     f"informe. Lo crudo esta en estado_fallo.txt y en "
                     f"logs/{hoy}.log.\n--- estado_fallo.txt ---\n" + crudo,
    "correccion": "Ninguna. Este incidente lo escribio run.sh para no avisar "
                  "con un reporte viejo, no el diagnostico.",
    "pendiente": f"Revisa a mano por que fallo '{fallo}' y por que el "
                 "autodiagnostico no dejo informe.",
    "resuelto": False,
}, sys.stdout, ensure_ascii=False, indent=1)
PY
    fi

    git add -A >/dev/null 2>&1
    git commit -qm "autocorreccion $HOY tras fallo en $FALLO" >/dev/null 2>&1 || true
fi

# ---- publicar y avisar: pase lo que pase -----------------------------------
# Una sola publicacion: publish.sh empuja todo el arbol, los dos reportes juntos.
paso "publicar" ./publish.sh || echo "no se pudo publicar"

aviso_ok=1
if [ -f incidente.json ]; then
    # Un incidente rompe la corrida entera, no un perfil: se avisa UNA vez.
    python3 notify.py --incidente
    aviso_ok=$?
else
    # Un aviso por perfil, cada uno con su enlace. Si el reporte de un perfil
    # no se llego a generar, se saltea su aviso en vez de mandar uno vacio.
    for pf in $PERFILES; do
        suf=$(python3 -c "import sys;from perfiles import sufijo;print(sufijo(sys.argv[1]))" "$pf")
        if [ ! -f "candidatos_del_dia${suf}.json" ]; then
            echo "sin reporte de $pf; no mando aviso"
            continue
        fi
        # Que el archivo exista no quiere decir que sea de hoy: si la via del
        # perfil se corto antes de state.py, queda el del ultimo dia bueno y el
        # aviso lo cuenta como si fuera fresco. Arriba ya se cubre el caso de la
        # corrida entera rota; esto cubre el del perfil solo, que no dispara
        # autodiagnostico. Un reporte viejo no se avisa: se dice en el log.
        fecha_rep=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('fecha',''))" \
            "candidatos_del_dia${suf}.json" 2>/dev/null)
        if [ "$fecha_rep" != "$HOY" ]; then
            echo "el reporte de $pf es del ${fecha_rep:-???}, no de hoy; no mando aviso"
            continue
        fi
        echo "--- aviso $pf ---"
        # Basta con que UN aviso salga para considerar que Eric se entero.
        if python3 notify.py --perfil "$pf"; then aviso_ok=0; fi
    done
fi

# Un incidente se cuenta UNA sola vez. Antes nadie archivaba incidente.json,
# asi que se quedaba para siempre: cada corrida siguiente mandaba el aviso
# viejo y lo pintaba en la pagina, tapando el reporte del dia. Paso de verdad
# (el del 2026-08-26 se conto cuatro dias seguidos).
#
# Se archiva DESPUES de render y del aviso, para que el dia del incidente si
# salga en ambos. Y solo si el aviso salio: si Pushover fallo, el incidente
# sigue pendiente de contar y se reintenta manana.
if [ -f incidente.json ] && [ "$aviso_ok" = 0 ]; then
    mkdir -p logs/incidentes
    destino="logs/incidentes/$(date +%F_%H%M%S).json"
    mv incidente.json "$destino"
    echo "incidente archivado en $destino (ya se conto)"
fi

# Si todo salio bien, esta version queda como la buena conocida.
# "Todo" incluye las dos vias: si el economico se rompio, esta version no es
# conocida-buena aunque el principal haya salido perfecto.
if [ -z "$FALLO" ] && [ -z "$FALLOS_PERFIL" ]; then
    git tag -f known-good >/dev/null 2>&1 || true
    echo "corrida OK; known-good actualizado"
elif [ -n "$FALLOS_PERFIL" ]; then
    echo "corrida parcial; fallaron:$FALLOS_PERFIL"
fi
echo "===== fin $(date '+%T') ====="
