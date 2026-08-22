from livekit.agents import RunContext, function_tool
from database.connection import create_pool
from tools.orders import PedidoEnCurso


async def _menu_rows() -> list[dict]:
    pool = await create_pool()
    rows = await pool.fetch(
        """
        SELECT id, name, description, price, stock, category
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

    Se llama una sola vez al iniciar cada sesion (ver agent.py). Antes
    esto era una tool (get_menu) que el agente tenia que invocar y esperar
    un roundtrip completo de LLM solo para listar 4 productos que no
    cambian durante la llamada; ahora el agente ya "sabe" el menu desde el
    primer turno, sin tool ni latencia.
    """
    rows = await _menu_rows()
    if not rows:
        return "El menu esta vacio por el momento; informa al cliente que no hay productos disponibles."

    por_categoria: dict[str, list[dict]] = {}
    for row in rows:
        por_categoria.setdefault(row["category"], []).append(row)

    lineas = []
    for categoria, productos in por_categoria.items():
        lineas.append(f"{categoria.upper()}:")
        for p in productos:
            lineas.append(f"  - [id {p['id']}] {p['name']}: {p['description']} ({_formatear_precio(p['price'])})")
    return "\n".join(lineas)


@function_tool
async def search_products(ctx: RunContext[PedidoEnCurso], query: str):
    """Busca un producto especifico a partir de lo que dice el cliente.

    El menu completo ya esta en tus instrucciones: para preguntas
    generales o por categoria ("que bebidas tienen", "que combos
    manejan") respondelas directo, sin usar esta herramienta.

    Usa search_products solo cuando el cliente nombra o describe un
    producto puntual y necesitas confirmar su id exacto antes de
    agregarlo al pedido. Entiende sinonimos comunes (ej. "gaseosa" ->
    Coca-Cola) y tolera errores de transcripcion de voz (ej. "polo asado"
    -> Pollo Asado).
    """

    # Si la consulta tarda (conexion lenta, base ocupada), el agente dice
    # esto en vez de dejar un silencio muerto. delay=0.6s: en el camino
    # feliz la tool responde antes y el filler nunca llega a sonar.
    async with ctx.with_filler("Dame un momento, reviso...", delay=0.6):
        pool = await create_pool()

        rows = await pool.fetch(
            """
            SELECT id, name, description, price, stock, category,
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
