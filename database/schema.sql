-- Habilita búsqueda por similitud de texto (tolera errores de tipeo / voz mal transcrita)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- category usa slugs en minuscula (lasagna, spaguetti, arroces, especiales,
-- almuerzo_ejecutivo); tools/products.py los mapea a los encabezados en
-- mayuscula que oye/lee el agente, en un orden fijo (no alfabetico).
--
-- dia_disponible solo aplica a la categoria "especiales" (Ajiaco los
-- miercoles, Bandeja Paisa los viernes): NULL para el resto de platos, que
-- estan disponibles todos los dias. tools/products.py calcula el dia actual
-- y se lo indica al agente en el catalogo, para que pueda decir si el
-- especial pedido aplica hoy sin necesidad de una tool aparte.
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    category VARCHAR(30) NOT NULL DEFAULT 'otro',
    dia_disponible VARCHAR(20),
    -- palabras con las que un cliente podría referirse a este plato
    -- sin usar su nombre exacto
    keywords TEXT[] NOT NULL DEFAULT '{}'
);

-- índices trigram para que la búsqueda difusa sea rápida
CREATE INDEX idx_products_name_trgm ON products USING gin (name gin_trgm_ops);
CREATE INDEX idx_products_description_trgm ON products USING gin (description gin_trgm_ops);

