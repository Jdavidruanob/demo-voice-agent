"""Pluralizacion en espanol para textos generados en Python (bloques de
disponibilidad, confirmaciones de reserva) que despues lee el LLM o el TTS.

No reemplaza el trabajo que ya hace el prompt para la voz del LLM (ver
agent.py): esto es para el texto que arman las tools directamente, donde no
hay LLM de por medio para decidir "2 noches" en vez de "2 de noche".
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


# Plurales de los adjetivos de tipo de habitacion que maneja el hotel. Se usa
# una tabla en vez del pluralizador generico porque "habitacion" pierde la
# tilde en plural (habitacion -> habitaciones) y el adjetivo debe concordar
# en genero y numero (doble -> dobles), algo que una regla generica no
# resuelve bien para un catalogo tan chico.
_PLURALES_ADJETIVO = {
    "sencilla": "sencillas",
    "doble": "dobles",
    "triple": "triples",
    "familiar": "familiares",
    "presidencial": "presidenciales",
}

# Palabras extranjeras que no se pluralizan en español (ej. "Suite Junior"
# -> "suites junior", nunca "juniors" ni "juniores").
_ADJETIVOS_INVARIABLES = {"junior"}


def _pluralizar_adjetivo(palabra: str) -> str:
    minuscula = palabra.lower()
    if minuscula in _ADJETIVOS_INVARIABLES:
        return palabra
    if minuscula in _PLURALES_ADJETIVO:
        return _PLURALES_ADJETIVO[minuscula]
    return pluralizar_sustantivo(palabra)


def formatear_habitaciones(cantidad: int, tipo_habitacion: str) -> str:
    """Pluraliza 'habitacion <tipo>' completo (ej. 3 -> 'habitaciones dobles').

    Para "suite" (y compuestos como "Suite Junior") no antepone "habitacion"
    (se dice "una suite", "dos suites junior", no "una habitación suite").
    """
    palabras = tipo_habitacion.strip().split()
    if not palabras:
        return formatear_cantidad(cantidad, tipo_habitacion)

    primera, *resto = palabras

    if primera.lower() == "suite":
        base_singular, base_plural = "suite", "suites"
    else:
        base_singular = f"habitación {primera}"
        base_plural = f"habitaciones {_pluralizar_adjetivo(primera)}"

    if resto:
        cola_singular = " " + " ".join(resto)
        cola_plural = " " + " ".join(_pluralizar_adjetivo(palabra) for palabra in resto)
    else:
        cola_singular = cola_plural = ""

    return formatear_cantidad(cantidad, base_singular + cola_singular, base_plural + cola_plural)


def formatear_precio(price: int) -> str:
    return f"${price:,}".replace(",", ".")
