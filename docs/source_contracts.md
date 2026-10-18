# Source Data Contracts

This document formalizes the operational agreements between the upstream transactional store (OLTP) and the data warehouse ingestion layer (`raw.*`). Any schema change, enum alteration, or watermark behavioral shift in the source requires a version bump of this contract.

---

## 1. Entity: `customers`

### Section A: Identity & Key Semantics
* **Entity Name:** `customers`
* **Natural / Business Key:** `customer_id` (UUID or BIGINT from source OLTP).
* **Surrogate / Stable Ingestion Key:** `customer_id` (tracked historically via `updated_at` downstream).
* **Granularity:** Exactly one row per registered customer account.

### Section B: Schema & Type Definitions
| Column Name | Source Operational Type | Target Raw Type | Nullable | Constraints / Allowed Values |
| :--- | :--- | :--- | :--- | :--- |
| `customer_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Unique, positive integer |
| `email` | `VARCHAR(255)` | `VARCHAR(255)` | `NOT NULL` | Valid email pattern, lowercase |
| `first_name` | `VARCHAR(100)` | `VARCHAR(100)` | `NOT NULL` | Stripped whitespace |
| `last_name` | `VARCHAR(100)` | `VARCHAR(100)` | `NOT NULL` | Stripped whitespace |
| `phone_number` | `VARCHAR(32)` | `VARCHAR(32)` | `NULL` | E.164 format if present |
| `city` | `VARCHAR(100)` | `VARCHAR(100)` | `NULL` | Standard string |
| `country_code` | `CHAR(2)` | `CHAR(2)` | `NOT NULL` | ISO 3166-1 alpha-2 (e.g., 'US', 'EE') |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | UTC |
| `updated_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | UTC; `>= created_at` |

### Section C: Temporal & Mutation Contract
* **Watermark / Ingestion Column:** `updated_at`
* **Mutation Behavior:** **Mutable in-place**. Profiles, addresses, and emails update over time. Downstream models (`dim_customers`) evaluate this table for SCD Type 2 tracking.
* **Late-Arriving Data Tolerance:** 72-hour sliding window on incremental reads (`WHERE updated_at >= :last_checkpoint - INTERVAL '72 hours'`) to capture delayed transactions or offline profile syncs.

### Section D: Failure & Rejection Policy
* **Missing or malformed `customer_id`:** Quarantine row to `audit.rejected_records` (Reason: `ERR_NULL_PK`).
* **Malformed `email` or invalid `country_code`:** Ingest into `raw.customers`, but flag with a validation warning in `audit.quality_results`; downstream sanitization applies default fallback (`UNKNOWN`).

---

## 2. Entity: `products`

### Section A: Identity & Key Semantics
* **Entity Name:** `products`
* **Natural / Business Key:** `product_id`
* **Surrogate / Stable Ingestion Key:** `product_id`
* **Granularity:** Exactly one row per catalog SKU / sellable item.

### Section B: Schema & Type Definitions
| Column Name | Source Operational Type | Target Raw Type | Nullable | Constraints / Allowed Values |
| :--- | :--- | :--- | :--- | :--- |
| `product_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Unique, positive integer |
| `sku` | `VARCHAR(64)` | `VARCHAR(64)` | `NOT NULL` | Unique string identifier |
| `product_name`| `VARCHAR(255)` | `VARCHAR(255)` | `NOT NULL` | Non-empty string |
| `category` | `VARCHAR(100)` | `VARCHAR(100)` | `NOT NULL` | Standard category taxonomy |
| `price_cents` | `INTEGER` | `INTEGER` | `NOT NULL` | Values `>= 0` (integer currency) |
| `is_active` | `BOOLEAN` | `BOOLEAN` | `NOT NULL` | Default `true` |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | UTC |
| `updated_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | UTC; `>= created_at` |

### Section C: Temporal & Mutation Contract
* **Watermark / Ingestion Column:** `updated_at`
* **Mutation Behavior:** **Mutable in-place**. Catalog pricing, category realignment, and active status can change.
* **Late-Arriving Data Tolerance:** 24-hour lookback buffer on incremental watermark extraction.

