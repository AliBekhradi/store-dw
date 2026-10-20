-- Schema definition for transactional source database (Store OLTP)
-- Source-side implementation of source_contracts.md (PostgreSQL).
-- Fresh-database initialization, NOT an upgrade migration.
-- Pipeline obligations are documented at the end; this DDL cannot implement
-- extraction scheduling, warehouse transformations, or ingestion rejection routing.
-- Interpretation: "valid email pattern" means the conventional ASCII pattern
-- below, not mailbox existence or complete RFC 5322 support. The contract does
-- not specify a pattern. "Alphanumeric SKU" means ASCII letters and digits.
-- Currency membership uses a maintained ISO 4217 reference table.
-- Application roles must not own these objects, disable triggers, change the
-- currency registry, truncate tables, or use replication-role bypasses.
-- Run as the database owner (or a superuser): initialization also sets the
-- current database's default time zone to UTC for subsequent connections.

BEGIN;
SET LOCAL TIME ZONE 'UTC';

-- SET LOCAL alone affects only this migration transaction. Persist the default
-- for future connections as well. Existing connections must reconnect or SET
-- TIME ZONE 'UTC'; role/client settings must not override this database default.
-- TIMESTAMPTZ represents absolute instants regardless of display time zone.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET TimeZone TO %L', current_database(), 'UTC');
END;
$$;

CREATE SCHEMA IF NOT EXISTS store_source;

-- ============================================================================
-- HELPER FUNCTIONS & TRIGGERS (Automates updated_at for watermarks)
-- ============================================================================
CREATE OR REPLACE FUNCTION store_source.fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    -- Use wall-clock time rather than transaction-start time; do not move
    -- an existing watermark backwards during a long-running transaction.
    NEW.updated_at = GREATEST(clock_timestamp(), OLD.updated_at, NEW.created_at);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 1. CUSTOMERS
-- ============================================================================
CREATE TABLE store_source.customers (
    customer_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    first_name          VARCHAR(100) NOT NULL,
    last_name           VARCHAR(100) NOT NULL,
    email               VARCHAR(255) NOT NULL,
    phone               VARCHAR(50),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_customers_id_positive CHECK (customer_id > 0),
    CONSTRAINT chk_customers_first_name CHECK (length(first_name) > 0),
    CONSTRAINT chk_customers_last_name CHECK (length(last_name) > 0),
    CONSTRAINT chk_customers_email_lower CHECK (email = lower(email)),
    CONSTRAINT chk_customers_email_pattern CHECK (
        email ~ '^[A-Za-z0-9!#$%&''*+/=?^_`{|}~-]+([.][A-Za-z0-9!#$%&''*+/=?^_`{|}~-]+)*@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?([.][A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
    ),
    CONSTRAINT chk_customers_timestamps CHECK (updated_at >= created_at)
);

-- Contract requires case-insensitive unique email
CREATE UNIQUE INDEX uq_customers_email_lower ON store_source.customers (LOWER(email));
CREATE INDEX idx_customers_updated_at ON store_source.customers (updated_at);

CREATE TRIGGER trg_customers_updated_at
BEFORE UPDATE ON store_source.customers
FOR EACH ROW EXECUTE FUNCTION store_source.fn_set_updated_at();

