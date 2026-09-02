from datetime import date

from livekit.agents import RunContext, function_tool
from database.connection import create_pool
from tools.orders import PedidoEnCurso

# Orden de presentacion del menu: NO es alfabetico, sigue el orden en que un
# mesero real recorreria la carta (platos de pasta primero, arroces, luego
# los especiales del dia y por ultimo los almuerzos ejecutivos).
_CATEGORIAS = [
    ("lasagna", "LASAGNA"),
    ("spaguetti", "SPAGUETTI"),
    ("arroces", "ARROCES"),
    ("especiales", "ESPECIALES"),
    ("almuerzo_ejecutivo", "ALMUERZO EJECUTIVO"),
]

_DIAS_ES = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
]

# Tildes solo para mostrar en el texto del prompt; la comparacion contra
# dia_disponible (columna sin tildes) usa _DIAS_ES tal cual. "lunes",
# "martes", "miercoles", "jueves" y "viernes" ya son invariables en plural
# (no llevan "s" extra), asi que este mapa tambien sirve para no duplicarla.
_DIAS_ES_DISPLAY = {
    "lunes": "lunes",
    "martes": "martes",
    "miercoles": "miércoles",
    "jueves": "jueves",
    "viernes": "viernes",
    "sabado": "sábado",
    "domingo": "domingo",
}


def _dia_actual() -> str:
    return _DIAS_ES[date.today().weekday()]


async def _menu_rows() -> list[dict]:
    pool = await create_pool()
    rows = await pool.fetch(
        """
        SELECT id, name, description, price, stock, category, dia_disponible
        FROM products
        WHERE stock > 0
        ORDER BY category, name;
        """
    )
    return [dict(row) for row in rows]


def _formatear_precio(price: int) -> str:
    return f"${price:,}".replace(",", ".")


async def build_menu_prompt_block() -> str:
    """Arma el menu como texto listo para pegar en las instructions del agente.

    Se llama una sola vez al iniciar cada sesion (ver agent.py): el agente ya
    "sabe" el menu completo desde el primer turno, sin gastar un roundtrip de
    tool para listarlo. Los ESPECIALES llevan ademas si el dia de hoy
    coincide con su dia_disponible, calculado aqui mismo (no le pidas al LLM
    que calcule el dia de la semana: es del tipo de cosas que transcribe o
    razona mal en una llamada de voz).
    """
    rows = await _menu_rows()
    if not rows:
        return "El menú está vacío por el momento; informa al cliente que no hay platos disponibles."

    por_categoria: dict[str, list[dict]] = {}
    for row in rows:
        por_categoria.setdefault(row["category"], []).append(row)

    dia_actual = _dia_actual()
    dia_actual_display = _DIAS_ES_DISPLAY[dia_actual]

    lineas = [f"MENÚ (hoy es {dia_actual_display}; esto es todo lo que existe, no ofrezcas ni inventes nada fuera de esta lista):"]
    for slug, encabezado in _CATEGORIAS:
        productos = por_categoria.get(slug)
        if not productos:
            continue
        lineas.append(f"{encabezado}:")
        for p in productos:
            extra = ""
            if p["dia_disponible"]:
                disponible_hoy = p["dia_disponible"] == dia_actual
                dia_especial_display = _DIAS_ES_DISPLAY[p["dia_disponible"]]
                extra = (
                    f" [disponible HOY, {dia_actual_display}]"
                    if disponible_hoy
                    else f" [NO disponible hoy; solo los {dia_especial_display}]"
                )
            lineas.append(
                f"  - [id {p['id']}] {p['name']}: {p['description']} "
                f"({_formatear_precio(p['price'])}){extra}"
            )
    lineas.append(
        "NOTA ALMUERZO EJECUTIVO: todos los almuerzos ejecutivos incluyen arroz "
        "blanco, papa a la francesa, ensalada y sopa. Además el cliente escoge un "
        "principio: frijoles, lentejas o pasta -- pregúntaselo siempre antes de "
        "agregar un almuerzo ejecutivo al pedido, y pásalo como el parámetro "
        "principio de add_item_to_order."
    )
    return "\n".join(lineas)


@function_tool
async def search_products(ctx: RunContext[PedidoEnCurso], query: str):
    """Busca un plato especifico a partir de lo que dice el cliente.

    El menu completo ya esta en tus instrucciones: para preguntas
    generales o por categoria ("que arroces tienen", "que almuerzos
    ejecutivos manejan") respondelas directo, sin usar esta herramienta.

    Usa search_products solo cuando el cliente nombra o describe un plato
    puntual y necesitas confirmar su id exacto antes de agregarlo al
    pedido. Entiende sinonimos comunes (ej. "espagueti" -> Spaguetti,
    "lasaña" -> Lasagna) y tolera errores de transcripcion de voz.
    """

    # Si la consulta tarda (conexion lenta, base ocupada), el agente dice
    # esto en vez de dejar un silencio muerto. delay=0.6s: en el camino
    # feliz la tool responde antes y el filler nunca llega a sonar.
    async with ctx.with_filler("Dame un momento, reviso...", delay=0.6):
        pool = await create_pool()

        rows = await pool.fetch(
            """
            SELECT id, name, description, price, stock, category, dia_disponible,
                   GREATEST(
                       similarity(name, $1),
                       similarity(coalesce(description, ''), $1),
                       COALESCE(
                           (SELECT MAX(similarity(kw, $1)) FROM unnest(keywords) AS kw),
                           0
                       )
                   ) AS score
            FROM products
            WHERE
                name ILIKE '%' || $1 || '%'
                OR description ILIKE '%' || $1 || '%'
                OR EXISTS (
                    SELECT 1 FROM unnest(keywords) AS kw
                    WHERE kw ILIKE '%' || $1 || '%' OR $1 ILIKE '%' || kw || '%'
                )
                OR similarity(name, $1) > 0.3
                OR similarity(coalesce(description, ''), $1) > 0.25
            ORDER BY score DESC
            LIMIT 5;
            """,
            query,
        )

    if not rows:
        return {"found": False, "products": []}

    return {
        "found": True,
        "products": [
            {k: v for k, v in dict(row).items() if k != "score"}
            for row in rows
        ],
    }
