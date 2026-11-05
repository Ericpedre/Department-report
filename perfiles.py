#!/usr/bin/env python3
"""
perfiles.py — que se busca, y con que vara se juzga.

Este proyecto busca DOS cosas distintas a la vez sobre un mismo scraping:

  principal  el reemplazo del apartamento de Eric: 2 rec / 2 banos / equipado,
             contra la vara de PH Mystic Village ($650).
  economico  el plan B barato: 1 rec / 1 bano / linea blanca / 1 estacionamiento,
             tope duro de $350, y aca SI entran las casas. El objetivo no es
             igualar prestaciones sino AHORRAR contra los $685 + luz + internet
             que Eric paga hoy.

Todo lo que distingue a un perfil del otro vive aca y en ningun otro lado. Antes
esto estaba desparramado como constantes: el `>= 2` de recamaras adentro de
photos.py, el `MYSTIC = {...}` adentro de render.py, la lista de categorias
adentro de fetch.py. Cada vez que se tocaba una habia que acordarse de las otras.

Las zonas NO estan aca a proposito: son las mismas para los dos perfiles y viven
en state.py, que es quien las aplica.
"""

# Categorias validas de Encuentra24 (slugs VERIFICADOS contra el sitio el
# 2026-09-15). `casas-amuebladas` y `habitaciones` NO existen: devuelven un
# soft-404 (HTTP 200 sin initialSearchUrl). No agregarlas sin volver a medir.
CAT_APARTAMENTOS = "bienes-raices-alquiler-apartamentos"
CAT_AMUEBLADOS = "bienes-raices-alquiler-apartamentos-amueblados"
CAT_CASAS = "bienes-raices-alquiler-casas"

# Filtro de precio por categoria. UN SOLO FILTRO POR URL: combinar con coma hace
# que el servidor descarte el `q` entero y devuelva la categoria sin filtrar,
# con HTTP 200 (ver el encabezado de fetch.py).
#
# Los apartamentos se bajan hasta 750 y no hasta los 685 de Eric a proposito:
# su alquiler NO incluye luz ni internet, asi que un anuncio de $720 con
# servicios incluidos puede ganar en plata real. Ese margen lo juzga Claude.
#
# Las casas se bajan hasta 350 y no hasta 750 porque SOLO alimentan al perfil
# economico, que corta duro en 350: bajar mas seria gastar ~13 paginas de
# requests en anuncios que ningun perfil va a mirar.
FILTROS_PRECIO = {
    CAT_APARTAMENTOS: "f_price.0-750",
    CAT_AMUEBLADOS: "f_price.0-750",
    CAT_CASAS: "f_price.0-350",
}

# --------------------------------------------------------------------------
# VENTA — no alimenta ningun perfil de busqueda: alimenta el almacen de mercado.
# --------------------------------------------------------------------------
# Slug VERIFICADO contra el sitio el 2026-09-18. Ojo, porque el intuitivo NO
# existe: `bienes-raices-venta-de-apartamentos` devuelve el soft-404 de siempre
# (HTTP 200 sin initialSearchUrl). El bueno lleva "de-propiedades" en el medio.
CAT_VENTA_APTOS = "bienes-raices-venta-de-propiedades-apartamentos"

# DICT APARTE, y esto NO es cosmetico: si el slug de venta se agregara a
# FILTROS_PRECIO, la corrida diaria de las 7:00 se llevaria 155 paginas extra
# sin que nadie lo pidiera, todos los dias. Venta se baja UNA VEZ POR SEMANA,
# desde venta.py, con su propio timer.
#
# Tope de $150k medido el 2026-09-18: 3097 anuncios en 155 paginas. Subirlo
# multiplica el costo rapido; bajarlo a 120k deja 1228 en 62.
FILTROS_VENTA = {
    CAT_VENTA_APTOS: "f_price.0-150000",
}


PERFILES = {
    "principal": {
        "titulo": "Apartamentos",
        # Subcarpeta dentro de publish/. El principal va en la raiz para no
        # romper la URL que Eric ya tiene guardada.
        "salida": "",
        "categorias": [CAT_APARTAMENTOS, CAT_AMUEBLADOS],
        "precio_max": 750,
        "min_recamaras": 2,
        "prompt": "prompt_criterio.md",
        "veredictos": "veredictos.json",
        "vara": "vara actual: PH Mystic Village $650, 2rec/2baños",
        # Etiqueta del hallazgo destacado. En el principal la marca la pone
        # Claude con `supera_mystic`; en el economico con `ahorro_mensual`.
        "campo_destacado": "supera_mystic",
        "tag_destacado": "le gana a Mystic Village",
        "titulo_push": "Apartamentos",
        # Se formatea con n=cuantos destacados, mejor=el mejor valor.
        "frase_push": "{n} le gana(n) a Mystic Village.",
        "max_fotos_anuncios": 25,
    },
    "economico": {
        "titulo": "Económicos",
        "salida": "economico",
        "categorias": [CAT_APARTAMENTOS, CAT_AMUEBLADOS, CAT_CASAS],
        "precio_max": 350,
        "min_recamaras": 1,
        "prompt": "prompt_criterio_economico.md",
        "veredictos": "veredictos_economico.json",
        "vara": "contra lo que pagás hoy: $685 + luz + internet",
        "campo_destacado": "ahorro_mensual",
        "tag_destacado": "ahorra ${}/mes",
        "titulo_push": "Económicos",
        "frase_push": "{n} candidato(s); el mejor ahorra ${mejor}/mes.",
        "max_fotos_anuncios": 12,
    },
}

# Orden en que corren. El principal primero: si la Pi se queda sin RAM o el dia
# se corta a la mitad, lo que tiene que haber salido si o si es el principal.
ORDEN = ["principal", "economico"]


def perfil(nombre):
    """Devuelve la config de un perfil, o explota con un mensaje util."""
    if nombre not in PERFILES:
        raise SystemExit(
            f"perfil desconocido: {nombre!r}. Validos: {', '.join(ORDEN)}")
    return PERFILES[nombre]


def sufijo(nombre):
    """Sufijo de los archivos de trabajo de un perfil.

    El principal NO lleva sufijo: sus archivos siguen llamandose
    candidatos_del_dia.json y para_criterio.json como siempre, asi que los
    comandos que Eric ya tiene en la memoria muscular siguen andando.
    """
    return "" if nombre == "principal" else f"_{nombre}"