### Section D: Failure & Rejection Policy
* **Missing `product_id` or `sku`:** Quarantine to `audit.rejected_records` (Reason: `ERR_NULL_PK_OR_SKU`).
* **`price_cents < 0`:** Quarantine to `audit.rejected_records` (Reason: `ERR_NEGATIVE_PRICE`). Critical business constraint breach.

---

## 3. Entity: `orders`

### Section A: Identity & Key Semantics
* **Entity Name:** `orders`
* **Natural / Business Key:** `order_id`
* **Surrogate / Stable Ingestion Key:** `order_id`
* **Granularity:** Exactly one row per checkout session initiated by a customer.

### Section B: Schema & Type Definitions
| Column Name | Source Operational Type | Target Raw Type | Nullable | Constraints / Allowed Values |
| :--- | :--- | :--- | :--- | :--- |
| `order_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Unique primary key |
| `customer_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Must reference an existing customer |
| `status` | `VARCHAR(32)` | `VARCHAR(32)` | `NOT NULL` | Enum: `['PENDING', 'PAID', 'SHIPPED', 'CANCELLED', 'REFUNDED']` |
| `order_total_cents` | `INTEGER` | `INTEGER` | `NOT NULL` | `>= 0` |
| `currency` | `CHAR(3)` | `CHAR(3)` | `NOT NULL` | ISO 4217 code (e.g., `'USD'`, `'EUR'`) |
| `order_timestamp` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | Order creation moment (UTC) |
| `updated_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | State transition timestamp (UTC) |

### Section C: Temporal & Mutation Contract
* **Watermark / Ingestion Column:** `updated_at`
* **Mutation Behavior:** **State Machine Transitions**. The row mutates as `status` changes from `PENDING` to `PAID`/`CANCELLED`.
* **Late-Arriving Data Tolerance:** 7-day lookback window on watermark runs to capture delayed fulfillment or manual order reconciliation.

### Section D: Failure & Rejection Policy
* **Missing `order_id`:** Quarantine to `audit.rejected_records` (Reason: `ERR_NULL_PK`).
* **Undefined `status` enum:** Quarantine to `audit.rejected_records` (Reason: `ERR_INVALID_STATUS_ENUM`). Downstream fact tables must not receive unmapped business states.
* **Orphan `customer_id`:** Ingest into `raw.orders`, but downstream staging logs an integrity warning and routes through an `UNKNOWN_CUSTOMER` dimension surrogate key ($key = -1$) to prevent data loss.

---

## 4. Entity: `order_items`

### Section A: Identity & Key Semantics
* **Entity Name:** `order_items`
* **Natural / Business Key:** `order_item_id`
* **Surrogate / Stable Ingestion Key:** Composite: `(order_id, order_item_id)`
* **Granularity:** Exactly one row per distinct product line item within an order.

### Section B: Schema & Type Definitions
| Column Name | Source Operational Type | Target Raw Type | Nullable | Constraints / Allowed Values |
| :--- | :--- | :--- | :--- | :--- |
| `order_item_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Unique line item key |
| `order_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Foreign key referencing `orders` |
| `product_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Foreign key referencing `products` |
| `quantity` | `INTEGER` | `INTEGER` | `NOT NULL` | Integer `> 0` |
| `unit_price_cents`| `INTEGER` | `INTEGER` | `NOT NULL` | Snapshot price at checkout; `>= 0` |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | UTC |

