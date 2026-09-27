from livekit.agents import RunContext, function_tool

from tools.orders import PedidoEnCurso


@function_tool
async def search_products(ctx: RunContext[PedidoEnCurso], query: str):
    """Busca un producto especifico a partir de lo que dice el cliente.

    El menu completo ya esta en tus instrucciones: para preguntas generales o
    por categoria ("que bebidas tienen", "que hamburguesas manejan")
    respondelas directo, sin usar esta herramienta.

    Usa search_products solo cuando el cliente nombra o describe un producto
    puntual y necesitas confirmar su codigo exacto antes de agregarlo al
    pedido. Tolera errores de transcripcion de voz (ej. "asado de cotilla" ->
    "Costilla BBQ").
    """

    catalogo = ctx.userdata.catalogo
    if catalogo is None:
        return {
            "found": False,
            "products": [],
            "message": (
                "No tengo el menú cargado en esta llamada. Dile al cliente que "
                "escriba por WhatsApp."
            ),
        }

    # Ya no hay consulta a la base: el catalogo esta en memoria desde que
    # arranco la llamada, asi que esto responde al instante y no necesita el
    # filler que antes tapaba la latencia de `pg_trgm`.
    encontrados = catalogo.buscar(query)
    if not encontrados:
        return {"found": False, "products": []}

    return {
        "found": True,
        "products": [
            {
                "sku": p.sku,
                "name": p.name,
                "description": p.description,
                "precio": p.precio_hoy,
                "promocion": p.promocion,
                "disponible": p.disponible,
                "categoria": p.categoria,
                # Los grupos obligatorios van en la respuesta para que el agente
                # sepa, en el mismo turno, que le falta preguntar el termino de
                # la carne antes de poder agregarlo.
                "preguntar": [
                    {
                        "grupo": g.name,
                        "opciones": [{"id": o.id, "name": o.name} for o in g.opciones],
                    }
                    for g in p.grupos_obligatorios
                ],
            }
            for p in encontrados
        ],
    }
