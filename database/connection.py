import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    """Devuelve un pool de conexiones reutilizable (singleton).

    Antes se creaba y se cerraba un pool nuevo en cada llamada a la
    herramienta, lo cual es lento y puede agotar las conexiones de
    Postgres si el agente recibe varias peticiones seguidas.
    """
    global _pool

    if _pool is None:
        _pool = await asyncpg.create_pool(
            user=os.getenv("DB_USER", "admin"),
            password=os.getenv("DB_PASSWORD", "password"),
            database=os.getenv("DB_NAME", "hotel_reservas"),
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
        )

    return _pool