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
│  ┌─────────────────────┐ │        │  Room + SFU + Inference    │
│  │ agent-worker        │ │ ───────┤  (STT/LLM/TTS)             │
│  │ (Dockerfile raíz)   │ │  WS    └───────────▲────────────────┘
│  │ agent.py start      │ │  saliente          │
│  └──────────┬──────────┘ │                    │
│             │            │                    │
│  ┌──────────┴──────────┐ │        Navegador del cliente
│  │ web                 │◄├────────  (WebRTC directo a LiveKit,
│  │ (web/Dockerfile)    │ │           no pasa por Railway)
│  │ FastAPI + index.html│ │
│  └─────────────────────┘ │
└─────────────┬────────────┘
              │ HTTPS (INTERNAL_SECRET)
              ▼
   Vercel: demo-delivery-system (apps/menu)
   GET /api/internal/catalog · POST /api/internal/voice-order · GET /api/health
```

**Dos** piezas en Railway (`agent-worker` y `web`) — ya **no hace falta
Postgres**: esta rama no tiene base de datos propia. El catálogo y los pedidos
viven en el sistema de pedidos de Brasa & Pan, que ya está desplegado en
Vercel. LiveKit Cloud sigue siendo el mismo proyecto que usas en local.

> Si vienes de la rama `pedidos` y ya tenías un servicio de Postgres en este
> proyecto de Railway, bórralo **después** de comprobar que las llamadas
> funcionan: ya no se usa, y sigue costando.

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

## 1. Tener a mano lo del sistema de pedidos

No hay que crear ninguna base de datos. Lo que sí hace falta, del proyecto
`demo-delivery-system` (el que ya está en Vercel):

1. **La URL de la app del menú** — la que sirve `/api/internal/catalog`. Es la
   que va en `DELIVERY_API_URL`. Compruébala antes de seguir:
   ```bash
   curl https://<esa-url>/api/health
   # {"ok":true,"open":true,"products":25}
   ```
2. **El valor de `INTERNAL_SECRET`** de ese proyecto (Vercel → Settings →
   Environment Variables). Tiene que ir **idéntico** en Railway: si no
   coincide, el catálogo responde 401 y ninguna llamada arranca.
3. **El número de WhatsApp real del restaurante**, sin `+` ni espacios. Es a
   dónde se manda al cliente cuando algo falla; sin él, el respaldo dice
   "escríbenos por WhatsApp" sin poder dar a dónde.

## 2. Desplegar el worker del agente

1. En el mismo proyecto: **New** → **GitHub Repo** → selecciona
   `demo-voice-agent` → rama **`brasa-y-pan`** (la demo conectada al sistema
   de pedidos; `pedidos` es la versión con base de datos propia, `main` es el
   código original y `reservas` es la otra demo, en su propio proyecto).
2. Railway detecta el `Dockerfile` de la raíz automáticamente (Root
   Directory = `/`, el default). No hace falta tocar el build.
3. Ve a **Variables** de este servicio y agrega:
   ```
   LIVEKIT_URL=wss://tu-proyecto.livekit.cloud
   LIVEKIT_API_KEY=...
   LIVEKIT_API_SECRET=...
   DELIVERY_API_URL=https://<la-app-del-menu>.vercel.app
   INTERNAL_SECRET=<el mismo valor del sistema de pedidos>
   BUSINESS_WHATSAPP_NUMBER=57...
   LLM_MODEL=openai/gpt-4.1-mini
   STT_MODEL=deepgram/flux-general-multi
   AVISO_LEGAL=false
   ```
   `DELIVERY_API_URL` **sin barra al final** y sin el `/api` — el cliente
   arma las rutas por su cuenta.
4. Nómbralo algo claro, ej. `agent-worker`, y despliega. En los logs deberías
   ver que arranca y queda esperando jobs, sin errores de conexión a LiveKit.
5. **No** generes un dominio público para este servicio: no recibe tráfico
   HTTP entrante, solo se conecta hacia afuera.

## 3. Desplegar la interfaz web

1. En el mismo proyecto: **New** → **GitHub Repo** → mismo repo, misma rama
   `pedidos`, pero esta vez en **Settings** de ese servicio pon
   **Root Directory = `web`**. Railway usará `web/Dockerfile`.
2. Variables de este servicio:
   ```
   LIVEKIT_URL=wss://tu-proyecto.livekit.cloud
   LIVEKIT_API_KEY=...
   LIVEKIT_API_SECRET=...
   DELIVERY_API_URL=https://<la-app-del-menu>.vercel.app
   BUSINESS_WHATSAPP_NUMBER=57...
   ```
   Las dos últimas son para `GET /api/estado`: la página pregunta si el
   sistema de pedidos está arriba **antes** de dejar llamar, y si no lo está
   muestra el WhatsApp en vez de dejar entrar a una llamada que no puede
   terminar en pedido. Este servicio **no** necesita `INTERNAL_SECRET`: solo
   consulta `/api/health`, que es público.
3. Nómbralo `web`, despliega, y en **Settings → Networking** genera un
   dominio público (`Generate Domain`). Railway te da una URL tipo
   `web-production-xxxx.up.railway.app` con HTTPS ya incluido — necesario
   para que el navegador permita el micrófono.

## 4. Probar

1. Abre la URL pública del servicio `web`.
2. Toca el botón de llamar, acepta el permiso de micrófono.
3. Corre el guion de prueba de `docs/SPEC.md` § Criterios de aceptación:
   pedir productos, corregir algo, confirmar con nombre, dirección, celular y
   forma de pago, despedirte — la llamada debe cerrarse sola.
4. **Abre el portal de Brasa & Pan**: la comanda tiene que estar en "Nuevos"
   con su badge 📞, con las opciones en el renglón y el total correcto. Eso es
   lo que hay que poder mostrar; que la llamada suene bien no basta.
5. Si no conecta: revisa los logs de `agent-worker` (¿arrancó? ¿el catálogo
   cargó, o hay un `[arranque] sin sistema de pedidos`?) y de `web` (¿el
   `/api/token` devuelve 200? ¿`/api/estado` dice `ok:true`?), en ese orden.

## Si pide micrófono pero no se escucha nada (diagnóstico)

Este es el síntoma más confuso porque no tira un error visible: el botón de
llamar funciona, el navegador pide el micrófono, `/api/token` responde `200`
en los logs de `web`... y no pasa nada. Sigue estos pasos en orden — cada uno
descarta una causa distinta:

**1. Confirma que `/api/token` de verdad respondió 200 en el momento de la
   llamada** (no un 200 viejo de otra carga de página). Si no hay una línea
   `GET /api/token` justo cuando tocaste el botón, el problema está en el
   frontend (revisa la consola del navegador, punto 4).

**2. Revisa los logs de `agent-worker` buscando la línea `received job
   request`** justo después de dar clic en llamar.
   - Si **aparece** y luego hay un traceback: el agente sí fue despachado
     pero crashea al arrancar. Lee el traceback, es el caso más fácil de
     depurar.
   - Si aparece `[arranque] sin sistema de pedidos`: el agente entró, no pudo
     traer el catálogo, se disculpó y colgó. Es el respaldo funcionando.
     Revisa `DELIVERY_API_URL` e `INTERNAL_SECRET` (un 401 ahí es el error
     más común: el secreto no coincide con el de Vercel).
   - Si **no aparece nada** (solo `initializing process` / `process
     initialized` en bucle, sin `received job request`): el problema es que
     LiveKit nunca despachó el agente a la sala. Sigue al punto 3.

**3. Entra al dashboard de LiveKit Cloud** ([cloud.livekit.io](https://cloud.livekit.io)),
   al proyecto cuya URL coincide con el `LIVEKIT_URL` configurado en Railway
   (si manejas varios proyectos, es fácil confundirse de cuál es).
   - Ve a la sección de **Sessions** (o **Rooms**, el nombre exacto depende
     de la versión del dashboard) y confirma que se crea una sala nueva cada
     vez que alguien toca el botón de llamar en la web. Si **no aparece
     ninguna sala nueva**, el navegador no está llegando a este proyecto de
     LiveKit — revisa `LIVEKIT_URL` en `web` (typo, proyecto equivocado, o
     usando `wss://` de un proyecto viejo).
   - Si la sala **sí se crea** pero el agente nunca entra, entra al detalle
     de esa sala/sesión y mira los participantes: solo debería estar el
     participante del navegador. Esto confirma que el despacho explícito
     (`RoomAgentDispatch` con `agent_name="agente-pollo"`) no está
     alcanzando a ningún worker — revisa el punto 4.
   - Si el dashboard tiene alguna vista de **Agents/Workers** conectados,
     confirma ahí que `agent-worker` aparece como conectado/en línea. Si no
     aparece ninguno, el worker de Railway no está registrado contra este
     proyecto (aunque sus logs digan `registered worker` — puede estar
     registrado contra otro proyecto por credenciales equivocadas).

