import json
import logging
from dataclasses import dataclass, field

from livekit.agents import RunContext, function_tool

from brasa.api import PedidoRechazado, PedidosAPI, PedidosCaido
from brasa.catalogo import Catalogo, Producto

logger = logging.getLogger("agent")

# Formas de pago que entiende el sistema de pedidos. `datafono` es cobro con
# tarjeta contra entrega: el domiciliario lleva el datafono.
FORMAS_DE_PAGO = ("efectivo", "transferencia", "datafono")


@dataclass
class ItemPedido:
    sku: str
    name: str
    unit_price: int
    quantity: int
    option_ids: list[int] = field(default_factory=list)
    # Los nombres de las opciones se guardan para poder leer el pedido en voz
    # alta al confirmar ("con termino tres cuartos") sin volver al catalogo.
    option_names: list[str] = field(default_factory=list)


@dataclass
class PedidoEnCurso:
    """Estado de la llamada en curso.

    Vive en `session.userdata`, no en un global de modulo: cada llamada (cada
    AgentSession) tiene su propia instancia, asi que dos llamadas simultaneas
    en el mismo proceso nunca comparten ni mezclan su pedido.

    `call_id` es el `room_name` de LiveKit y es lo que hace idempotente la
    confirmacion: si el modelo invoca `confirm_order` dos veces, el sistema de
    pedidos devuelve el mismo pedido en vez de mandar la comanda repetida a la
    cocina. **No se regenera nunca dentro de una llamada.**

    `customer_phone` se llena desde telefonia (sip.phoneNumber) cuando aplica;
    en una llamada web queda en None y el agente lo pregunta.

    `order_code` queda en None hasta que `confirm_order` guarda con exito.
    `respaldo_dado` se marca cuando el sistema de pedidos se cayo y el agente ya
    le dijo al cliente que escriba por WhatsApp: los dos los consulta
    `finalizar_llamada` para decidir si puede colgar.
    """

    items: list[ItemPedido] = field(default_factory=list)
    customer_phone: str | None = None
    customer_name: str | None = None
    delivery_address: str | None = None
    payment_method: str | None = None
    order_code: str | None = None
    respaldo_dado: bool = False

    call_id: str = ""
    catalogo: Catalogo | None = None
    api: PedidosAPI | None = None

    @property
    def total(self) -> int:
        """Un subtotal PARCIAL, para poder ir diciendo por donde va la cuenta.

        No es el total del pedido: le falta el domicilio, y el que manda es el
        que devuelve `confirm_order`. Ver `brasa/catalogo.py`."""
        if self.catalogo is None:
            return sum(i.unit_price * i.quantity for i in self.items)

        total = 0
        for item in self.items:
            producto = self.catalogo.buscar_sku(item.sku)
            if producto is None:
                total += item.unit_price * item.quantity
            else:
                total += producto.total_linea(item.option_ids, item.quantity)
        return total

    def resumen(self) -> list[dict]:
        return [
            {
                "sku": i.sku,
                "name": i.name,
                "opciones": i.option_names,
                "unit_price": i.unit_price,
                "quantity": i.quantity,
            }
            for i in self.items
        ]

    def para_el_sistema(self) -> list[dict]:
        """Las lineas como las espera `POST /api/internal/voice-order`.

        Van solo referencias: SKU, ids de opcion y cantidad. Los precios los
        resuelve el servidor contra la base — el agente no manda cifras."""
        return [
            {"sku": i.sku, "optionIds": i.option_ids, "quantity": i.quantity}
            for i in self.items
        ]


def _describir(producto: Producto, option_ids: list[int]) -> list[str]:
    nombres = []
    for opcion_id in option_ids:
        opcion = producto.opcion(opcion_id)
        if opcion:
            nombres.append(opcion.name)
    return nombres


def _resolver(pedido: PedidoEnCurso, sku: str) -> tuple[Producto | None, dict | None]:
    """El producto, o la respuesta de error que el agente tiene que decir."""
    if pedido.catalogo is None:
        return None, {
            "success": False,
            "message": (
                "No tengo el menú cargado en esta llamada. Dile al cliente que "
                "escriba por WhatsApp y termina la llamada."
            ),
        }

    producto = pedido.catalogo.buscar_sku(sku)
    if producto is None:
        return None, {
            "success": False,
            "message": (
                f"No existe ningún producto con el código {sku}. Usa "
                "search_products para confirmar el código correcto."
            ),
        }

    if not producto.disponible:
        return None, {
            "success": False,
            "message": (
                f"{producto.name} está agotado hoy. Dile al cliente que no hay y "
                "ofrécele algo parecido del menú."
            ),
        }

    return producto, None


