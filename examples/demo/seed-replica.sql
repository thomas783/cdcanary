-- The "replica": what a CDC pipeline left in the warehouse — with four
-- kinds of silent drift baked in. CDCanary should catch all of them.

-- orders: healthy. Counts, freshness and nulls all match. ✅
CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL,
  amount NUMERIC(10,2) NOT NULL,
  status VARCHAR(20) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
INSERT INTO orders (id, customer_id, amount, status, created_at, updated_at)
SELECT n, n, 10000 + n * 7, CASE WHEN n % 9 = 0 THEN 'cancelled' ELSE 'paid' END,
       NOW() - ((2000 - n) || ' minutes')::interval,
       NOW() - ((2000 - n) || ' minutes')::interval
FROM generate_series(1, 800) AS n;

-- products: schema-drift NULL corruption — sale_status was added at the
-- source mid-stream; rows replicated in that window carry NULLs the source
-- never had. Counts match, freshness fine. 🔴 null_rate
CREATE TABLE products (
  id INT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  price NUMERIC(10,2) NOT NULL,
  sale_status VARCHAR(20),
  updated_at TIMESTAMP NOT NULL
);
INSERT INTO products (id, name, price, sale_status, updated_at)
SELECT n, 'product_' || n, 5000 + n * 13,
       CASE WHEN n BETWEEN 40 AND 45 THEN NULL ELSE 'ON_SALE' END,
       NOW() - (n || ' minutes')::interval
FROM generate_series(1, 90) AS n;

-- users: schema drift — the source grew a `phone` column that never reached
-- the replica. Every new row silently loses it. 🔴 schema_drift
CREATE TABLE users (
  id INT PRIMARY KEY,
  email VARCHAR(100) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
INSERT INTO users (id, email, created_at, updated_at)
SELECT n, 'user' || n || '@example.com',
       NOW() - ((500 - n) || ' hours')::interval,
       NOW() - ((500 - n) || ' hours')::interval
FROM generate_series(1, 60) AS n;

-- events: the connector died three hours ago — rows stop mid-stream.
-- 🔴 freshness (and row_delta)
CREATE TABLE events (
  id INT PRIMARY KEY,
  kind VARCHAR(30) NOT NULL,
  payload VARCHAR(200),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
INSERT INTO events (id, kind, payload, created_at, updated_at)
SELECT n, CASE WHEN n % 3 = 0 THEN 'click' ELSE 'view' END, 'payload_' || n,
       NOW() - ((300 - n) || ' minutes')::interval,
       NOW() - ((300 - n) || ' minutes')::interval
FROM generate_series(1, 120) AS n;   -- source has 240

-- coupons: created at the source, replication never configured. 🔴 table_presence
-- (deliberately absent here)
