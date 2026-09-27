"""Comprueba el contrato entre las piezas, sin LiveKit ni base de datos.

Son las cosas que **se rompen en silencio**: no dan error, no tumban el
despliegue, y el sintoma es un cliente esperando frente a una pantalla que dice
"Conectando...". Por eso valen un script y no un comentario.

    uv run python scripts/verify_contrato.py

No necesita credenciales ni red: lee los archivos.
"""

import ast
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FALLOS: list[str] = []


def ok(etiqueta: str, condicion: bool, detalle: str = "") -> None:
    print(f"{'✓' if condicion else '✗'} {etiqueta}" + (f" — {detalle}" if detalle else ""))
    if not condicion:
        FALLOS.append(etiqueta)


def _constante(archivo: str, nombre: str) -> str | None:
    """Lee una constante de nivel de modulo sin importar el archivo.

    Se hace con `ast` y no importando porque `web/main.py` necesita FastAPI y
    `agent.py` necesita livekit-agents: son dos entornos distintos (dos
    servicios de Railway), y esta comprobacion tiene que poder correr en
    cualquiera de los dos.
    """
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name) and destino.id == nombre:
                    if isinstance(nodo.value, ast.Constant):
                        return nodo.value.value
    return None


print("── El agent_name: la trampa que no da error ──")
# El despacho es explicito. Si el nombre del token (web/main.py) y el del worker
# (agent.py) no son identicos, LiveKit no manda a nadie a la sala: el navegador
# se conecta bien, no hay excepcion en ningun log, y el cliente se queda
# esperando a alguien que nunca va a entrar.
del_agente = _constante("agent.py", "AGENT_NAME")
del_web = _constante("web/main.py", "AGENT_NAME")
ok("agent.py declara AGENT_NAME", bool(del_agente), str(del_agente))
ok("web/main.py declara AGENT_NAME", bool(del_web), str(del_web))
ok(
    "los dos son EXACTAMENTE el mismo",
    bool(del_agente) and del_agente == del_web,
    f"agent.py={del_agente!r} web={del_web!r}",
)

agente_py = (RAIZ / "agent.py").read_text(encoding="utf-8")
ok(
    "el decorador usa la constante, no el nombre a mano",
    "@server.rtc_session(agent_name=AGENT_NAME)" in agente_py,
)

print("\n── El call_id: la idempotencia del pedido ──")
# El nombre de la sala viaja como `callId` al crear el pedido, y es lo unico que
# evita que un `confirm_order` repetido mande la comanda dos veces a la cocina.
ok(
    "el agente usa el nombre de la sala como call_id",
    "call_id=ctx.room.name" in agente_py,
)
ok(
    "el call_id no se regenera dentro de la llamada",
    len(re.findall(r"call_id\s*=", agente_py)) == 1,
)

print("\n── Colgar: los dos caminos ──")
# El candado original (no colgar sin pedido confirmado) es correcto, pero si el
# sistema de pedidos se cae el agente se queda SIN PODER DESPEDIRSE, repitiendo
# el error mientras el cliente espera.
call_py = (RAIZ / "tools/call.py").read_text(encoding="utf-8")
ok(
    "finalizar_llamada puede cerrar con el pedido confirmado O con el respaldo dado",
    "pedido.order_code is None and not pedido.respaldo_dado" in call_py,
)
orders_py = (RAIZ / "tools/orders.py").read_text(encoding="utf-8")
ok("el respaldo marca respaldo_dado", "pedido.respaldo_dado = True" in orders_py)
ok(
    "y loguea el pedido que no se guardo, para poder recuperarlo",
    "[pedido-no-guardado]" in orders_py,
)

print("\n── El agente no manda cifras, manda referencias (RN-14) ──")
ok(
    "las lineas que se envian son sku + optionIds + quantity",
    '{"sku": i.sku, "optionIds": i.option_ids, "quantity": i.quantity}' in orders_py,
)
ok(
    "no se manda ningun precio al crear el pedido",
    "unit_price" not in orders_py.split("def para_el_sistema")[1].split("def ")[0],
)

print("\n── Ya no hay base de datos propia ──")
ok("database/ borrado", not (RAIZ / "database").exists())
ok("docker-compose.yml borrado", not (RAIZ / "docker-compose.yml").exists())
ok(
    "asyncpg fuera de las dependencias",
    "asyncpg" not in (RAIZ / "pyproject.toml").read_text(encoding="utf-8"),
)
ok(
    "el respaldo de WhatsApp esta en la pagina",
    "wa.me" in (RAIZ / "web/static/index.html").read_text(encoding="utf-8"),
)

if FALLOS:
    print(f"\n{len(FALLOS)} fallo(s): " + ", ".join(FALLOS))
    sys.exit(1)
print("\nTodo el contrato se cumple.")
