"""Backend minimo para la interfaz web: sirve la pagina estatica y emite
tokens de LiveKit para que un navegador pueda hablar con el agente sin
necesidad de un numero de telefono.

Es un servicio aparte del agente de voz (agent.py), pensado para desplegarse
como un segundo servicio en Railway. No toca Postgres ni el pedido: solo
mintea credenciales de sala.
"""

import os
import secrets
import string
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import httpx
from livekit import api

load_dotenv()

# Debe coincidir exactamente con el AGENT_NAME de agent.py. Como ese decorador
# usa despacho explicito (no automatico), sin este nombre en el
# RoomAgentDispatch del token la sala quedaria vacia: el cliente se conectaria
# y ningun agente le contestaria, SIN ningun error visible. Si se cambia aca,
# hay que cambiarlo alla en el mismo commit.
AGENT_NAME = "agente-brasa"

# El numero al que se manda al cliente cuando la llamada no puede terminar en
# pedido. La pagina lo usa en los tres caminos de respaldo.
WHATSAPP_RESPALDO = os.getenv("BUSINESS_WHATSAPP_NUMBER", "")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()


def _codigo_corto(n: int = 6) -> str:
    alfabeto = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(n))


@app.get("/api/token")
def crear_token():
    """Crea una sala nueva y un token de cliente para esa sala.

    Cada llamada usa una sala nueva (un cliente = una llamada), igual que una
    llamada telefonica real no comparte linea con otra. El RoomAgentDispatch
    incluido en el propio token es lo que le pide a LiveKit que despache
    AGENT_NAME a la sala apenas el cliente entre, sin necesidad de una llamada
    aparte a la API de LiveKit para crear el dispatch.
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL")
    if not api_key or not api_secret or not livekit_url:
        raise HTTPException(
            status_code=500,
            detail="Faltan LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET en el entorno.",
        )

    # El nombre de la sala es lo que el agente manda como `callId` al crear el
    # pedido: es unico por llamada, y es lo que evita que un `confirm_order`
    # repetido duplique la comanda.
    room_name = f"brasa-{_codigo_corto()}"
    identity = f"cliente-{_codigo_corto()}"

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Cliente web")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_room_config(
            api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
    )

    return {
        "serverUrl": livekit_url,
        "roomName": room_name,
        "participantToken": token.to_jwt(),
    }


@app.get("/api/estado")
async def estado():
    """¿Se puede tomar un pedido por llamada ahora mismo?

    La pagina lo consulta ANTES de dejar llamar. Si el sistema de pedidos no
    responde, el boton queda deshabilitado y se muestra el WhatsApp: es mejor
    no dejar entrar a una llamada de cuatro minutos que no puede terminar en un
    pedido, que dejar al cliente contarle todo a un agente que no va a poder
    guardarlo.

    Nunca falla con 500: la respuesta siempre es un `ok` que la pagina pueda
    leer. Un error aca dejaria la pagina sin saber que hacer, que es peor que
    un "no".
    """
    return {"ok": await _pedidos_arriba(), "whatsapp": WHATSAPP_RESPALDO}


async def _pedidos_arriba() -> bool:
    """La misma comprobacion que hace `brasa/api.py`, repetida a proposito.

    Este servicio se construye con `web/` como contexto de build (Root
    Directory en Railway), asi que no puede importar `brasa/`. Acoplar los dos
    contextos para no repetir seis lineas de un GET saldria mas caro que
    repetirlas: el servicio web es deliberadamente autonomo — sirve la pagina y
    mintea tokens, nada mas.
    """
    base = (os.getenv("DELIVERY_API_URL") or "").rstrip("/")
    if not base:
        return False
    try:
        # 5 s y no 3: esto consulta el catálogo contra la base, y un falso
        # "caído" le cierra la puerta a un cliente que sí podía pedir.
        async with httpx.AsyncClient(timeout=5.0) as cliente:
            r = await cliente.get(f"{base}/api/health")
            return r.status_code == 200 and r.json().get("ok") is True
    except Exception:  # noqa: BLE001 - cualquier fallo es "no esta arriba"
        return False


# Al final: cualquier ruta que no sea /api/* sirve la pagina estatica
# (index.html incluido via html=True). Debe ir despues de las rutas de API
# para que estas tengan prioridad.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