def _agregar(
    pedido: PedidoEnCurso,
    sku: str,
    quantity: int,
    option_ids: list[int] | None = None,
) -> dict:
    """El cuerpo de `add_item_to_order`, como funcion normal.

    Vive aparte porque `set_item_quantity` lo necesita cuando el cliente pide
    una cantidad de algo que todavia no estaba en el pedido, y una tool no se
    invoca desde otra tool.
    """
    option_ids = option_ids or []

    producto, error = _resolver(pedido, sku)
    if error:
        return error
    assert producto is not None

    if quantity < 1:
        return {
            "success": False,
            "message": "La cantidad tiene que ser al menos uno. Usa set_item_quantity para quitar algo.",
        }

    # El termino de la carne se pregunta ANTES de agregar, no al confirmar: el
    # servidor rechaza el pedido entero si falta, y descubrirlo al final
    # significaria volver a armar la conversacion desde cero.
    faltantes = producto.faltantes_obligatorios(option_ids)
    if faltantes:
        detalle = "; ".join(
            f"{g.name.lower()} ({', '.join(o.name for o in g.opciones)})" for g in faltantes
        )
        cuantas = (
            "esto" if len(faltantes) == 1 else f"estas {len(faltantes)} cosas, una a la vez"
        )
        return {
            "success": False,
            "message": (
                f"Antes de agregar {producto.name} tienes que preguntarle al cliente "
                f"{cuantas}: {detalle}. Después vuelve a llamar add_item_to_order con "
                "todos los option_ids juntos."
            ),
            "falta_preguntar": [
                {
                    "grupo": g.name,
                    "opciones": [{"id": o.id, "name": o.name} for o in g.opciones],
                }
                for g in faltantes
            ],
        }

    # Una opcion que no pertenece a este producto se descarta en silencio, igual
    # que hace el servidor: un id viejo o inventado no puede reventar la llamada.
    validas = [oid for oid in option_ids if producto.opcion(oid) is not None]
    nombres = _describir(producto, validas)
    unitario = producto.precio_unitario(validas)

    existente = next(
        (i for i in pedido.items if i.sku == producto.sku and sorted(i.option_ids) == sorted(validas)),
        None,
    )
    if existente:
        existente.quantity += quantity
    else:
        pedido.items.append(
            ItemPedido(
                sku=producto.sku,
                name=producto.name,
                unit_price=unitario,
                quantity=quantity,
                option_ids=validas,
                option_names=nombres,
            )
        )

    mensaje = f"{producto.name} agregado al pedido."
    if producto.promocion:
        mensaje += f" Le aplica {producto.promocion}."

    return {
        "success": True,
        "message": mensaje,
        "order": pedido.resumen(),
        "subtotal_parcial": pedido.total,
    }


@function_tool
async def add_item_to_order(
    ctx: RunContext[PedidoEnCurso],
    sku: str,
    quantity: int,
    option_ids: list[int] | None = None,
):
    """Agrega un producto al pedido actual (todavia no lo guarda).

    Args:
        sku: El codigo del producto, tal como aparece entre corchetes en el
            menu de tus instrucciones (ej. BURG-DOBLE).
        quantity: Cuantas unidades.
        option_ids: Los ids de las opciones que eligio el cliente, tal como
            aparecen en el menu (ej. [9] para el termino de la carne). Si el
            producto tiene un grupo marcado "PREGUNTA SIEMPRE" y no lo mandas,
            esta herramienta te va a decir que lo preguntes: no lo elijas tu.

    Si el cliente pide el mismo producto con las mismas opciones otra vez, suma
    la cantidad a lo que ya tenia.
    """
    return _agregar(ctx.userdata, sku, quantity, option_ids)


