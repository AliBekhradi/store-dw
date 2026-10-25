-- Schema definition for transactional source database (Store OLTP)
-- Source-side implementation of source_contracts.md (PostgreSQL).
-- Fresh-database initialization, NOT an upgrade migration.
-- Pipeline obligations are documented at the end; this DDL cannot implement
-- extraction scheduling, warehouse transformations, or ingestion rejection routing.
-- Interpretation: "valid email pattern" means the conventional ASCII pattern
-- below, not mailbox existence or complete RFC 5322 support. The contract does
-- not specify a pattern. "Alphanumeric SKU" means ASCII letters and digits.
-- All monetary amounts are integer Iranian rial (IRR).
-- Application roles must not own these objects, disable triggers,
-- truncate tables, or use replication-role bypasses.
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
    -- Advance the watermark on every update, even if the source clock
    -- repeats a timestamp or moves backward.

    NEW.updated_at = GREATEST(
        clock_timestamp(),
        OLD.updated_at + INTERVAL '1 microsecond',
        NEW.created_at
    );
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
    price_rial          BIGINT NOT NULL CHECK (price_rial >= 0),
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
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_orders_id_positive CHECK (order_id > 0),

    CONSTRAINT chk_orders_status
        CHECK (order_status IN ('pending', 'completed', 'cancelled')),

    CONSTRAINT chk_orders_completion
        CHECK (
            (
                order_status = 'completed'
                AND completed_at IS NOT NULL
                AND completed_at >= created_at
            )
            OR
            (
                order_status IN ('pending', 'cancelled')
                AND completed_at IS NULL
            )
        ),

    CONSTRAINT chk_orders_timestamps
        CHECK (
            updated_at >= created_at
            AND (completed_at IS NULL OR updated_at >= completed_at)
        )

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
    -- Globally unique line identifier, also used by external refunds.
    order_item_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id            BIGINT NOT NULL REFERENCES store_source.orders(order_id) ON DELETE RESTRICT,
    product_id          BIGINT NOT NULL REFERENCES store_source.products(product_id) ON DELETE RESTRICT,
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_rial     BIGINT NOT NULL CHECK (unit_price_rial >= 0),
    line_discount_rial BIGINT NOT NULL DEFAULT 0,
    line_sales_rial     BIGINT GENERATED ALWAYS AS (
        quantity::BIGINT * unit_price_rial - line_discount_rial
    ) STORED,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT uq_order_items_order_product
        UNIQUE (order_id, product_id),

    CONSTRAINT chk_order_items_id_positive
        CHECK (order_item_id > 0),

    CONSTRAINT chk_order_items_discount
        CHECK (
            line_discount_rial >= 0
            AND line_discount_rial <= quantity::BIGINT * unit_price_rial
        )
);

CREATE INDEX idx_order_items_order_id ON store_source.order_items (order_id);
CREATE INDEX idx_order_items_product_id ON store_source.order_items (product_id);
CREATE INDEX idx_order_items_created_at ON store_source.order_items (created_at);

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

CREATE FUNCTION store_source.fn_guard_order_lifecycle()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.order_status <> 'pending' THEN
            RAISE EXCEPTION 'New orders must begin pending'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END IF;

    IF OLD.order_status <> 'pending'
       AND (
           NEW.order_status IS DISTINCT FROM OLD.order_status
           OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
       )
    THEN
        RAISE EXCEPTION 'Completed and cancelled orders are terminal'
            USING ERRCODE = '23514';
    END IF;

    IF OLD.order_status = 'pending'
       AND NEW.order_status = 'completed'
    THEN
        IF NOT EXISTS (
            SELECT 1
            FROM store_source.order_items
            WHERE order_id = NEW.order_id
        ) THEN
            RAISE EXCEPTION 'Completed orders require at least one line'
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM store_source.order_items
            WHERE order_id = NEW.order_id
              AND created_at > NEW.completed_at
        ) THEN
            RAISE EXCEPTION 'Order completion cannot precede its lines'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_orders_lifecycle
