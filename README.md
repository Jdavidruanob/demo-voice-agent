# Demo — Agente de voz para tomar pedidos

Agente de voz construido con [LiveKit Agents](https://docs.livekit.io/agents/) que atiende
llamadas de una cadena de restaurantes de pollo: escucha al cliente en español, consulta el
menú en Postgres, arma el pedido y lo guarda cuando el cliente lo confirma.

## Cómo funciona

```
Cliente (voz)
    │
    ▼
LiveKit Inference ──  STT: deepgram/nova-2:es
                      LLM: openai/gpt-4.1-mini
                      TTS: deepgram/aura-2 (voz "celeste", es-CO)
    │
    ▼
Assistant (agent.py) ── function tools ──▶ Postgres
                          search_products
                          get_menu
                          add_item_to_order
                          confirm_order
```

Detalles de la conversación:

- **Detección de turno** con `MultilingualModel`, para saber cuándo el cliente terminó
  de hablar sin cortarlo a mitad de frase.
- **VAD** con Silero y **cancelación de ruido** BVC, pensado para audio de teléfono.
- **Búsqueda tolerante a errores**: `search_products` usa la extensión `pg_trgm` de
  Postgres más una columna de `keywords`, así que entiende sinónimos ("gaseosa" →
  Coca-Cola) y transcripciones imperfectas ("polo asado" → Pollo Asado).

## Estructura

```
agent.py                 Prompt del agente, sesión y configuración de voz
database/connection.py   Pool de conexiones a Postgres (singleton)
database/schema.sql      Tablas, índices trigram y productos de ejemplo
tools/products.py        search_products, get_menu
tools/orders.py          add_item_to_order, confirm_order
docker-compose.yml       Postgres 17 para desarrollo local
```

## Requisitos

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Docker (para la base de datos local)
- Una cuenta de [LiveKit Cloud](https://cloud.livekit.io)

## Puesta en marcha

**1. Instalar dependencias**

```bash
uv sync
```

**2. Configurar las variables de entorno**

```bash
cp .env.example .env
```

Completa `LIVEKIT_URL`, `LIVEKIT_API_KEY` y `LIVEKIT_API_SECRET` con las credenciales de
tu proyecto en LiveKit Cloud (Settings → Keys). Las variables de base de datos ya vienen
con los valores que levanta `docker-compose.yml`.

**3. Levantar Postgres y cargar el esquema**

```bash
docker compose up -d
docker compose exec -T postgres psql -U admin -d chicken_store < database/schema.sql
```

**4. Descargar los modelos locales** (VAD y detección de turno, solo la primera vez)

```bash
uv run agent.py download-files
```

**5. Ejecutar el agente**

```bash
# Conversación por terminal, sin necesidad de un frontend
uv run agent.py console

# Modo desarrollo: se conecta a tu proyecto de LiveKit y recarga al guardar
uv run agent.py dev
```

Con `dev`, conéctate desde cualquier cliente de LiveKit — por ejemplo el
[Agents Playground](https://agents-playground.livekit.io) — usando el mismo proyecto.

## Notas

- `.env` está en `.gitignore` a propósito: nunca subas tus claves al repositorio.
- El pedido en curso vive en una lista global dentro de `tools/orders.py`. Funciona para
  una llamada a la vez; para atender varias llamadas en paralelo en el mismo proceso, ese
  estado debería moverse a `session.userdata`.
- Las credenciales del `docker-compose.yml` (`admin` / `password`) son solo para
  desarrollo local.
