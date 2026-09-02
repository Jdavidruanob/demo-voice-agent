"""Pluralizacion en espanol para textos generados en Python (resumenes de
pedido, confirmaciones) que despues lee el LLM o el TTS.

No reemplaza el trabajo que ya hace el prompt para la voz del LLM (ver
agent.py): esto es para el texto que arma tools/orders.py directamente,
donde no hay LLM de por medio para decidir "2 lasagnas" en vez de "2 de
lasagna".
"""


def pluralizar_sustantivo(singular: str) -> str:
    """Pluraliza un sustantivo/adjetivo espanol regular (sin excepciones)."""
    palabra = singular.strip()
    minuscula = palabra.lower()

    if minuscula.endswith("z"):
        return palabra[:-1] + "ces"
    if minuscula.endswith("on") or minuscula.endswith("ón"):
        return palabra[:-2] + "ones"
    if minuscula.endswith(("a", "e", "i", "o", "u")):
        return palabra + "s"
    return palabra + "es"


def formatear_cantidad(cantidad: int, singular: str, plural: str | None = None) -> str:
    """Devuelve '<cantidad> <singular o plural>', nunca 'X de <singular>'."""
    if cantidad == 1:
        return f"1 {singular}"
    return f"{cantidad} {plural or pluralizar_sustantivo(singular)}"


def formatear_platos(cantidad: int, nombre_plato: str) -> str:
    """Pluraliza el nombre de un plato del menu (ej. 2 -> '2 Lasagnas Bolognesa').

    Solo pluraliza la primera palabra (el tipo de plato: Lasagna, Spaguetti,
    Arroz, Filete, Chuleta...); el resto del nombre (el sabor/proteina:
    "Bolognesa", "Carbonara", "de Cerdo") queda invariable, tal como suena
    natural en espanol para un nombre de plato ("2 Spaguettis Carbonara", no
    "2 Spaguettis Carbonaras"; "3 Chuletas de Cerdo", no "3 Chuletas de
    Cerdos"). Nunca usa la forma "X de <plato>".
    """
    palabras = nombre_plato.strip().split()
    if not palabras:
        return formatear_cantidad(cantidad, nombre_plato)

    primera, *resto = palabras
    primera_plural = pluralizar_sustantivo(primera)

    singular = " ".join([primera, *resto])
    plural = " ".join([primera_plural, *resto])

    return formatear_cantidad(cantidad, singular, plural)


def formatear_precio(price: int) -> str:
    return f"${price:,}".replace(",", ".")
