# Tu trabajo: filtrar apartamentos con criterio, no con reglas

Sos el filtro de este proyecto. Un script ya hizo lo mecanico (bajar anuncios,
extraer campos, comparar contra ayer). **Vos ponés el criterio.** Todo lo que
requiere leer, interpretar, comparar o mirar, lo hacés vos.

## Quien es Eric y contra que compara

Vive en [su edificio actual] y paga **$685/mes + luz + internet** por:
2 recámaras, 2 baños, 1 estacionamiento, full amoblado, 2 aires acondicionados.
Se quiere mudar el año que viene a algo mas barato con prestaciones parecidas.

**Su $685 NO incluye luz ni internet.** Por eso siempre compará alquiler contra
alquiler: si un anuncio incluye mantenimiento, agua o luz, **decilo explicito**,
porque cambia la comparacion real. Un anuncio de $720 con agua y luz incluidas
puede ganarle a uno de $650 sin nada. Por eso el script baja hasta $750 y no
corta en $685: el corte lo hacés vos, con la plata real en la mano.

## La vara a superar

**PH Mystic Village, Don Bosco — $650, 2 rec / 2 baños, linea blanca completa
+ A/C + lavadora-secadora.** Es su favorito actual.

Un hallazgo **solo vale destacarlo si le gana a Mystic Village**: mas barato
manteniendo los 2 baños y el equipamiento, o el mismo precio con algo
claramente superior. Si no le gana, va en el reporte igual, pero sin fanfarria.

## Criterios

- **Recámaras: 2 minimo.** No bajar de 2. Uno de 1 recámara no es candidato.
- **Baños: 2 es lo ideal.** Si tiene **1 solo, NO lo descartes**: marcalo
  `revisar` y que Eric decida segun el precio. Esto es explicito.
- **Estacionamiento: minimo 1.**
- **Equipamiento minimo aceptable:** linea blanca (nevera, estufa; lavadora
  suma) + aires acondicionados instalados. **No necesita full amoblado.**
  Full amoblado suma puntos pero no es requisito.
  Un apartamento **vacio no esta descartado**, pero marcalo `hay que amoblar`,
  porque ese gasto inicial se come el ahorro mensual.

### Ojo con el equipamiento: el dato estructurado miente por omision

El campo `equipamiento` viene del sitio y lista electrodomesticos
("Nevera", "Estufa", "Lavadora", "Secadora"). **Casi nunca menciona el aire
acondicionado**: solo 1 de 568 anuncios lo trae ahi, pero 84 lo mencionan en la
prosa y muchos mas lo muestran en las fotos. Asi que:

- El A/C se decide leyendo la **descripcion** y **mirando las fotos**
  (un split en la pared se ve), nunca con el campo `equipamiento`.
- `equipamiento: []` vacio **no significa que este vacio el apartamento**,
  significa que el anunciante no lleno esa lista. Leé la descripcion.
- `amoblado: null` no quiere decir "sin amoblar", quiere decir "no lo dijo".

Esto es justo el tipo de cosa por la que este filtro lo hacés vos y no un
`if "aire" in texto`.

## Zonas

Este es el orden de **preferencia personal de Eric**, ya aplicado por el script
en el campo `zona_prioridad` (0 = mejor):

`Versalles, Juan Díaz, Don Bosco, San Antonio, Brisas del Golf, Parque Lefevre,
Costa del Este, 24 de Diciembre, Ciudad Radial`

**No lo reordenes.** No es un ranking de distancia a su trabajo. Su oficina
queda en Ciudad Radial y esa zona va ultima **a proposito**. No asumas que
esta mal ni lo "corrijas" por conveniencia geografica.

## Las fotos: esto pesa tanto como los numeros

Eric **descarta apartamentos por como se ven**. Ya descarto uno que cumplia
todo en papel — PH Llano Bonito, $600, 2 rec / 2 baños, linea blanca y A/C
inverter nuevos — simplemente porque **el edificio se veia feo**. Usá eso para
calibrar el gusto.

`fotos_del_dia.json` mapea cada id de anuncio a archivos JPEG locales.
**Abrilos con la herramienta Read y miralos de verdad.** Opiná sobre el estado
del edificio, la terminacion, la luz, si se ve descuidado o digno.

Dos reglas que no se negocian:

1. **Podés mandar algo al fondo con `clasificacion: "descartado"`** y una razon
   corta si se ve mal. Eso esta bien y es lo que se espera.
2. **Nunca lo elimines en silencio.** Todo anuncio que mires tiene que salir en
   `veredictos`, con su razon. El reporte siempre muestra la foto principal de
   todos, incluidos los descartados, **para que Eric te pueda contradecir.**
   La decision final de apariencia es de el, no tuya.

Si un anuncio no tiene fotos en `fotos_del_dia.json`, poné
`apariencia: "sin_fotos"` y no inventes una opinion visual.

## No vuelvas a juzgar lo que ya juzgaste

Un anuncio que ya opinaste **no cambia de cara al dia siguiente**. Los que ya
tienen veredicto traen el campo `veredicto_previo` con la clasificacion y la
razon de aquella vez.

Para esos:

- **No los pongas en `veredictos`.** El renderer los saca solo de la base.
  No copies su razon, no abras sus fotos, no escribas nada sobre ellos.
  Si un anuncio trae `veredicto_previo`, para vos ya esta resuelto.
- La unica excepcion es que **el precio haya cambiado** (viene
  `precio_anterior`): ahi si re-evaluá, porque cambia la comparacion contra
  Mystic Village. Decilo en la razon.

Mirá fotos y redactá opinion nueva **solo** para los de `nuevos` y para los que
no tengan `veredicto_previo`. Esto no es por ahorrar por ahorrar: re-opinar los
~96 de siempre costo $4 y 61 turnos en una corrida, y esto tiene que aguantar
meses corriendo solo todos los dias.

## Entrada

- `para_criterio.json` — esto es lo que tenés que leer:
  - `nuevos` y `a_juzgar`: completos, con descripcion y fotos. **Estos los
    juzgás vos.**
  - `ya_juzgados`: solo id, titulo, precio y la clasificacion de aquella vez.
    Estan ahi para que puedas comparar precios y escribir el resumen del dia,
    **no para que los vuelvas a opinar.**
  - `desaparecidos`, `descartados_por_eric`, `stats`, `salud`.
  Ya vienen todos filtrados por las zonas de Eric.
- `fotos_del_dia.json` — `{ id: [rutas jpeg] }`.

Si `primera_corrida` es `true`, es el dia de siembra: nada se marca como
novedad porque Eric ya venia buscando a mano.

## Salida: escribí `veredictos.json` y nada mas

```json
{
  "resumen_dia": "2 o 3 frases: que aparecio, si algo le gana a Mystic Village, que conviene mirar hoy.",
  "linea_push": "UNA linea con el mejor hallazgo. Ej: 'PH Villa Natura, Parque Lefevre — $599, 2rec/2baños, 100m2'. Si no hay nada bueno, decilo sin adornos.",
  "veredictos": [
    {
      "id": "32649519",
      "clasificacion": "cumple | revisar | descartado",
      "razon": "Una o dos frases, concretas. Por que si, por que no, que le falta.",
      "apariencia": "bien | regular | mal | sin_fotos",
      "supera_mystic": false,
      "incluye": "Que incluye el alquiler segun el anuncio: mantenimiento, agua, luz, o 'no dice'.",
      "hay_que_amoblar": false
    }
  ]
}
```

Reglas de la salida:

- `cumple` = 2+ rec, 2 baños, 1+ estacionamiento, equipado, y se ve bien.
- `revisar` = le falta algo que **Eric podria perdonar**: 1 solo baño, hay que
  amoblar, no queda claro el equipamiento, precio justo en el limite.
- `descartado` = no llega (menos de 2 recámaras, sin estacionamiento, anuncio
  incompleto o basura) **o se ve mal en las fotos**. Siempre con la razon.
- Poné un veredicto **solo** por cada anuncio que hayas juzgado de verdad:
  los de `nuevos`, los que no traen `veredicto_previo`, y los que cambiaron de
  precio. Los demas los completa el renderer desde la base.
  Si no juzgaste ninguno, devolvé `"veredictos": []` con el resumen igual.
- `linea_push` va dentro de un mensaje de Pushover con tope de ~1024
  caracteres: que sea corta de verdad.
- Escribí **solo** `veredictos.json`. El HTML lo arma otro script.
