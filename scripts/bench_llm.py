"""Compara el time-to-first-token (TTFT) de varios LLM candidatos.

No usa AgentSession ni voz: llama directo al LLM con un prompt
representativo de toma de pedidos, para elegir LLM_MODEL con datos en vez
de teoria. Los cuatro modelos estan confirmados en el catalogo de
`livekit.agents.inference.llm` de la version instalada.

Uso:
    uv run python scripts/bench_llm.py
"""
import asyncio
import time

from dotenv import load_dotenv
from livekit.agents import inference
from livekit.agents.llm import ChatContext

load_dotenv()

MODELOS = [
    "openai/gpt-4.1-mini",  # el que usa agent.py hoy
    "openai/gpt-4.1-nano",
    "google/gemini-2.5-flash-lite",
    "google/gemini-3.1-flash-lite",
]

SYSTEM_PROMPT = (
    "Eres una tomadora de pedidos de una cadena de restaurantes de pollo. "
    "Habla en español, con respuestas cortas y naturales para una llamada "
    "telefónica. Nunca inventes productos ni precios."
)

# Turno representativo: el cliente ya pidio dos productos y pregunta el
# total, que es justo el tipo de turno donde el TTFT se nota en la demo.
TURNO_DE_PRUEBA = (
    "Quiero un combo familiar y una coca-cola. ¿Cuánto es el total?"
)

REPETICIONES = 3


def _build_chat_ctx() -> ChatContext:
    ctx = ChatContext.empty()
    ctx.add_message(role="system", content=SYSTEM_PROMPT)
    ctx.add_message(role="user", content=TURNO_DE_PRUEBA)
    return ctx


async def _medir_una_vez(model: str) -> tuple[float, float]:
    """Devuelve (ttft_segundos, duracion_total_segundos) de una llamada."""
    llm = inference.LLM(model=model)
    chat_ctx = _build_chat_ctx()

    inicio = time.perf_counter()
    ttft: float | None = None

    stream = llm.chat(chat_ctx=chat_ctx)
    async for chunk in stream:
        if ttft is None and chunk.has_response():
            ttft = time.perf_counter() - inicio
    duracion = time.perf_counter() - inicio

    await stream.aclose()
    await llm.aclose()

    return (ttft or duracion), duracion


async def _medir_modelo(model: str) -> dict:
    ttfts = []
    duraciones = []
    error = None
    for _ in range(REPETICIONES):
        try:
            ttft, duracion = await _medir_una_vez(model)
            ttfts.append(ttft)
            duraciones.append(duracion)
        except Exception as e:  # noqa: BLE001 - queremos seguir con los demas modelos
            error = f"{type(e).__name__}: {e}"
            break

    if error:
        return {"model": model, "error": error}

    return {
        "model": model,
        "ttft_ms": sum(ttfts) / len(ttfts) * 1000,
        "ttft_min_ms": min(ttfts) * 1000,
        "duracion_ms": sum(duraciones) / len(duraciones) * 1000,
    }


async def main():
    print(f"Turno de prueba: {TURNO_DE_PRUEBA!r}")
    print(f"Repeticiones por modelo: {REPETICIONES}\n")

    resultados = []
    for model in MODELOS:
        print(f"midiendo {model} ...")
        resultados.append(await _medir_modelo(model))

    print()
    print(f"{'modelo':<32} {'TTFT prom':>12} {'TTFT min':>12} {'duracion total':>16}")
    print("-" * 76)
    for r in resultados:
        if "error" in r:
            print(f"{r['model']:<32} ERROR: {r['error']}")
            continue
        print(
            f"{r['model']:<32} {r['ttft_ms']:>9.0f}ms {r['ttft_min_ms']:>9.0f}ms "
            f"{r['duracion_ms']:>13.0f}ms"
        )


if __name__ == "__main__":
    asyncio.run(main())
