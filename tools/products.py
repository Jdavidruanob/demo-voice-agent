from livekit.agents import function_tool
from database.connection import create_pool


@function_tool
async def search_products(query: str):
    """Busca productos del restaurante a partir de lo que dice el cliente.

    No necesita coincidir exactamente con el nombre del producto: entiende
    sinónimos comunes (ej. "gaseosa" -> Coca-Cola) y tolera errores de
    tipeo o transcripción de voz (ej. "polo asado" -> Pollo Asado).
    Si el cliente pregunta algo vago por categoría (ej. "qué bebidas
    tienen"), usa mejor la herramienta get_menu.
    """

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
        query
    )

    if not rows:
        return {
            "found": False,
            "products": []
        }

    return {
        "found": True,
        "products": [
            {k: v for k, v in dict(row).items() if k != "score"}
            for row in rows
        ]
    }


@function_tool
async def get_menu():
    """Devuelve el menú completo agrupado por categoría.

    Útil cuando el cliente hace una pregunta general o vaga, como
    "qué bebidas tienen" o "qué combos manejan", en vez de nombrar
    un producto específico.
    """

    pool = await create_pool()

    rows = await pool.fetch(
        """
        SELECT id, name, description, price, stock, category
        FROM products
        WHERE stock > 0
        ORDER BY category, name;
        """
    )

    return {"products": [dict(row) for row in rows]}