BEFORE INSERT OR UPDATE ON store_source.orders
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_order_lifecycle();

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

CREATE FUNCTION store_source.fn_guard_order_item_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    parent_status TEXT;
    parent_created_at TIMESTAMPTZ;
BEGIN
    SELECT order_status, created_at
      INTO parent_status, parent_created_at
      FROM store_source.orders
     WHERE order_id = NEW.order_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Order % does not exist', NEW.order_id
            USING ERRCODE = '23503';
    END IF;

    IF parent_status <> 'pending' THEN
        RAISE EXCEPTION 'Lines can only be added to pending orders'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at < parent_created_at THEN
        RAISE EXCEPTION 'A line cannot predate its order'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_order_items_insert
BEFORE INSERT ON store_source.order_items
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_order_item_insert();

CREATE TRIGGER trg_customers_stable_id BEFORE UPDATE ON store_source.customers
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('customer_id');

CREATE TRIGGER trg_products_stable_id BEFORE UPDATE ON store_source.products
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('product_id');

CREATE TRIGGER trg_orders_stable_id BEFORE UPDATE ON store_source.orders
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('order_id');

CREATE TRIGGER trg_products_stable_sku
BEFORE UPDATE ON store_source.products
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('sku');

CREATE TRIGGER trg_orders_stable_customer
BEFORE UPDATE ON store_source.orders
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('customer_id');

CREATE TRIGGER trg_customers_stable_created_at
BEFORE UPDATE ON store_source.customers
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('created_at');

CREATE TRIGGER trg_products_stable_created_at
BEFORE UPDATE ON store_source.products
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('created_at');

CREATE TRIGGER trg_orders_stable_created_at
BEFORE UPDATE ON store_source.orders
FOR EACH ROW EXECUTE FUNCTION store_source.fn_guard_stable_id('created_at');

CREATE FUNCTION store_source.fn_reject_source_deletion()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Source deletion is outside version 1: %', TG_OP
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER trg_customers_no_delete
BEFORE DELETE OR TRUNCATE ON store_source.customers
FOR EACH STATEMENT
EXECUTE FUNCTION store_source.fn_reject_source_deletion();

CREATE TRIGGER trg_products_no_delete
BEFORE DELETE OR TRUNCATE ON store_source.products
FOR EACH STATEMENT
EXECUTE FUNCTION store_source.fn_reject_source_deletion();

CREATE TRIGGER trg_orders_no_delete
BEFORE DELETE OR TRUNCATE ON store_source.orders
FOR EACH STATEMENT
EXECUTE FUNCTION store_source.fn_reject_source_deletion();

-- ============================================================================
-- CONTRACT BOUNDARIES: REQUIRED DOWNSTREAM IMPLEMENTATION
-- ============================================================================
-- This database exposes only the four PostgreSQL store entities.
-- Payments are extracted through HTTP; refunds arrive as CSV files.
--
-- Money is integer Iranian rial. No currency conversion, tax, or shipping.
-- Order totals are derived from order-item line_sales_rial.
-- Sales use completed_at, converted to Asia/Tehran for reporting dates.
--
-- Customer and product warehouse dimensions use Type 1 updates.
-- Incremental extraction follows the shared source contract:
-- bounded windows, configurable overlap, consistent reads, and safe replay.
-- Source timestamps do not constitute complete change-data capture.
--
-- Source CHECK/FK/trigger errors are not warehouse rejection routing.
-- Downstream ingestion must retain invalid external records and reasons.
-- Missing references remain recoverable; blocking failures must not publish.
--
-- Database time zone defaults to UTC; clients must not override it.
-- Identity sequences are unique, not gapless.
-- Runtime roles must not own tables or bypass mutation guards.
-- Development reset requires a separate privileged, explicit operation.
--
-- Initial seed updates invoke the real-time updated_at trigger.
-- Deterministic seed business data excludes these operational update
-- timestamps; use a full extraction after development reseeding.

COMMIT;