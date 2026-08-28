# Desplegar en Railway (la forma más barata)

Guía paso a paso para poner en línea el agente + la interfaz web, sin
depender de un número de teléfono. Pensada para seguirse una sola vez desde
el dashboard de Railway; no requiere su CLI (aunque se menciona como
alternativa donde ayuda).

## Qué se despliega y dónde

```
┌─────────────────────────┐        ┌──────────────────────────┐
│  Railway                │        │  LiveKit Cloud            │
│                          │        │  (ya lo tienes, no cambia)│
│  ┌────────────────────┐ │        │                            │
│  │ Postgres (plugin)   │ │        │  Room + SFU + Inference    │
│  └─────────▲──────────┘ │        │  (STT/LLM/TTS)             │
│            │            │        └───────────▲────────────────┘
│  ┌─────────┴──────────┐ │                    │ WebSocket saliente
│  │ agent-worker        │ │ ───────────────────┘
│  │ (Dockerfile raíz)   │ │
│  │ agent.py start      │ │
│  └─────────────────────┘ │
│                          │
│  ┌─────────────────────┐ │        Navegador del cliente
│  │ web                 │◄├────────  (WebRTC directo a LiveKit,
│  │ (web/Dockerfile)    │ │           no pasa por Railway)
│  │ FastAPI + index.html│ │
│  └─────────────────────┘ │
└──────────────────────────┘
```

Tres piezas nuevas en Railway (Postgres, `agent-worker`, `web`); LiveKit Cloud
sigue siendo el mismo proyecto que ya usas en local — no hay que crear otro.

**Por qué no se autohospeda LiveKit (el servidor de media) en Railway:**
WebRTC necesita rango de puertos UDP para el audio; Railway solo expone
TCP/HTTP hacia afuera. Autohospedar el SFU ahí sería frágil y más caro de
mantener que simplemente seguir en LiveKit Cloud, que ya tiene un tier
gratuito generoso para el volumen de una demo.

## Sobre el costo