**4. Verifica que las credenciales sean idénticas en los dos servicios de
   Railway.** Abre **Variables** de `agent-worker` y de `web` lado a lado y
   compara carácter por carácter: `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
   `LIVEKIT_API_SECRET`. Deben apuntar exactamente al mismo proyecto de
   LiveKit Cloud. Un error de copiado (key de un proyecto, secret de otro,
   o una URL vieja) hace que la sala se cree en un proyecto y el worker esté
   escuchando en otro — sin ningún error visible en ninguno de los dos
   servicios.

**5. Revisa la consola del navegador** (F12 → pestaña **Console**, y
   **Network** filtrando por `WS`/`WebSocket`) justo al tocar el botón de
   llamar:
   - Un error al llamar `room.connect(...)` indica que el navegador no pudo
     abrir la conexión WebRTC hacia `LIVEKIT_URL` (revisa que sea `wss://` y
     no `ws://`, y que no haya un firewall/proxy corporativo bloqueando
     WebRTC si estás probando desde una red de oficina).
   - Si no hay errores y el estado de la sala se ve "conectado" desde el
     navegador, el problema no es de la web sino del despacho del agente
     (vuelve al punto 3).

**6. Si todo lo anterior coincide (mismas credenciales, mismo proyecto, sala
   se crea, worker registrado) y aun así no hay despacho:** confirma que el
   `agent_name` sea *exactamente* igual en los dos lados —
   `web/main.py:AGENT_NAME` y el `agent_name="..."` del decorador
   `@server.rtc_session(...)` en `agent.py` — un espacio o mayúscula distinta
   ya rompe el match. Si son iguales y sigue sin funcionar, es momento de
   escribirle a soporte de LiveKit con el ID de la sala que no recibió
   despacho (se ve en el dashboard, en el detalle de la sesión).

## Nota de seguridad para la demo

El endpoint `/api/token` no tiene autenticación: cualquiera con el link
público puede abrir una llamada (y consumir LLM/STT/TTS de tu proyecto de
LiveKit). Para una demo controlada, compartiendo el link solo con quien vaya
a probarla, es aceptable. Si el link se va a compartir más ampliamente o va
a quedar público indefinidamente, vale la pena agregar algo simple antes
(ej. una contraseña compartida que el frontend mande como header y
`web/main.py` valide antes de emitir el token) — no está implementado porque
no era parte de lo pedido.