-- ============================================================================
-- 2. PRODUCTS
-- ============================================================================
CREATE TABLE store_source.products (
    product_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku                 VARCHAR(64) NOT NULL UNIQUE,
    product_name        VARCHAR(255) NOT NULL,
    category            VARCHAR(100) NOT NULL,
    price_cents         INTEGER NOT NULL CHECK (price_cents >= 0),
    stock_quantity      INTEGER NOT NULL CHECK (stock_quantity >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_products_id_positive CHECK (product_id > 0),
    CONSTRAINT chk_products_sku_format CHECK (sku ~ '^[A-Za-z0-9]+$'),
    CONSTRAINT chk_products_timestamps CHECK (updated_at >= created_at)
);

CREATE INDEX idx_products_category ON store_source.products (category);
CREATE INDEX idx_products_updated_at ON store_source.products (updated_at);

CREATE TRIGGER trg_products_updated_at
BEFORE UPDATE ON store_source.products
FOR EACH ROW EXECUTE FUNCTION store_source.fn_set_updated_at();

-- ============================================================================
-- 3. ORDERS
-- ============================================================================
CREATE TABLE store_source.orders (
    order_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id         BIGINT NOT NULL REFERENCES store_source.customers(customer_id) ON DELETE RESTRICT,
    order_status        VARCHAR(32) NOT NULL,
    subtotal_cents      INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    tax_cents           INTEGER NOT NULL DEFAULT 0 CHECK (tax_cents >= 0),
    shipping_cents      INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
    total_cents         INTEGER NOT NULL CHECK (total_cents >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_orders_status CHECK (order_status IN ('PENDING', 'PAID', 'SHIPPED', 'CANCELLED', 'REFUNDED')),
    CONSTRAINT chk_orders_total_balance CHECK (total_cents::BIGINT = subtotal_cents::BIGINT + tax_cents::BIGINT + shipping_cents::BIGINT),
    CONSTRAINT chk_orders_timestamps CHECK (updated_at >= created_at)
);

CREATE INDEX idx_orders_customer_id ON store_source.orders (customer_id);
CREATE INDEX idx_orders_order_status ON store_source.orders (order_status);
CREATE INDEX idx_orders_updated_at ON store_source.orders (updated_at);

CREATE TRIGGER trg_orders_updated_at
BEFORE UPDATE ON store_source.orders
FOR EACH ROW EXECUTE FUNCTION store_source.fn_set_updated_at();

-- ============================================================================
-- 4. ORDER ITEMS (Immutable)
-- ============================================================================
CREATE TABLE store_source.order_items (
    -- The contract scopes this ID to its order, not to the entire table.
    -- Keep automatic allocation as a convenience, but allow explicit IDs to
    -- repeat across different orders. The composite primary key is authoritative.
    order_item_id       BIGINT GENERATED BY DEFAULT AS IDENTITY NOT NULL,
    order_id            BIGINT NOT NULL REFERENCES store_source.orders(order_id) ON DELETE RESTRICT,
    product_id          BIGINT NOT NULL REFERENCES store_source.products(product_id) ON DELETE RESTRICT,
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents    INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    line_total_cents    INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT pk_order_items PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT uq_order_items_order_product UNIQUE (order_id, product_id),
    CONSTRAINT chk_order_items_line_total CHECK (line_total_cents::BIGINT = quantity::BIGINT * unit_price_cents::BIGINT)
);

CREATE INDEX idx_order_items_order_id ON store_source.order_items (order_id);
CREATE INDEX idx_order_items_product_id ON store_source.order_items (product_id);
CREATE INDEX idx_order_items_created_at ON store_source.order_items (created_at);

-- ============================================================================
-- 5. PAYMENTS
-- ============================================================================
CREATE TABLE store_source.payments (
    payment_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id            BIGINT NOT NULL REFERENCES store_source.orders(order_id) ON DELETE RESTRICT,
    payment_method      VARCHAR(50) NOT NULL,
    payment_status      VARCHAR(32) NOT NULL,
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    currency            CHAR(3) NOT NULL DEFAULT 'USD',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_payments_status CHECK (payment_status IN ('PENDING', 'SUCCESS', 'FAILED')),
    CONSTRAINT chk_payments_timestamps CHECK (updated_at >= created_at)
);

CREATE INDEX idx_payments_order_id ON store_source.payments (order_id);
CREATE INDEX idx_payments_updated_at ON store_source.payments (updated_at);

CREATE TRIGGER trg_payments_updated_at
BEFORE UPDATE ON store_source.payments
FOR EACH ROW EXECUTE FUNCTION store_source.fn_set_updated_at();

-- ============================================================================
-- 6. REFUNDS
-- ============================================================================
CREATE TABLE store_source.refunds (
    refund_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payment_id          BIGINT NOT NULL REFERENCES store_source.payments(payment_id) ON DELETE RESTRICT,
    order_id            BIGINT NOT NULL REFERENCES store_source.orders(order_id) ON DELETE RESTRICT,
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    reason              VARCHAR(255),
    refund_status       VARCHAR(32) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_refunds_status CHECK (refund_status IN ('PENDING', 'COMPLETED', 'FAILED')),
    CONSTRAINT chk_refunds_timestamps CHECK (updated_at >= created_at)
);

CREATE INDEX idx_refunds_payment_id ON store_source.refunds (payment_id);
CREATE INDEX idx_refunds_order_id ON store_source.refunds (order_id);
CREATE INDEX idx_refunds_updated_at ON store_source.refunds (updated_at);

CREATE TRIGGER trg_refunds_updated_at
BEFORE UPDATE ON store_source.refunds
FOR EACH ROW EXECUTE FUNCTION store_source.fn_set_updated_at();

-- ============================================================================
-- ISO 4217 MEMBERSHIP
-- ============================================================================
-- SIX ISO 4217 Maintenance Agency List One, published 2026-09-17:
-- https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
-- Includes ISO-assigned fund/special-purpose codes; the contract excludes none.
-- Maintain this registry as ISO assignments change. Retain formerly valid codes
-- needed by historical payments; do not cascade updates into payment records.
CREATE TABLE store_source.iso_4217_currencies (
    currency CHAR(3) PRIMARY KEY CHECK (currency ~ '^[A-Z]{3}$')
);
INSERT INTO store_source.iso_4217_currencies (currency) VALUES
('AED'), ('AFN'), ('ALL'), ('AMD'), ('AOA'), ('ARS'), ('AUD'), ('AWG'), ('AZN'),
('BAM'), ('BBD'), ('BDT'), ('BHD'), ('BIF'), ('BMD'), ('BND'), ('BOB'), ('BOV'),
('BRL'), ('BSD'), ('BTN'), ('BWP'), ('BYN'), ('BZD'), ('CAD'), ('CDF'), ('CHE'),
('CHF'), ('CHW'), ('CLF'), ('CLP'), ('CNY'), ('COP'), ('COU'), ('CRC'), ('CUP'),
('CVE'), ('CZK'), ('DJF'), ('DKK'), ('DOP'), ('DZD'), ('EGP'), ('ERN'), ('ETB'),
('EUR'), ('FJD'), ('FKP'), ('GBP'), ('GEL'), ('GHS'), ('GIP'), ('GMD'), ('GNF'),
('GTQ'), ('GYD'), ('HKD'), ('HNL'), ('HTG'), ('HUF'), ('IDR'), ('ILS'), ('INR'),
('IQD'), ('IRR'), ('ISK'), ('JMD'), ('JOD'), ('JPY'), ('KES'), ('KGS'), ('KHR'),
('KMF'), ('KPW'), ('KRW'), ('KWD'), ('KYD'), ('KZT'), ('LAK'), ('LBP'), ('LKR'),
('LRD'), ('LSL'), ('LYD'), ('MAD'), ('MDL'), ('MGA'), ('MKD'), ('MMK'), ('MNT'),
('MOP'), ('MRU'), ('MUR'), ('MVR'), ('MWK'), ('MXN'), ('MXV'), ('MYR'), ('MZN'),
('NAD'), ('NGN'), ('NIO'), ('NOK'), ('NPR'), ('NZD'), ('OMR'), ('PAB'), ('PEN'),
('PGK'), ('PHP'), ('PKR'), ('PLN'), ('PYG'), ('QAR'), ('RON'), ('RSD'), ('RUB'),
('RWF'), ('SAR'), ('SBD'), ('SCR'), ('SDG'), ('SEK'), ('SGD'), ('SHP'), ('SLE'),
('SOS'), ('SRD'), ('SSP'), ('STN'), ('SVC'), ('SYP'), ('SZL'), ('THB'), ('TJS'),
('TMT'), ('TND'), ('TOP'), ('TRY'), ('TTD'), ('TWD'), ('TZS'), ('UAH'), ('UGX'),
('USD'), ('USN'), ('UYI'), ('UYU'), ('UYW'), ('UZS'), ('VED'), ('VES'), ('VND'),
('VUV'), ('WST'), ('XAD'), ('XAF'), ('XAG'), ('XAU'), ('XBA'), ('XBB'), ('XBC'),
('XBD'), ('XCD'), ('XCG'), ('XDR'), ('XOF'), ('XPD'), ('XPF'), ('XPT'), ('XSU'),
('XTS'), ('XUA'), ('XXX'), ('YER'), ('ZAR'), ('ZMW'), ('ZWG');
ALTER TABLE store_source.payments
    ADD CONSTRAINT fk_payments_iso_currency FOREIGN KEY (currency)
    REFERENCES store_source.iso_4217_currencies(currency) ON DELETE RESTRICT;

-- ============================================================================
-- MUTATION GUARANTEES
-- ============================================================================
CREATE FUNCTION store_source.fn_reject_order_item_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'order_items is append-only: % is prohibited', TG_OP
        USING ERRCODE = '23514';
END;
$$;

-- Statement-level guards reject even bulk UPDATE/DELETE and TRUNCATE.
CREATE TRIGGER trg_order_items_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON store_source.order_items
FOR EACH STATEMENT EXECUTE FUNCTION store_source.fn_reject_order_item_mutation();

CREATE FUNCTION store_source.fn_guard_stable_id()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) -> TG_ARGV[0]) IS DISTINCT FROM
       (to_jsonb(OLD) -> TG_ARGV[0]) THEN
        RAISE EXCEPTION '% is a stable ingestion key and cannot change', TG_ARGV[0]
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_customers_stable_id BEFORE UPDATE ON store_source.customers
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('customer_id');
CREATE TRIGGER trg_products_stable_id BEFORE UPDATE ON store_source.products
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('product_id');
CREATE TRIGGER trg_orders_stable_id BEFORE UPDATE ON store_source.orders
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('order_id');
CREATE TRIGGER trg_payments_stable_id BEFORE UPDATE ON store_source.payments
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('payment_id');
CREATE TRIGGER trg_refunds_stable_id BEFORE UPDATE ON store_source.refunds
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('refund_id');

