-- Habilita búsqueda por similitud de texto (tolera errores de tipeo / voz mal transcrita)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    category VARCHAR(30) NOT NULL DEFAULT 'otro',
    -- palabras con las que un cliente podría referirse a este producto
    -- sin usar su nombre exacto
    keywords TEXT[] NOT NULL DEFAULT '{}'
);

-- índices trigram para que la búsqueda difusa sea rápida
CREATE INDEX idx_products_name_trgm ON products USING gin (name gin_trgm_ops);
CREATE INDEX idx_products_description_trgm ON products USING gin (description gin_trgm_ops);

INSERT INTO products (name, description, price, stock, category, keywords)
VALUES
(
    'Combo Personal',
    '2 piezas de pollo, papas fritas y gaseosa pequeña.',
    18000,
    20,
    'combo',
    ARRAY['combo pequeño', 'combo individual', 'combo 1 persona', 'personal']
),
(
    'Combo Familiar',
    '8 piezas de pollo, papas familiares y 4 gaseosas.',
    45000,
    8,
    'combo',
    ARRAY['combo grande', 'combo para varios', 'combo familia', 'familiar']
),
(
    'Pollo Asado',
    'Pollo asado entero acompañado de papas.',
    30000,
    5,
    'plato',
    ARRAY['pollo entero', 'pollo al horno', 'asado']
),
(
    'Coca-Cola 400ml',
    'Gaseosa Coca-Cola de 400 ml.',
    4000,
    30,
    'bebida',
    ARRAY['gaseosa', 'soda', 'refresco', 'coca', 'bebida']
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0)
);