@function_tool
async def set_item_quantity(
    ctx: RunContext[PedidoEnCurso],
    sku: str,
    quantity: int,
):
    """Corrige la cantidad de un producto que ya esta en el pedido.

    Usa esta herramienta cuando el cliente se corrige o cambia de opinion
    (ej. "quiteme la gaseosa", "mejor que sean tres", "cambie eso").
    `quantity` es la cantidad final que debe quedar, no un ajuste relativo.
    Usa quantity=0 para quitar el producto del pedido por completo.

    Args:
        sku: El codigo del producto, como aparece en el menu.
        quantity: La cantidad final. 0 lo quita.
    """

    pedido = ctx.userdata

    if quantity < 0:
        return {"success": False, "message": "La cantidad no puede ser negativa."}

    # Se busca por SKU: si el cliente pidio el mismo producto con dos
    # personalizaciones distintas, se ajusta la primera y se avisa, en vez de
    # adivinar cual de las dos queria cambiar.
    coincidencias = [i for i in pedido.items if i.sku.lower() == (sku or "").lower()]

    if quantity == 0:
        if not coincidencias:
            return {"success": False, "message": "Ese producto no estaba en el pedido."}
        for item in coincidencias:
            pedido.items.remove(item)
        return {
            "success": True,
            "message": f"{coincidencias[0].name} se quitó del pedido.",
            "order": pedido.resumen(),
            "subtotal_parcial": pedido.total,
        }

    if coincidencias:
        if len(coincidencias) > 1:
            return {
                "success": False,
                "message": (
                    f"Hay {len(coincidencias)} versiones de {coincidencias[0].name} en el "
                    "pedido con personalizaciones distintas. Pregúntale al cliente a cuál "
                    "se refiere."
                ),
            }
        coincidencias[0].quantity = quantity
        return {
            "success": True,
            "message": f"Cantidad de {coincidencias[0].name} actualizada a {quantity}.",
            "order": pedido.resumen(),
            "subtotal_parcial": pedido.total,
        }

    # No estaba en el pedido: se agrega, que es lo que el cliente quiso decir.
    return _agregar(pedido, sku, quantity)


@function_tool
async def vaciar_pedido(ctx: RunContext[PedidoEnCurso]):
    """Cancela y borra todo el pedido actual, sin guardarlo.

    Usa esta herramienta solo si el cliente pide explicitamente empezar de
    nuevo o cancelar todo lo que llevaba pedido.
    """

    ctx.userdata.items.clear()
    return {"success": True, "message": "Pedido vaciado."}


