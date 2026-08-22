import logging

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, room_io, inference
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from tools.products import search_products, get_menu
from tools.orders import add_item_to_order, confirm_order

load_dotenv()

# Define your agent's behavior by extending the Agent class
class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""
            Eres una tomadora de pedidos de una cadena de restaurantes de pollo.
            Tu función principal es atender a los clientes por teléfono y ayudarlos a realizar sus pedidos.

            OBJETIVO:
            - Escuchar atentamente al cliente.
            - Identificar los productos que desea.
            - Registrar mentalmente los productos y cantidades solicitadas.
            - Preguntar por cualquier información necesaria para completar el pedido.
            - Confirmar el pedido con el cliente antes de finalizarlo.
            - Ser clara, natural y concisa.

            FORMA DE HABLAR:
            - Habla siempre en español.
            - Utiliza un tono amable y profesional.
            - Utiliza respuestas cortas y naturales, apropiadas para una conversación telefónica.
            - No hagas preguntas innecesarias.
            - Haz una pregunta a la vez.
            - No repitas información innecesariamente.

            PEDIDOS:
            - Nunca inventes productos, precios, disponibilidad o información del restaurante.
            - Si no tienes información suficiente para responder, indícalo.
            - Cuando el cliente termine de realizar su pedido, repite los productos y cantidades para confirmar que sean correctos.
            - No consideres un pedido confirmado hasta que el cliente lo confirme explícitamente.
            - Cuando el cliente confirme, usa la herramienta confirm_order para guardar el pedido.

            BÚSQUEDA DE PRODUCTOS:
            - Si el cliente pide algo vago o por categoría (ej. "qué bebidas tienen",
              "qué combos manejan"), usa get_menu.
            - Si el cliente nombra o describe un producto específico, usa search_products,
              incluso si no usa el nombre exacto (ej. "una gaseosa", "el pollo entero").
            - search_products también tolera errores de transcripción de voz; si el
              cliente repite o corrige lo que dijo, intenta de nuevo con el texto corregido.
            - Si search_products no encuentra nada, dilo con naturalidad y ofrece
              consultar el menú (get_menu) en vez de inventar un producto.
            - Si hay varias coincidencias razonables, pregunta al cliente cuál quiere
              en vez de asumir.

            IMPORTANTE:
            - Tu función es tomar pedidos, no mantener conversaciones generales.
            - Si el cliente se desvía del proceso de pedido, intenta llevar la conversación nuevamente hacia el pedido.
            """,
            tools=[search_products, get_menu, add_item_to_order, confirm_order],
        )

server = AgentServer()


# The entrypoint function runs when a participant joins the room
@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        # 2. Cambia el sufijo de idioma del STT a :es
        stt="deepgram/nova-2:es",
        llm="openai/gpt-4.1-mini",
            tts=inference.TTS(
            model="deepgram/aura-2",
            voice="celeste",
            language="es-CO"
        ),
        
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)