from dataclasses import dataclass, field

from livekit.agents import RunContext, ToolError, function_tool
from database.connection import create_pool


@dataclass
class ItemPedido:
    product_id: int
    name: str
    unit_price: int
    quantity: int


@dataclass
class PedidoEnCurso:
    """Estado de la llamada en curso.

    Vive en `session.userdata`, no en un global de modulo: cada llamada
    (cada AgentSession) tiene su propia instancia, asi que dos llamadas
    simultaneas en el mismo proceso nunca comparten ni mezclan su pedido.
    `customer_phone` se llena desde telefonia (sip.phoneNumber) cuando
    aplica; en console/playground queda en None.
    """

    items: list[ItemPedido] = field(default_factory=list)
    customer_phone: str | None = None

    @property
    def total(self) -> int:
        return sum(item.unit_price * item.quantity for item in self.items)

    def resumen(self) -> list[dict]:
        return [
            {
                "product_id": i.product_id,
                "name": i.name,
                "unit_price": i.unit_price,
                "quantity": i.quantity,
                "subtotal": i.unit_price * i.quantity,
            }
            for i in self.items
        ]


async def _fetch_product(product_id: int) -> dict | None:
    pool = await create_pool()
    row = await pool.fetchrow(
        "SELECT id, name, price, stock FROM products WHERE id = $1;",
        product_id,
    )
    return dict(row) if row else None


@function_tool
async def add_item_to_order(ctx: RunContext[PedidoEnCurso], product_id: int, quantity: int):
    """Agrega un producto al pedido actual (todavia no lo guarda en la base de datos).

    Valida que el producto exista y que haya stock suficiente antes de
    agregarlo. Si el cliente pide el mismo producto otra vez, suma la
    cantidad a lo que ya tenia en el pedido.
    """

    pedido = ctx.userdata

    product = await _fetch_product(product_id)
    if product is None:
        return {
            "success": False,
            "message": "Ese producto no existe. Usa search_products para confirmar el id correcto.",
        }

    ya_pedido = sum(i.quantity for i in pedido.items if i.product_id == product_id)
    if product["stock"] < ya_pedido + quantity:
        disponible = max(product["stock"] - ya_pedido, 0)
        return {
            "success": False,
            "message": (
                f"No hay suficiente stock de {product['name']}. "
                f"Disponible: {disponible}."
            ),
        }

    existente = next((i for i in pedido.items if i.product_id == product_id), None)
    if existente:
        existente.quantity += quantity
    else:
        pedido.items.append(
            ItemPedido(
                product_id=product["id"],
                name=product["name"],
                unit_price=product["price"],
                quantity=quantity,
            )
        )

    return {
        "success": True,
        "message": f"{product['name']} agregado al pedido.",
        "order": pedido.resumen(),
        "total": pedido.total,
    }


@function_tool
async def set_item_quantity(ctx: RunContext[PedidoEnCurso], product_id: int, quantity: int):
    """Corrige la cantidad de un producto que ya esta en el pedido.

    Usa esta herramienta cuando el cliente se corrige o cambia de opinion
    (ej. "quiteme la gaseosa", "mejor que sean tres", "cambie eso").
    `quantity` es la cantidad final que debe quedar, no un ajuste relativo.
    Usa quantity=0 para quitar el producto del pedido por completo.
    """

    pedido = ctx.userdata

    if quantity < 0:
        return {"success": False, "message": "La cantidad no puede ser negativa."}

    existente = next((i for i in pedido.items if i.product_id == product_id), None)

    if quantity == 0:
        if existente is None:
            return {
                "success": False,
                "message": "Ese producto no estaba en el pedido.",
            }
        pedido.items.remove(existente)
        return {
            "success": True,
            "message": f"{existente.name} se quito del pedido.",
            "order": pedido.resumen(),
            "total": pedido.total,
        }

    product = await _fetch_product(product_id)
    if product is None:
        return {
            "success": False,
            "message": "Ese producto no existe. Usa search_products para confirmar el id correcto.",
        }
    if product["stock"] < quantity:
        return {
            "success": False,
            "message": f"No hay suficiente stock de {product['name']}. Disponible: {product['stock']}.",
        }

    if existente:
        existente.quantity = quantity
    else:
        pedido.items.append(
            ItemPedido(
                product_id=product["id"],
                name=product["name"],
                unit_price=product["price"],
                quantity=quantity,
            )
        )

    return {
        "success": True,
        "message": f"Cantidad de {product['name']} actualizada a {quantity}.",
        "order": pedido.resumen(),
        "total": pedido.total,
    }


@function_tool
async def vaciar_pedido(ctx: RunContext[PedidoEnCurso]):
    """Cancela y borra todo el pedido actual, sin guardarlo en la base de datos.

    Usa esta herramienta solo si el cliente pide explicitamente empezar
    de nuevo o cancelar todo lo que llevaba pedido.
    """

    ctx.userdata.items.clear()
    return {"success": True, "message": "Pedido vaciado."}


@function_tool
async def confirm_order(ctx: RunContext[PedidoEnCurso]):
    """Confirma y guarda el pedido actual en la base de datos.

    Debe usarse solo despues de que el cliente confirmo explicitamente
    todos los productos y cantidades de su pedido.
    """

    pedido = ctx.userdata

    if not pedido.items:
        return {
            "success": False,
            "message": "No hay productos en el pedido todavia.",
        }

    pool = await create_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Bloquea las filas de producto involucradas y revalida stock
            # justo antes de descontarlo, por si cambio desde que se
            # agregaron los items (otra llamada concurrente, por ejemplo).
            for item in pedido.items:
                stock = await conn.fetchval(
                    "SELECT stock FROM products WHERE id = $1 FOR UPDATE;",
                    item.product_id,
                )
                if stock is None or stock < item.quantity:
                    # ToolError (no una excepcion generica): su mensaje llega
                    # tal cual al LLM. Cualquier otra excepcion sin capturar
                    # se convierte en "An internal error occurred" en ingles,
                    # lo cual el agente terminaria diciendo en una llamada en
                    # espanol.
                    raise ToolError(
                        f"Ya no hay suficiente stock de {item.name} para confirmar el pedido."
                    )

            order_id = await conn.fetchval(
                """
                INSERT INTO orders (status, customer_phone, total)
                VALUES ('confirmed', $1, $2)
                RETURNING id;
                """,
                pedido.customer_phone,
                pedido.total,
            )

            for item in pedido.items:
                await conn.execute(
                    """
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price)
                    VALUES ($1, $2, $3, $4);
                    """,
                    order_id,
                    item.product_id,
                    item.quantity,
                    item.unit_price,
                )
                await conn.execute(
                    "UPDATE products SET stock = stock - $1 WHERE id = $2;",
                    item.quantity,
                    item.product_id,
                )

    total = pedido.total
    pedido.items.clear()

    return {
        "success": True,
        "message": "Pedido confirmado y guardado.",
        "order_id": order_id,
        "total": total,
    }
