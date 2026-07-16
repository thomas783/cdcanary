-- Source of truth: a healthy MySQL OLTP database.
--
-- All timestamps hang off a fixed anchor rather than the wall clock, so both
-- sides of the demo generate byte-identical values — sampled_checksum compares
-- row contents, and container boot skew must not read as drift. Freshness only
-- ever compares source vs target, so the clock never mattered anyway.
USE shop;

CREATE TABLE orders (
  id INT PRIMARY KEY AUTO_INCREMENT,
  customer_id INT NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  status VARCHAR(20) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE products (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  sale_status VARCHAR(20) NOT NULL,   -- column added mid-stream in the incident this tool is named after
  updated_at DATETIME NOT NULL
);

CREATE TABLE users (
  id INT PRIMARY KEY AUTO_INCREMENT,
  email VARCHAR(100) NOT NULL,
  phone VARCHAR(20),                  -- exists at the source, never reached the replica
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE events (
  id INT PRIMARY KEY AUTO_INCREMENT,
  kind VARCHAR(30) NOT NULL,
  payload VARCHAR(200),
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE coupons (                -- brand new table; replication not configured yet
  id INT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(20) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

-- data — ids are explicit (not AUTO_INCREMENT-assigned): an unordered
-- INSERT...SELECT hands out ids in scan order, which would misalign row
-- contents against the replica's explicit id = n.
INSERT INTO orders (id, customer_id, amount, status, created_at, updated_at)
SELECT n, n, 10000 + n * 7, IF(n % 9 = 0, 'cancelled', 'paid'),
       TIMESTAMP('2026-06-01 12:00:00') - INTERVAL (2000 - n) MINUTE, TIMESTAMP('2026-06-01 12:00:00') - INTERVAL (2000 - n) MINUTE
FROM (SELECT a.n + b.n * 10 + c.n * 100 + 1 AS n
      FROM (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
           (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b,
           (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) c) seq
WHERE n <= 800;

INSERT INTO products (id, name, price, sale_status, updated_at)
SELECT n, CONCAT('product_', n), 5000 + n * 13, 'ON_SALE', TIMESTAMP('2026-06-01 12:00:00') - INTERVAL n MINUTE
FROM (SELECT a.n + b.n * 10 + 1 AS n
      FROM (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
           (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b) seq
WHERE n <= 90;

INSERT INTO users (id, email, phone, created_at, updated_at)
SELECT n, CONCAT('user', n, '@example.com'), CONCAT('010-', 1000 + n),
       TIMESTAMP('2026-06-01 12:00:00') - INTERVAL (500 - n) HOUR, TIMESTAMP('2026-06-01 12:00:00') - INTERVAL (500 - n) HOUR
FROM (SELECT a.n + b.n * 10 + 1 AS n
      FROM (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
           (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b) seq
WHERE n <= 60;

INSERT INTO events (id, kind, payload, created_at, updated_at)
SELECT n, IF(n % 3 = 0, 'click', 'view'), CONCAT('payload_', n),
       TIMESTAMP('2026-06-01 12:00:00') - INTERVAL (300 - n) MINUTE, TIMESTAMP('2026-06-01 12:00:00') - INTERVAL (300 - n) MINUTE
FROM (SELECT a.n + b.n * 10 + c.n * 100 + 1 AS n
      FROM (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
           (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b,
           (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) c) seq
WHERE n <= 240;

INSERT INTO coupons (code, created_at, updated_at) VALUES
  ('WELCOME10', TIMESTAMP('2026-06-01 12:00:00'), TIMESTAMP('2026-06-01 12:00:00')),
  ('SUMMER26', TIMESTAMP('2026-06-01 12:00:00'), TIMESTAMP('2026-06-01 12:00:00'));