@function_tool
async def confirm_order(
    ctx: RunContext[PedidoEnCurso],
    customer_name: str,
    delivery_address: str,
    phone: str,
    payment_method: str,
):
    """Confirma el pedido y lo manda a la cocina del restaurante.

    Debe usarse solo despues de que el cliente confirmo explicitamente todos
    los productos y cantidades, Y despues de haberle preguntado los cuatro
    datos de abajo. No inventes ni asumas ninguno.

    Args:
        customer_name: A nombre de quien queda el pedido.
        delivery_address: La direccion de entrega, completa, como la dicto el
            cliente (con apartamento o torre si la dio).
        phone: El numero de celular al que se le confirma por WhatsApp, solo
            digitos. Si el cliente no lo quiere dar, manda una cadena vacia.
        payment_method: Como paga: "efectivo", "transferencia" o "datafono"
            (tarjeta contra entrega).

    El total que devuelve esta herramienta es el que quedo guardado: **dile ese
    numero al cliente, no el que hayas sumado tu.**
    """

    pedido = ctx.userdata

    if not pedido.items:
        return {"success": False, "message": "No hay productos en el pedido todavía."}

    customer_name = (customer_name or "").strip()
    delivery_address = (delivery_address or "").strip()
    if not customer_name or not delivery_address:
        return {
            "success": False,
            "message": (
                "Antes de confirmar necesito a nombre de quién queda el pedido y la "
                "dirección de entrega."
            ),
        }

    forma = (payment_method or "").strip().lower()
    if forma not in FORMAS_DE_PAGO:
        return {
            "success": False,
            "message": (
                "Pregúntale al cliente cómo va a pagar: en efectivo, por transferencia "
                "o con tarjeta contra entrega (datáfono)."
            ),
        }

    solo_digitos = "".join(c for c in (phone or "") if c.isdigit())

    pedido.customer_name = customer_name
    pedido.delivery_address = delivery_address
    pedido.payment_method = forma
    if solo_digitos:
        pedido.customer_phone = solo_digitos

    if pedido.api is None:
        return _respaldo(pedido, "no hay conexión con el sistema de pedidos")

    # Si el sistema tarda, el agente dice esto en vez de dejar un silencio
    # muerto mientras se guarda el pedido.
    async with ctx.with_filler("Dame un segundo que lo registro...", delay=0.8):
        try:
            guardado = await pedido.api.crear_pedido(
                call_id=pedido.call_id,
                customer_name=customer_name,
                phone=pedido.customer_phone,
                address=delivery_address,
                address_notes=None,
                payment_method=forma,
                items=pedido.para_el_sistema(),
            )
        except PedidoRechazado as e:
            # Error esperado: el pedido llego bien y el sistema lo rechazo con un
            # motivo que se puede decir en voz alta. No se pierde el pedido: el
            # cliente arregla lo que falta y se vuelve a intentar.
            agotados = ", ".join(e.agotados) if e.agotados else ""
            return {
                "success": False,
                "message": (
                    f"No se pudo cerrar el pedido: {e.motivo}. "
                    + (
                        f"Dile al cliente que se acabó {agotados} y ofrécele cambiarlo "
                        "o quitarlo, y vuelve a confirmar."
                        if agotados
                        else "Arréglalo con el cliente y vuelve a confirmar."
                    )
                ),
            }
        except PedidosCaido as e:
            logger.error("[pedidos] no se pudo guardar: %s", e)
            return _respaldo(pedido, str(e))

    pedido.order_code = guardado.get("code")
    total = guardado.get("total")
    eta = guardado.get("eta") or "30 a 45 minutos"

    if guardado.get("duplicated"):
        # El pedido ya estaba: esto fue un segundo intento del modelo, no un
        # pedido nuevo. Se responde igual para que no lo anuncie dos veces.
        logger.info("[pedidos] confirm_order repetido para la llamada %s", pedido.call_id)

    pedido.items.clear()

    return {
        "success": True,
        "message": (
            f"Pedido confirmado a nombre de {customer_name}, con entrega en "
            f"{delivery_address}. El total es {_pesos(total)} con domicilio incluido, "
            f"y llega en aproximadamente {eta}. Dile el total y el tiempo al cliente."
        ),
        "codigo": pedido.order_code,
        "total": total,
        "eta": eta,
        "aviso_whatsapp": bool(guardado.get("notified")),
    }


def _pesos(valor) -> str:
    try:
        return f"${int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "el total"


def _respaldo(pedido: PedidoEnCurso, motivo: str) -> dict:
    """Cuando el sistema de pedidos no responde.

    Tres cosas, en este orden de importancia:

    1. **No se le miente al cliente.** El pedido no quedo guardado, y decir "ya
       quedo" es peor que decir la verdad: la comida no llega y nadie sabe por
       que.
    2. Se marca `respaldo_dado` para que `finalizar_llamada` **pueda colgar**.
       Sin esto el agente se queda sin poder despedirse, repitiendo el error.
    3. Se loguea el pedido completo en una linea estructurada. En Railway el
       disco es efimero (una cola en archivo no sobrevive al siguiente
       despliegue), asi que los logs son el unico sitio de donde se puede
       recuperar lo que el cliente alcanzo a dictar.
    """
    pedido.respaldo_dado = True

    logger.error(
        "[pedido-no-guardado] %s",
        json.dumps(
            {
                "motivo": motivo,
                "call_id": pedido.call_id,
                "nombre": pedido.customer_name,
                "telefono": pedido.customer_phone,
                "direccion": pedido.delivery_address,
                "pago": pedido.payment_method,
                "items": pedido.resumen(),
            },
            ensure_ascii=False,
        ),
    )

    whatsapp = pedido.catalogo.whatsapp if pedido.catalogo else ""
    numero = f" al WhatsApp {whatsapp}" if whatsapp else " por WhatsApp"

    return {
        "success": False,
        "message": (
            "El sistema de pedidos no está respondiendo y el pedido NO quedó guardado. "
            "Dile al cliente exactamente esto, sin prometerle que va a llegar: que se "
            f"te cayó el sistema, que no quieres dejarle el pedido a medias, y que nos "
            f"escriba{numero} con lo que te dijo para tomárselo ahí mismo. "
            "Después despídete con finalizar_llamada."
        ),
    }
