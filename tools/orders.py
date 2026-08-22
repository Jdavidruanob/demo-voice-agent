from livekit.agents import function_tool
from database.connection import create_pool

# Pedido actual de la llamada en curso. Ojo: si en el futuro tu agente
# maneja varias llamadas en paralelo dentro del mismo proceso, esta
# lista global se compartiría entre llamadas. Para eso, LiveKit Agents
# permite guardar este estado en `session.userdata` en vez de un global.
# Por ahora, con una llamada a la vez, esto funciona bien.
order = []


@function_tool
async def add_item_to_order(product_id: int, quantity: int):
    """Agrega un producto al pedido actual (todavía no lo guarda en la base de datos)."""

    order.append({
        "product_id": product_id,
        "quantity": quantity,
    })

    return {
        "success": True,
        "message": "Producto agregado al pedido.",
        "order": order,
    }


@function_tool
async def confirm_order():
    """Confirma y guarda el pedido actual en la base de datos.

    Debe usarse solo después de que el cliente confirmó explícitamente
    todos los productos y cantidades de su pedido.
    """

    if not order:
        return {
            "success": False,
            "message": "No hay productos en el pedido todavía.",
        }

    pool = await create_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            order_id = await conn.fetchval(
                "INSERT INTO orders (status) VALUES ('confirmed') RETURNING id;"
            )

            for item in order:
                await conn.execute(
                    """
                    INSERT INTO order_items (order_id, product_id, quantity)
                    VALUES ($1, $2, $3);
                    """,
                    order_id,
                    item["product_id"],
                    item["quantity"],
                )

    order.clear()

    return {
        "success": True,
        "message": "Pedido confirmado y guardado.",
        "order_id": order_id,
    }
