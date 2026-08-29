-- Catalogo de tipos de habitacion. "disponible" es el inventario total de
-- ese tipo (igual que products.stock en la version anterior), no un
-- booleano por fecha: la disponibilidad real para un rango de fechas dado
-- se calcula restando las reservas que se solapan con ese rango (ver
-- tools/habitaciones.py:consultar_disponibilidad). Simplificacion
-- deliberada de demo: una reserva no "libera" el cupo automaticamente el
-- dia del checkout salvo que su fecha_salida ya haya pasado, que es
-- justamente la condicion que usa el calculo de solapamiento.
CREATE TABLE habitaciones (
    id SERIAL PRIMARY KEY,
    tipo_habitacion VARCHAR(30) NOT NULL,
    -- amenidades/detalles que el agente puede mencionar cuando el cliente
    -- pregunta por una habitacion en concreto, no solo precio y capacidad
    descripcion TEXT NOT NULL DEFAULT '',
    capacidad INTEGER NOT NULL,
    precio_noche INTEGER NOT NULL,
    disponible INTEGER NOT NULL DEFAULT 0
);

INSERT INTO habitaciones (tipo_habitacion, descripcion, capacidad, precio_noche, disponible)
VALUES
(
    'Sencilla',
    'Cama doble, wifi de alta velocidad y TV por cable. Ideal para viajeros solos.',
    2, 120000, 6
),
(
    'Doble',
    'Dos camas, baño privado y escritorio de trabajo. Pensada para dos personas o viajes de negocio.',
    3, 160000, 5
),
(
    'Triple',
    'Tres camas individuales, cómoda para grupos pequeños de amigos o colegas.',
    4, 200000, 3
),
(
    'Familiar',
    'Dos camas dobles en un espacio amplio, con nevera pequeña. Pensada para familias grandes.',
    6, 260000, 2
),
(
    'Suite Junior',
    'Sala de estar separada, minibar y vista a la ciudad.',
    3, 300000, 3
),
(
    'Suite Presidencial',
    'La más exclusiva: jacuzzi privado, balcón panorámico y servicio de mayordomo.',
    4, 480000, 1
);

CREATE TABLE reservas (
    id SERIAL PRIMARY KEY,
    cliente_nombre VARCHAR(100) NOT NULL,
    cliente_telefono VARCHAR(20) NOT NULL,
    fecha_entrada DATE NOT NULL,
    fecha_salida DATE NOT NULL,
    num_huespedes INTEGER NOT NULL,
    tipo_habitacion_id INTEGER NOT NULL REFERENCES habitaciones(id),
    -- precio de esa noche al momento de reservar, para que un cambio de
    -- tarifa futuro no altere reservas ya confirmadas (mismo motivo que
    -- order_items.unit_price en la version anterior)
    precio_noche INTEGER NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'confirmada',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (fecha_salida > fecha_entrada)
);

-- para calcular rapido cuantas reservas se solapan con un rango de fechas
-- por tipo de habitacion (ver consultar_disponibilidad / crear_reserva)
CREATE INDEX idx_reservas_tipo_fechas ON reservas (tipo_habitacion_id, fecha_entrada, fecha_salida)
    WHERE estado != 'cancelada';

-- Reservas de ejemplo: agotan el cupo de un par de tipos en fechas
-- concretas, para poder probar el escenario de "sin disponibilidad" de
-- consultar_disponibilidad sin tener que crear reservas a mano primero.
INSERT INTO reservas (cliente_nombre, cliente_telefono, fecha_entrada, fecha_salida, num_huespedes, tipo_habitacion_id, precio_noche)
VALUES
(
    'Carlos Ramírez', '3011234567', '2026-09-05', '2026-09-08', 2,
    (SELECT id FROM habitaciones WHERE tipo_habitacion = 'Doble'), 160000
),
(
    -- Suite Presidencial solo tiene 1 unidad (ver disponible arriba): esta
    -- reserva la agota por completo para el 10-15 de septiembre de 2026.
    'Ana María Torres', '3022345678', '2026-09-10', '2026-09-15', 2,
    (SELECT id FROM habitaciones WHERE tipo_habitacion = 'Suite Presidencial'), 480000
),
(
    -- Familiar tiene 2 unidades; estas dos reservas se solapan entre el
    -- 12 y el 14 de septiembre de 2026, agotando el cupo justo esos días.
    'Jorge Peláez', '3033456789', '2026-09-12', '2026-09-16', 5,
    (SELECT id FROM habitaciones WHERE tipo_habitacion = 'Familiar'), 260000
),
(
    'Lucía Gómez', '3044567890', '2026-09-12', '2026-09-14', 4,
    (SELECT id FROM habitaciones WHERE tipo_habitacion = 'Familiar'), 260000
),
(
    'Andrés Salazar', '3055678901', '2026-09-20', '2026-09-22', 1,
    (SELECT id FROM habitaciones WHERE tipo_habitacion = 'Sencilla'), 120000
);

-- Informacion general del hotel (horarios, servicios) que no depende de
-- fechas ni de disponibilidad: se inyecta una sola vez en el prompt, igual
-- que el catalogo de habitaciones (ver tools/habitaciones.py:
-- build_servicios_prompt_block).
CREATE TABLE servicios_hotel (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL,
    detalle TEXT NOT NULL
);

INSERT INTO servicios_hotel (nombre, detalle)
VALUES
('Check-in / Check-out', 'Check-in desde las 3:00 p.m., check-out hasta las 12:00 m.'),
('Desayuno', 'Desayuno buffet incluido en la tarifa, de 6:30 a.m. a 10:00 a.m.'),
('Wifi', 'Wifi de alta velocidad gratuito en todas las áreas del hotel.'),
('Parqueadero', 'Parqueadero privado y vigilado sin costo adicional para huéspedes.'),
('Piscina', 'Piscina exterior climatizada, abierta de 7:00 a.m. a 9:00 p.m.'),
('Mascotas', 'Se permiten mascotas pequeñas con cargo adicional; avisar al reservar.');