INSERT INTO products (name, description, price, stock, category, dia_disponible, keywords)
VALUES
-- LASAGNA
(
    'Lasagna Bolognesa',
    'Capas de pasta horneada con carne molida en salsa bolognesa y queso gratinado.',
    20000, 20, 'lasagna', NULL,
    ARRAY['lasaña bolognesa', 'lasagna de carne', 'lasaña de carne', 'boloñesa']
),
(
    'Lasagna Pollo y Champiñones',
    'Capas de pasta horneada con pollo desmechado, champiñones salteados y queso gratinado.',
    20000, 20, 'lasagna', NULL,
    ARRAY['lasaña de pollo', 'lasagna de pollo y champiñones', 'lasaña pollo champiñones']
),
(
    'Lasagna Mixta',
    'Capas de pasta horneada con carne molida, pollo desmechado y queso gratinado.',
    20000, 20, 'lasagna', NULL,
    ARRAY['lasaña mixta', 'lasagna combinada', 'lasaña de carne y pollo']
),
-- SPAGUETTI
(
    'Spaguetti Bolognesa',
    'Spaguetti con salsa bolognesa de carne molida.',
    20000, 20, 'spaguetti', NULL,
    ARRAY['espagueti bolognesa', 'espagueti de carne', 'spaguetti de carne', 'boloñesa']
),
(
    'Spaguetti Pollo, Champiñones y Tocineta',
    'Spaguetti con pollo desmechado, champiñones y tocineta en salsa cremosa.',
    20000, 20, 'spaguetti', NULL,
    ARRAY['espagueti de pollo con tocineta', 'spaguetti de pollo champiñones tocineta']
),
(
    'Spaguetti Carbonara',
    'Spaguetti en salsa carbonara con tocineta y queso parmesano.',
    20000, 20, 'spaguetti', NULL,
    ARRAY['espagueti carbonara', 'spaguetti a la carbonara']
),
(
    'Spaguetti Vegano con Verduras al Wok',
    'Spaguetti salteado al wok con vegetales frescos de temporada, sin ingredientes de origen animal.',
    20000, 15, 'spaguetti', NULL,
    ARRAY['espagueti vegano', 'spaguetti de verduras', 'espagueti al wok', 'pasta vegana']
),
-- ARROCES
(
    'Arroz con Pollo',
    'Arroz salteado con pollo desmechado y vegetales.',
    20000, 20, 'arroces', NULL,
    ARRAY['arroz de pollo']
),
(
    'Arroz Thai con Pollo y Carne',
    'Arroz al estilo thai salteado con pollo, carne de res y vegetales, toque agridulce.',
    20000, 15, 'arroces', NULL,
    ARRAY['arroz thai mixto', 'arroz tailandes con pollo y carne', 'arroz thai de pollo y res']
),
(
    'Arroz Thai Vegano',
    'Arroz al estilo thai salteado con vegetales de temporada, sin ingredientes de origen animal.',
    20000, 15, 'arroces', NULL,
    ARRAY['arroz tailandes vegano', 'arroz thai de verduras']
),
-- ESPECIALES (solo el dia que les corresponde; ver dia_disponible)
(
    'Ajiaco',
    'Sopa bogotana de pollo con tres tipos de papa, guascas, mazorca, alcaparras y crema de leche. Solo los miércoles.',
    20000, 12, 'especiales', 'miercoles',
    ARRAY['ajiaco bogotano', 'ajiaco santafereño']
),
(
    'Bandeja Paisa',
    'Frijoles, arroz, carne molida, chicharrón, chorizo, huevo, plátano maduro, aguacate y arepa. Solo los viernes.',
    20000, 12, 'especiales', 'viernes',
    ARRAY['bandeja paisa completa', 'la paisa']
),
-- ALMUERZO EJECUTIVO: todos incluyen arroz blanco, papa a la francesa,
-- ensalada y sopa; el principio (frijoles, lentejas o pasta) lo escoge el
-- cliente al pedir (ver notes en order_items / tools/orders.py).
(
    'Filete de Pollo',
    'Almuerzo ejecutivo con filete de pollo a la plancha. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 25, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo de pollo', 'almuerzo de pollo']
),
(
    'Filete de Cerdo',
    'Almuerzo ejecutivo con filete de cerdo a la plancha. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 25, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo de cerdo', 'almuerzo de cerdo']
),
(
    'Filete de Pollo a la Barbiquiu',
    'Almuerzo ejecutivo con filete de pollo bañado en salsa barbiquiu. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 20, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo de pollo bbq', 'pollo bbq ejecutivo', 'filete de pollo barbacoa', 'filete de pollo b.b.q.', 'pollo barbiquiu']
),
(
    'Filete de Cerdo Barbiquiu',
    'Almuerzo ejecutivo con filete de cerdo bañado en salsa barbiquiu. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 20, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo de cerdo bbq', 'cerdo bbq ejecutivo', 'filete de cerdo barbacoa', 'filete de cerdo b.b.q.', 'cerdo barbiquiu']
),
(
    'Chuleta de Pollo',
    'Almuerzo ejecutivo con chuleta de pollo apanada. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 25, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo chuleta de pollo', 'chuleta apanada de pollo']
),
(
    'Chuleta de Cerdo',
    'Almuerzo ejecutivo con chuleta de cerdo apanada. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 25, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo chuleta de cerdo', 'chuleta apanada de cerdo']
),
(
    'Carne Asada',
    'Almuerzo ejecutivo con carne de res asada a la parrilla. Incluye arroz blanco, papa a la francesa, ensalada y sopa; escoge tu principio (frijoles, lentejas o pasta).',
    17000, 20, 'almuerzo_ejecutivo', NULL,
    ARRAY['ejecutivo de carne asada', 'almuerzo de carne']
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed',
    -- numero del cliente cuando la llamada entra por telefonia (sip.phoneNumber);
    -- queda NULL en console/playground.
    customer_phone VARCHAR(20),
    -- a nombre de quien queda el pedido y donde se entrega; confirm_order
    -- los exige como parametros obligatorios, asi que nunca quedan vacios
    -- en un pedido confirmado.
    customer_name VARCHAR(100) NOT NULL,
    delivery_address TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    -- precio del producto al momento del pedido, para que un cambio de
    -- precio futuro no altere pedidos ya confirmados
    unit_price INTEGER NOT NULL,
    -- customizacion libre del item (hoy solo se usa para el principio del
    -- almuerzo ejecutivo: frijoles, lentejas o pasta); NULL si no aplica
    notes VARCHAR(50)
);