Railway cobra por uso real (CPU/RAM/segundos), no una tarifa fija por
servicio — trae de entrada un plan con un crédito mensual pequeño, y luego es
prepago por lo que consumas. Como los precios y el crédito incluido cambian
con el tiempo, confirma las cifras actuales en
[railway.app/pricing](https://railway.app/pricing) antes de decidir cuánto
vas a gastar; no lo repito aquí para no darte un número que ya esté
desactualizado cuando lo leas.

Para mantenerlo barato en una demo (no un servicio 24/7 con tráfico real):

- Las dos imágenes (`Dockerfile` del agente y `web/Dockerfile`) son
  deliberadamente livianas (`python:3.14-slim`, sin dependencias de más).
- El worker del agente casi no consume CPU en reposo (solo mantiene un
  WebSocket abierto hacia LiveKit); el gasto real ocurre durante una llamada.
- Railway no duerme automáticamente los servicios "worker" (sin tráfico
  HTTP), así que si vas a hacer la demo un día puntual y no necesitas el
  agente corriendo el resto del mes, la forma más simple de no seguir
  gastando es **pausar los servicios entre demos**: en cada servicio, menú
  `⋯` → detener/eliminar el deployment activo, y volver a desplegar (o
  simplemente hacer `git push` de nuevo) el día que la necesites. Los datos
  de Postgres no se pierden al pausar `agent-worker` o `web`, solo si borras
  el propio servicio de Postgres.

## 0. Prerequisitos

- Tu repo ya está en GitHub (`Jdavidruanob/demo-voice-agent`) — Railway
  despliega directo desde ahí, no hace falta subir código a mano.
- Una cuenta en [railway.app](https://railway.app) (puedes entrar con GitHub).
- Las credenciales de tu proyecto de LiveKit Cloud, que ya tienes en tu
  `.env` local (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`).

## 1. Crear el proyecto y Postgres

1. En Railway: **New Project** → **Deploy PostgreSQL** (el template oficial,
   no lo conectes a un repo, es solo la base de datos).
2. Cuando termine de aprovisionar, entra al servicio de Postgres → pestaña
   **Variables** y anota los nombres exactos que expone (normalmente
   `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`) — los vas a
   referenciar desde el servicio del agente en el paso 3.
3. Carga el esquema. La forma más simple: pestaña **Connect** del servicio
   de Postgres → copia la URL de conexión pública (`postgresql://...`) y
   corre desde tu máquina:
   ```bash
   psql "postgresql://usuario:password@host:puerto/railway" -f database/schema.sql
   ```
   (Si tu Railway tiene una pestaña **Data**/consulta integrada, también
   puedes pegar el contenido de `database/schema.sql` ahí directamente.)

## 2. Desplegar el worker del agente

1. En el mismo proyecto: **New** → **GitHub Repo** → selecciona
   `demo-voice-agent` → rama **`feat/demo-fluidez`** (ahí ya está todo lo
   fusionado; no despliegues `llamada-activada` ni `main`).
2. Railway detecta el `Dockerfile` de la raíz automáticamente (Root
   Directory = `/`, el default). No hace falta tocar el build.
3. Ve a **Variables** de este servicio y agrega:
   ```
   LIVEKIT_URL=wss://tu-proyecto.livekit.cloud
   LIVEKIT_API_KEY=...
   LIVEKIT_API_SECRET=...
   DB_HOST=${{Postgres.PGHOST}}
   DB_PORT=${{Postgres.PGPORT}}
   DB_USER=${{Postgres.PGUSER}}
   DB_PASSWORD=${{Postgres.PGPASSWORD}}
   DB_NAME=${{Postgres.PGDATABASE}}
   LLM_MODEL=openai/gpt-4.1-mini
   STT_MODEL=deepgram/flux-general-multi
   AVISO_LEGAL=false
   ```
   `${{Postgres.PGHOST}}` es la sintaxis de Railway para referenciar la
   variable de **otro** servicio del mismo proyecto — usa el nombre que le
   puso Railway a tu servicio de Postgres (por defecto suele llamarse
   `Postgres`) y los nombres de variable que anotaste en el paso 1.2.
4. Nómbralo algo claro, ej. `agent-worker`, y despliega. En los logs deberías
   ver que arranca y queda esperando jobs (sin errores de conexión a
   Postgres ni a LiveKit).
5. **No** generes un dominio público para este servicio: no recibe tráfico
   HTTP entrante, solo se conecta hacia afuera.

## 3. Desplegar la interfaz web

1. En el mismo proyecto: **New** → **GitHub Repo** → mismo repo, misma rama
   `feat/demo-fluidez`, pero esta vez en **Settings** de ese servicio pon
   **Root Directory = `web`**. Railway usará `web/Dockerfile`.
2. Variables de este servicio (son las únicas tres que necesita):
   ```
   LIVEKIT_URL=wss://tu-proyecto.livekit.cloud
   LIVEKIT_API_KEY=...
   LIVEKIT_API_SECRET=...
   ```
3. Nómbralo `web`, despliega, y en **Settings → Networking** genera un
   dominio público (`Generate Domain`). Railway te da una URL tipo
   `web-production-xxxx.up.railway.app` con HTTPS ya incluido — necesario
   para que el navegador permita el micrófono.

## 4. Probar

1. Abre la URL pública del servicio `web`.
2. Toca el botón de llamar, acepta el permiso de micrófono.
3. Corre el guion de prueba de `docs/SPEC.md` § Criterios de aceptación
   (escenario 11): pedir productos, corregir algo, confirmar con nombre y
   dirección, despedirte — la llamada debe cerrarse sola.
4. Si no conecta: revisa los logs de `agent-worker` (¿arrancó? ¿se conectó a
   Postgres?) y de `web` (¿el `/api/token` devuelve 200?), en ese orden.

## Nota de seguridad para la demo

El endpoint `/api/token` no tiene autenticación: cualquiera con el link
público puede abrir una llamada (y consumir LLM/STT/TTS de tu proyecto de
LiveKit). Para una demo controlada, compartiendo el link solo con quien vaya
a probarla, es aceptable. Si el link se va a compartir más ampliamente o va
a quedar público indefinidamente, vale la pena agregar algo simple antes
(ej. una contraseña compartida que el frontend mande como header y
`web/main.py` valide antes de emitir el token) — no está implementado porque
no era parte de lo pedido.