CREATE FUNCTION store_source.fn_guard_terminal_status()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    old_status TEXT := to_jsonb(OLD) ->> TG_ARGV[0];
    new_status TEXT := to_jsonb(NEW) ->> TG_ARGV[0];
BEGIN
    -- CHECK constraints validate the status set. Same-status updates are valid.
    -- No initial status default/requirement is invented: the contract has none.
    IF new_status IS DISTINCT FROM old_status AND old_status <> 'PENDING' THEN
        RAISE EXCEPTION 'Invalid % transition: % -> %', TG_ARGV[0], old_status, new_status
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_payments_status_transition BEFORE UPDATE ON store_source.payments
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_terminal_status('payment_status');
CREATE TRIGGER trg_refunds_status_transition BEFORE UPDATE ON store_source.refunds
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_terminal_status('refund_status');

-- ============================================================================
-- CONTRACT BOUNDARIES: REQUIRED DOWNSTREAM IMPLEMENTATION
-- ============================================================================
-- These requirements are NOT replaced by source CHECK/FK errors:
-- * customers: 72-hour lookback; dim_customers SCD2; ERR_NULL_PK rejection;
--   duplicate customer_id within an extraction batch fails validation.
-- * products: 72-hour lookback; ERR_NULL_PK_OR_SKU / ERR_NEGATIVE_VALUE routing.
-- * orders: 7-day lookback; UPSERT on order_id; ERR_INVALID_STATUS_ENUM /
--   ERR_INVALID_TOTAL routing; unmatched staging customers map to warehouse -1.
-- * order_items: created_at watermark; inherit parent orders' extraction window;
--   ERR_ORPHAN_LINE_ITEM in audit.quality_results; ERR_INVALID_LINE_ITEM rejection.
-- * payments: 24-48-hour lookback; ERR_NULL_RELATIONAL_KEYS /
--   ERR_NON_POSITIVE_PAYMENT / ERR_INVALID_STATUS_ENUM routing.
-- * refunds: 30-day lookback; ERR_NON_POSITIVE_REFUND rejection; aggregate ALL
--   refund statuses by order_id and raise ERR_INVALID_REFUND_AMOUNT when SUM
--   exceeds orders.total_cents. This is an ALERT, not a source write rejection.
-- All rejection routing targets audit.rejected_records where specified.
-- Audit table schemas and extraction/checkpoint machinery are not supplied by
-- the contract. Implement them in the ingestion/warehouse deployment.
-- The database defaults new connections to TimeZone UTC. Configure extraction
-- connections to UTC as well: role/client settings can override that default.
-- Sequence IDs are unique but not gapless (PostgreSQL sequences consume values
-- on rolled-back inserts). The contract must not interpret "sequential" as gapless.
-- "Canonical category" has no supplied vocabulary; no vocabulary is invented.
-- E.164 phone formatting is preferred, not mandatory, so it is not rejected here.
-- Contract ambiguity: customer_id=0 violates Section B's strictly-positive rule
-- but Section D names only null/negative IDs. The source rejects zero; downstream
-- rejection-code handling for zero needs clarification in the contract.

COMMIT;