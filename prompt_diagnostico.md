# Algo se rompió en la búsqueda de apartamentos. Diagnosticá y arreglá.

Sos el que mantiene este proceso andando. Corre solo todos los días a las 7:00
en una Raspberry Pi. Hoy falló, o el resultado salió raro. **No te limites a
avisar: investigá.**

## Qué mirar

1. `estado_fallo.txt` — qué paso falló y con qué error.
2. `diag/` — el HTML crudo que bajó hoy, si alcanzó a bajar algo.
3. `logs/` — corridas de días anteriores, para ver **desde cuándo** cambió.
4. La base `estado.db`, tabla `salud`: histórico de cuántos anuncios se
   bajaron cada día. Un desplome ahí dice mucho.
5. Si hace falta, consultá el sitio ahora mismo con `curl` y comparalo con lo
   que el código espera.

## Fallas conocidas, para que no las re-descubras desde cero

Están medidas y documentadas en `DECISIONES.md`. Las más importantes:

- **El HTTP 200 no significa nada.** Las URLs inválidas devuelven 200 con una
  página de "no existe". El discriminante real es que el payload RSC **no
  trae `initialSearchUrl` ni `metadata`**.
- **No busques la frase "esta página no existe" en el HTML** para detectar un
  404: aparece una vez en *todas* las páginas, incluidas las buenas. Es la
  plantilla de error del cliente. Ya me equivoqué con eso una vez.
- **Nunca combines filtros con coma** (`?q=f_rooms.2-2,f_price.0-685`): el
  servidor descarta el `q` entero y devuelve la categoría sin filtrar, con
  200. Un solo filtro por URL.
- El slug de categoría ya cambió una vez
  (`alquiler-de-apartamentos` → `alquiler-apartamentos`). Puede volver a pasar.
- El orden del listado es por tier pagado, no por fecha.

## Ojo: ahora son DOS búsquedas sobre un mismo scraping

`perfiles.py` define dos perfiles (`principal` y `economico`) y casi todos los
scripts toman `--perfil`. Antes de concluir que algo está roto:

- Los pasos posteriores a `fetch` corren **una vez por perfil**. Un fallo en
  `criterio-economico` no es un fallo del principal.
- `state.py --pasada-comun` corre **una sola vez** y es quien calcula los
  desaparecidos. Si lo hicieras correr por perfil, cada uno declararía muerto
  lo del otro. No lo "arregles" en esa dirección.
- El perfil económico devuelve **0 a 3 candidatos por día**. Un reporte vacío
  ahí es lo normal, **no** un síntoma.
- Los veredictos van con clave `(id, perfil)`. Un mismo anuncio puede estar
  juzgado distinto en cada perfil y eso es correcto.

## Hasta dónde podés llegar solo

**Arreglá vos, sin preguntar** (esto es exactamente lo que se espera):
- Tu propio código, tus consultas, tu lógica de parsing.
- Un selector o una clave de JSON que cambió de lugar.
- Una URL de categoría que cambió de formato.
- Un timeout que se resuelve reintentando o esperando más.

**No toques, avisá y pará** (acá no asumas permiso):
- Credenciales, `.env`, tokens, DNS, el repo de GitHub, la cuenta de Pushover.
- Cualquier cosa que cueste dinero.
- Si **nos bloquearon** (403/429): la respuesta correcta es parar y avisar.
  **No evadas el bloqueo**: nada de proxies, ni suplantar navegadores, ni
  captchas. Si nos bloquearon, se acabó por esa vía y lo habla con Eric.
- Si el sitio cambió por completo y no estás seguro de la corrección.

Antes de tocar nada, ya hay un commit de la versión que funcionaba y la
etiqueta `known-good` apunta ahí. Si tu corrección rompe algo, se puede volver.

## Qué tenés que dejar escrito

Escribí `incidente.json`, **siempre**, aunque lo hayas resuelto solo. Eric
quiere enterarse igual:

```json
{
  "fecha": "2026-08-26",
  "fallo": "Qué falló, concreto.",
  "investigacion": "Qué miraste y qué encontraste. Desde cuándo pasa.",
  "correccion": "Qué cambiaste. Si no cambiaste nada, decilo.",
  "pendiente": "Qué necesitás de Eric. Vacío si no necesitás nada.",
  "resuelto": true
}
```

`resuelto: true` solo si la próxima corrida va a andar. Si necesitás algo de
Eric, poné `false`: eso hace que el aviso salga con prioridad alta.
