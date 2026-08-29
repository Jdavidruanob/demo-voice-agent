from dataclasses import dataclass
from datetime import date

import asyncpg
from livekit.agents import RunContext, ToolError, function_tool
from database.connection import create_pool
from tools.formato import formatear_cantidad, formatear_habitaciones, formatear_precio

# Mensaje de respaldo cuando Postgres no responde (conexion caida, base
# ocupada). El agente lo dice tal cual y sigue la llamada en vez de
# colgarse o dejar un silencio muerto.
MENSAJE_FALLBACK_DB = (
    "En este momento no puedo acceder al sistema de reservas, pero puedo "
    "tomar tus datos y te confirmamos apenas se restablezca."
)


@dataclass
class ReservaEnCurso:
    """Estado de la llamada en curso.

    Vive en `session.userdata`, no en un global de modulo: cada llamada
    (cada AgentSession) tiene su propia instancia, asi que dos llamadas
    simultaneas en el mismo proceso nunca comparten ni mezclan su reserva.
    fecha_entrada/fecha_salida/num_huespedes se llenan al llamar
    consultar_disponibilidad, para que crear_reserva no tenga que volver a
    preguntarlos si ya se establecieron antes en la misma conversacion.
    customer_phone_sip se llena solo si la llamada entra por telefonia SIP;
    en console/playground queda en None.
    """

    fecha_entrada: str | None = None
    fecha_salida: str | None = None
    num_huespedes: int | None = None
    customer_phone_sip: str | None = None


def _parsear_fecha(valor: str, campo: str) -> date:
    try:
        return date.fromisoformat(valor.strip())
    except (ValueError, AttributeError):
        raise ValueError(f"La fecha de {campo} debe tener formato AAAA-MM-DD.")


@function_tool
async def crear_reserva(
    ctx: RunContext[ReservaEnCurso],
    cliente_nombre: str,
    cliente_telefono: str,
    tipo_habitacion: str,
    fecha_entrada: str,
    fecha_salida: str,
    num_huespedes: int,
):
    """Confirma y guarda la reserva en la base de datos.

    Usa esta herramienta solo despues de haber verificado disponibilidad con
    consultar_disponibilidad, de que el cliente eligio un tipo de habitacion
    de las opciones presentadas, y de haberle pedido su nombre completo y un
    telefono de contacto. No inventes ni asumas estos dos datos: pidelos
    siempre, incluso si el cliente los menciono de pasada antes.
    fecha_entrada y fecha_salida van en formato AAAA-MM-DD.
    """

    cliente_nombre = cliente_nombre.strip()
    cliente_telefono = cliente_telefono.strip()
    if not cliente_nombre or not cliente_telefono:
        return {
            "success": False,
            "message": (
                "Antes de confirmar necesito el nombre completo del huésped "
                "y un teléfono de contacto."
            ),
        }

    try:
        entrada = _parsear_fecha(fecha_entrada, "entrada")
        salida = _parsear_fecha(fecha_salida, "salida")
    except ValueError as e:
        return {"success": False, "message": str(e)}

    if salida <= entrada:
        return {
            "success": False,
            "message": "La fecha de salida debe ser posterior a la de entrada.",
        }
    if num_huespedes < 1:
        return {"success": False, "message": "El número de huéspedes debe ser al menos 1."}

    noches = (salida - entrada).days

    try:
        pool = await create_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Bloquea la fila del tipo de habitacion y revalida
                # disponibilidad justo antes de reservar, por si cambio desde
                # que se consulto (otra llamada concurrente, por ejemplo).
                habitacion = await conn.fetchrow(
                    "SELECT id, tipo_habitacion, capacidad, precio_noche, disponible "
                    "FROM habitaciones WHERE tipo_habitacion ILIKE $1 FOR UPDATE;",
                    tipo_habitacion,
                )
                if habitacion is None:
                    return {
                        "success": False,
                        "message": f"No manejamos un tipo de habitación llamado {tipo_habitacion}.",
                    }
                if num_huespedes > habitacion["capacidad"]:
                    return {
                        "success": False,
                        "message": (
                            f"{formatear_habitaciones(1, habitacion['tipo_habitacion'])} tiene "
                            f"capacidad para {habitacion['capacidad']}; ese número de huéspedes no alcanza."
                        ),
                    }

                reservadas = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM reservas
                    WHERE tipo_habitacion_id = $1
                      AND estado != 'cancelada'
                      AND fecha_entrada < $2
                      AND fecha_salida > $3;
                    """,
                    habitacion["id"],
                    salida,
                    entrada,
                )
                if reservadas >= habitacion["disponible"]:
                    # ToolError (no una excepcion generica): su mensaje llega
                    # tal cual al LLM. Cualquier otra excepcion sin capturar
                    # se convierte en "An internal error occurred" en ingles,
                    # lo cual el agente terminaria diciendo en una llamada en
                    # espanol.
                    raise ToolError(
                        f"Justo se agotaron las {formatear_habitaciones(2, habitacion['tipo_habitacion'])} "
                        f"para esas fechas."
                    )

                reserva_id = await conn.fetchval(
                    """
                    INSERT INTO reservas
                        (cliente_nombre, cliente_telefono, fecha_entrada, fecha_salida,
                         num_huespedes, tipo_habitacion_id, precio_noche)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id;
                    """,
                    cliente_nombre,
                    cliente_telefono,
                    entrada,
                    salida,
                    num_huespedes,
                    habitacion["id"],
                    habitacion["precio_noche"],
                )
    except (asyncpg.PostgresError, OSError):
        return {"success": False, "message": MENSAJE_FALLBACK_DB}

    total = habitacion["precio_noche"] * noches

    return {
        "success": True,
        "message": (
            f"Reserva confirmada a nombre de {cliente_nombre}: "
            f"{formatear_habitaciones(1, habitacion['tipo_habitacion'])} desde {fecha_entrada} "
            f"hasta {fecha_salida} ({formatear_cantidad(noches, 'noche')}), "
            f"total {formatear_precio(total)}."
        ),
        "reserva_id": reserva_id,
        "total": total,
        "noches": noches,
    }
