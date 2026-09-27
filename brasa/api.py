"""Cliente HTTP del sistema de pedidos de Brasa & Pan.

El agente de voz **no toca la base de datos**. Dos razones:

1. Duplicar la resolucion de precios seria duplicar las promociones, y la
   segunda copia se quedaria atras en silencio: el cliente oiria un precio por
   telefono y pagaria otro. Ese calculo vive en un solo sitio, en el servidor.
2. Un repo en Python con las credenciales de la base de produccion es una
   superficie que no hace falta abrir. Con un secreto compartido y dos
   endpoints alcanza.

Lo unico que este modulo sabe hacer es preguntar y avisar. Lo que decide es
siempre el otro lado.
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("agent")

# El catalogo se pide una sola vez al arrancar la llamada: si tarda mas que
# esto, el cliente ya esta esperando en silencio y es mejor caer al respaldo.
TIMEOUT_CATALOGO = 5.0

# Confirmar el pedido es la unica llamada que no se puede perder: el cliente ya
# dicto todo. Se le da mas margen y se reintenta.
TIMEOUT_CONFIRMAR = 10.0
REINTENTOS_CONFIRMAR = 2

TIMEOUT_SALUD = 3.0


class PedidosCaido(Exception):
    """El sistema de pedidos no contesto o contesto algo que no se entiende.

    Es el unico error del que el agente no puede salir hablando: significa que
    el pedido no quedo guardado en ningun lado.
    """


class PedidoRechazado(Exception):
    """El sistema entendio el pedido y lo rechazo, con un motivo que se puede
    decir en voz alta ("no hay Cerveza Nacional", "falta el termino de la
    carne").

    Es un error **esperado**, no una falla: el agente lo convierte en una
    pregunta al cliente.
    """

    def __init__(self, motivo: str, agotados: list[str] | None = None) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.agotados = agotados or []


class PedidosAPI:
    """Un cliente por proceso. El `AsyncClient` se reusa para no pagar el
    handshake TLS en cada llamada (el arranque de la sesion y la confirmacion
    son los dos momentos donde mas se nota)."""

    def __init__(self, base_url: str | None = None, secret: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("DELIVERY_API_URL") or "").rstrip("/")
        self.secret = secret or os.getenv("INTERNAL_SECRET") or ""
        self._client: httpx.AsyncClient | None = None

    @property
    def configurado(self) -> bool:
        return bool(self.base_url and self.secret)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.secret}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def esta_arriba(self) -> bool:
        """¿Se pueden tomar pedidos ahora mismo?

        Lo consulta la pagina web antes de dejar hablar al cliente: mejor no
        dejar entrar a una llamada de cuatro minutos que no puede terminar en
        un pedido. Nunca lanza: si algo falla, la respuesta es "no".
        """
        if not self.configurado:
            return False
        try:
            r = await self._http().get("/api/health", timeout=TIMEOUT_SALUD)
            return r.status_code == 200 and r.json().get("ok") is True
        except Exception as e:  # noqa: BLE001 - cualquier fallo es "no esta arriba"
            logger.warning("[pedidos] /api/health no contesto: %s", e)
            return False

    async def catalogo(self) -> dict:
        """El catalogo con el precio de hoy ya resuelto por el servidor.

        Lanza `PedidosCaido` si no se pudo traer. No se reintenta: el cliente
        esta esperando el saludo, y un catalogo que tarda 15 segundos es peor
        que un "escribenos por WhatsApp" inmediato.
        """
        if not self.configurado:
            raise PedidosCaido("Falta DELIVERY_API_URL o INTERNAL_SECRET en el entorno.")

        try:
            r = await self._http().get("/api/internal/catalog", timeout=TIMEOUT_CATALOGO)
        except Exception as e:  # noqa: BLE001
            raise PedidosCaido(f"no se pudo traer el catalogo: {e}") from e

        if r.status_code == 401:
            raise PedidosCaido("INTERNAL_SECRET no coincide con el del sistema de pedidos.")
        if r.status_code != 200:
            raise PedidosCaido(f"el catalogo respondio {r.status_code}")

        try:
            return r.json()
        except Exception as e:  # noqa: BLE001
            raise PedidosCaido(f"el catalogo no vino en JSON: {e}") from e

    async def crear_pedido(
        self,
        *,
        call_id: str,
        customer_name: str | None,
        phone: str | None,
        address: str,
        address_notes: str | None,
        payment_method: str | None,
        items: list[dict],
    ) -> dict:
        """Crea el pedido en el sistema y devuelve lo que quedo guardado.

        **El total de la respuesta es el que hay que decirle al cliente**, no el
        que sumo el agente: si una promocion se vencio en medio de la llamada,
        el numero que se dice en voz alta y el que quedo en la comanda son el
        mismo.

        `call_id` es el `room_name` de LiveKit. Es lo que hace esta llamada
        idempotente: el modelo invoca `confirm_order` dos veces de vez en
        cuando, y mandar el mismo `call_id` es lo que evita que la cocina reciba
        la comanda repetida. **Nunca se genera uno nuevo en un reintento.**

        Lanza `PedidoRechazado` (422: algo del pedido no se puede cobrar) o
        `PedidosCaido` (no se pudo guardar en ningun lado).
        """
        if not self.configurado:
            raise PedidosCaido("Falta DELIVERY_API_URL o INTERNAL_SECRET en el entorno.")

        cuerpo = {
            "callId": call_id,
            "customerName": customer_name,
            "phone": phone or "",
            "address": address,
            "addressNotes": address_notes,
            "paymentMethod": payment_method,
            "items": items,
        }

        ultimo_error: Exception | None = None
        for intento in range(1, REINTENTOS_CONFIRMAR + 2):
            try:
                r = await self._http().post(
                    "/api/internal/voice-order",
                    json=cuerpo,
                    timeout=TIMEOUT_CONFIRMAR,
                )
            except Exception as e:  # noqa: BLE001 - red: se reintenta
                ultimo_error = e
                logger.warning("[pedidos] intento %s fallo: %s", intento, e)
                await self._esperar(intento)
                continue

            if r.status_code in (200, 201):
                return r.json()

            # 422: el pedido llego bien y el sistema lo rechazo con un motivo.
            # Reintentar daria el mismo resultado; lo que hace falta es que el
            # agente se lo pregunte al cliente.
            if r.status_code == 422:
                datos = _json_seguro(r)
                raise PedidoRechazado(
                    datos.get("reason") or "algo del pedido no esta disponible",
                    agotados=datos.get("unavailable") or [],
                )

            # 4xx que no sea 422 es un error nuestro (falta la direccion, el
            # secreto no sirve): reintentar no lo arregla.
            if 400 <= r.status_code < 500:
                raise PedidosCaido(
                    f"el sistema rechazo la peticion ({r.status_code}): "
                    f"{_json_seguro(r).get('error', 'sin detalle')}"
                )

            ultimo_error = PedidosCaido(f"el sistema respondio {r.status_code}")
            logger.warning("[pedidos] intento %s respondio %s", intento, r.status_code)
            await self._esperar(intento)

        raise PedidosCaido(f"no se pudo guardar el pedido: {ultimo_error}")

    @staticmethod
    async def _esperar(intento: int) -> None:
        """Backoff corto. En una llamada en curso no se puede esperar mucho: el
        cliente esta al telefono oyendo silencio."""
        if intento <= REINTENTOS_CONFIRMAR:
            await asyncio.sleep(0.4 * intento)


def _json_seguro(r: httpx.Response) -> dict:
    try:
        datos = r.json()
        return datos if isinstance(datos, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
