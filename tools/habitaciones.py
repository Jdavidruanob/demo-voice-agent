from datetime import date

import asyncpg
from livekit.agents import RunContext, function_tool
from database.connection import create_pool
from tools.formato import formatear_cantidad, formatear_habitaciones, formatear_precio
from tools.reservas import ReservaEnCurso

# Mensaje de respaldo cuando Postgres no responde (conexion caida, base
# ocupada). El agente lo dice tal cual y sigue la llamada en vez de
# colgarse o dejar un silencio muerto.
MENSAJE_FALLBACK_DB = (
    "En este momento no puedo acceder al sistema de reservas, pero puedo "
    "tomar tus datos y te confirmamos apenas se restablezca."
)


def _parsear_fecha(valor: str, campo: str) -> date:
    try:
        return date.fromisoformat(valor.strip())
    except (ValueError, AttributeError):
        raise ValueError(f"La fecha de {campo} debe tener formato AAAA-MM-DD.")


async def _catalogo_rows() -> list[dict]:
    pool = await create_pool()
    rows = await pool.fetch(
        """
        SELECT id, tipo_habitacion, descripcion, capacidad, precio_noche, disponible
        FROM habitaciones
        WHERE disponible > 0
        ORDER BY precio_noche;
        """
    )
    return [dict(row) for row in rows]


async def _servicios_rows() -> list[dict]:
    pool = await create_pool()
    rows = await pool.fetch("SELECT nombre, detalle FROM servicios_hotel ORDER BY id;")
    return [dict(row) for row in rows]


async def build_servicios_prompt_block() -> str:
    """Arma la info general del hotel (horarios, servicios) como texto para
    las instructions del agente.

    Igual que el catalogo de habitaciones: es informacion que no cambia
    durante una llamada, asi que se consulta una sola vez al arrancar la
    sesion en vez de ser una tool. Ver build_catalogo_prompt_block para el
    manejo de caida de Postgres (misma logica aqui).
    """
    try:
        rows = await _servicios_rows()
    except (asyncpg.PostgresError, OSError):
        return ""
    if not rows:
        return ""

    lineas = ["INFORMACIÓN GENERAL DEL HOTEL:"]
    for r in rows:
        lineas.append(f"  - {r['nombre']}: {r['detalle']}")
    return "\n".join(lineas)


async def build_catalogo_prompt_block() -> str:
    """Arma el catalogo de tipos de habitacion (sin verificar fechas) como
    texto listo para las instructions del agente.

    Se llama una sola vez al iniciar la sesion, igual que el menu en la
    version anterior del proyecto: sirve para responder preguntas generales
    de precio/capacidad sin gastar un roundtrip de tool. La disponibilidad
    real para fechas concretas siempre pasa por consultar_disponibilidad,
    porque eso si cambia durante la llamada.

    A diferencia de las tools, esta funcion corre antes de que la sesion
    arranque (no hay RunContext ni ToolError disponibles), asi que si
    Postgres esta caido justo al iniciar una llamada, una excepcion sin
    capturar aqui tumbaria la sesion entera antes del saludo. Por eso se
    atrapa el error de conexion y se devuelve un catalogo de respaldo en vez
    de dejar que la llamada muera en silencio.
    """
    try:
        rows = await _catalogo_rows()
    except (asyncpg.PostgresError, OSError):
        return (
            "No se pudo cargar el catálogo de habitaciones (base de datos no disponible). "
            f"Informa al cliente: \"{MENSAJE_FALLBACK_DB}\""
        )
    if not rows:
        return "No hay tipos de habitacion cargados; informa al cliente que no puedes verificar disponibilidad."

    lineas = ["TIPOS DE HABITACION (tarifa por noche; la disponibilidad exacta se confirma con consultar_disponibilidad):"]
    for r in rows:
        lineas.append(
            f"  - {r['tipo_habitacion']}: hasta {r['capacidad']} huéspedes, "
            f"{formatear_precio(r['precio_noche'])} la noche. {r['descripcion']}"
        )
    return "\n".join(lineas)


@function_tool
async def consultar_disponibilidad(
    ctx: RunContext[ReservaEnCurso],
    fecha_entrada: str,
    fecha_salida: str,
    num_huespedes: int,
):
    """Consulta que tipos de habitacion hay libres para un rango de fechas.

    fecha_entrada y fecha_salida van en formato AAAA-MM-DD (convierte lo que
    diga el cliente a ese formato antes de llamar la tool). Devuelve hasta
    2 opciones ordenadas por precio, con la tarifa por noche y el total ya
    calculado para esas noches, mas un texto ("resumen") listo para leerle
    al cliente.
    """

    try:
        entrada = _parsear_fecha(fecha_entrada, "entrada")
        salida = _parsear_fecha(fecha_salida, "salida")
    except ValueError as e:
        return {"disponible": False, "opciones": [], "resumen": str(e)}

    if salida <= entrada:
        return {
            "disponible": False,
            "opciones": [],
            "resumen": "La fecha de salida debe ser posterior a la de entrada.",
        }
    if num_huespedes < 1:
        return {
            "disponible": False,
            "opciones": [],
            "resumen": "El número de huéspedes debe ser al menos 1.",
        }

    noches = (salida - entrada).days

    async with ctx.with_filler("A ver, reviso disponibilidad...", delay=0.6):
        try:
            pool = await create_pool()
            rows = await pool.fetch(
                """
                SELECT
                    h.id, h.tipo_habitacion, h.descripcion, h.capacidad, h.precio_noche,
                    h.disponible - COALESCE((
                        SELECT COUNT(*) FROM reservas r
                        WHERE r.tipo_habitacion_id = h.id
                          AND r.estado != 'cancelada'
                          AND r.fecha_entrada < $2
                          AND r.fecha_salida > $1
                    ), 0) AS cupos_libres
                FROM habitaciones h
                WHERE h.capacidad >= $3
                ORDER BY h.precio_noche
                """,
                entrada,
                salida,
                num_huespedes,
            )
        except (asyncpg.PostgresError, OSError):
            return {"disponible": False, "opciones": [], "resumen": MENSAJE_FALLBACK_DB}

    opciones = []
    for r in rows:
        if r["cupos_libres"] <= 0:
            continue
        total = r["precio_noche"] * noches
        opciones.append(
            {
                "tipo_habitacion": r["tipo_habitacion"],
                "descripcion": r["descripcion"],
                "capacidad": r["capacidad"],
                "precio_noche": r["precio_noche"],
                "noches": noches,
                "total": total,
            }
        )
        if len(opciones) == 2:
            break

    # Guarda lo ya preguntado en el estado de la llamada: si el cliente
    # elige una opcion, crear_reserva no tiene que volver a pedir fechas ni
    # numero de huespedes.
    ctx.userdata.fecha_entrada = fecha_entrada
    ctx.userdata.fecha_salida = fecha_salida
    ctx.userdata.num_huespedes = num_huespedes

    if not opciones:
        return {
            "disponible": False,
            "opciones": [],
            "resumen": (
                f"No hay habitaciones libres para {formatear_cantidad(num_huespedes, 'huésped', 'huéspedes')} "
                f"en esas fechas."
            ),
        }

    partes = [
        f"{formatear_habitaciones(1, o['tipo_habitacion'])} a {formatear_precio(o['precio_noche'])} la noche "
        f"({formatear_cantidad(o['noches'], 'noche')}, {formatear_precio(o['total'])} en total)"
        for o in opciones
    ]
    resumen = "Hay disponible: " + "; y ".join(partes) + "."

    return {"disponible": True, "opciones": opciones, "resumen": resumen}