### Section C: Temporal & Mutation Contract
* **Watermark / Ingestion Column:** `created_at` (or parent order's `updated_at` if joint-extracted).
* **Mutation Behavior:** **Strictly Append-Only**. Once an order is placed, line items are immutable. Items removed or cancelled are handled via status changes or refunds, not row deletions.
* **Late-Arriving Data Tolerance:** Extracted in lockstep with the corresponding `order_id`. Lookback window aligns with `orders` (7 days).

### Section D: Failure & Rejection Policy
* **`quantity <= 0` or negative `unit_price_cents`:** Quarantine to `audit.rejected_records` (Reason: `ERR_INVALID_NUMERIC_METRICS`).
* **Missing `order_id` or `product_id`:** Quarantine to `audit.rejected_records` (Reason: `ERR_ORPHAN_LINE_ITEM`).

---

## 5. Entity: `payments`

### Section A: Identity & Key Semantics
* **Entity Name:** `payments`
* **Natural / Business Key:** `payment_id`
* **Surrogate / Stable Ingestion Key:** `payment_id`
* **Granularity:** Exactly one row per payment transaction attempt.

### Section B: Schema & Type Definitions
| Column Name | Source Operational Type | Target Raw Type | Nullable | Constraints / Allowed Values |
| :--- | :--- | :--- | :--- | :--- |
| `payment_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Unique identifier from payment service |
| `order_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Associated order identifier |
| `payment_method` | `VARCHAR(32)` | `VARCHAR(32)` | `NOT NULL` | Enum: `['CREDIT_CARD', 'BANK_TRANSFER', 'PAYPAL', 'CRYPTO']` |
| `payment_status` | `VARCHAR(32)` | `VARCHAR(32)` | `NOT NULL` | Enum: `['INITIATED', 'SUCCESS', 'FAILED', 'SETTLED']` |
| `amount_cents` | `INTEGER` | `INTEGER` | `NOT NULL` | Amount processed; `> 0` |
| `transaction_ref`| `VARCHAR(128)` | `VARCHAR(128)` | `NULL` | Upstream processor transaction ID |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | Timestamp of attempt (UTC) |
| `updated_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | Timestamp of last state change (UTC) |

### Section C: Temporal & Mutation Contract
* **Watermark / Ingestion Column:** `updated_at`
* **Mutation Behavior:** **State Machine Transitions** (`INITIATED` $\to$ `SUCCESS`/`FAILED`).
* **Late-Arriving Data Tolerance:** 72-hour window. Payment settlement often clears asynchronously 24–48 hours after cart creation.

### Section D: Failure & Rejection Policy
* **Missing `payment_id` or `order_id`:** Quarantine to `audit.rejected_records` (Reason: `ERR_NULL_RELATIONAL_KEYS`).
* **`amount_cents <= 0`:** Quarantine to `audit.rejected_records` (Reason: `ERR_NON_POSITIVE_PAYMENT`).

---

## 6. Entity: `refunds`

### Section A: Identity & Key Semantics
* **Entity Name:** `refunds`
* **Natural / Business Key:** `refund_id`
* **Surrogate / Stable Ingestion Key:** `refund_id`
* **Granularity:** Exactly one row per refund event/credit issuance against an order.

### Section B: Schema & Type Definitions
| Column Name | Source Operational Type | Target Raw Type | Nullable | Constraints / Allowed Values |
| :--- | :--- | :--- | :--- | :--- |
| `refund_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Unique primary key |
| `order_id` | `BIGINT` | `BIGINT` | `NOT NULL` | Target order reference |
| `payment_id` | `BIGINT` | `BIGINT` | `NULL` | Specific transaction reference if direct reversal |
| `refund_amount_cents`| `INTEGER` | `INTEGER` | `NOT NULL` | Amount refunded; `> 0` |
| `refund_reason` | `VARCHAR(255)` | `VARCHAR(255)` | `NULL` | Text explanation or categorical code |
| `refund_status` | `VARCHAR(32)` | `VARCHAR(32)` | `NOT NULL` | Enum: `['PENDING', 'COMPLETED', 'REJECTED']` |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | Refund request initiation time (UTC) |
| `updated_at` | `TIMESTAMPTZ` | `TIMESTAMPTZ` | `NOT NULL` | Final settlement timestamp (UTC) |

### Section C: Temporal & Mutation Contract
* **Watermark / Ingestion Column:** `updated_at`
* **Mutation Behavior:** **Append-driven with status updates**.
* **Late-Arriving Data Tolerance:** High latency. Refunds routinely trigger 14 to 30 days after the initial order. Extraction handles this by relying on `refunds.updated_at` rather than order dates.

### Section D: Failure & Rejection Policy
* **`refund_amount_cents <= 0`:** Quarantine to `audit.rejected_records` (Reason: `ERR_INVALID_REFUND_AMOUNT`).
* **`refund_amount_cents > order_total_cents` (Cross-table check):** Handled downstream in dimensional validation; logs an integrity error to `audit.quality_results`.