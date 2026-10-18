# Business Rules — Version 1

## Purpose

Project Undead models a fictional store and its sales, payments, and refunds.

This document defines the business meaning of warehouse data and reports.
Source field definitions and delivery/update behavior belong in
`docs/source_contracts.md`.

These rules follow the initial Project Undead briefing.

## 1. Orders and sales

- An order contains one or more order lines.
- Allowed order states are `pending`, `completed`, and `cancelled`.
- Only completed orders count toward sales.
- `completed_at` determines when a sale occurs.
- The sales reporting date is the date of `completed_at` in `Asia/Tehran`.
- Cancellation happens before completion. Reversals after completion use refunds.
- Once an order is completed, its line quantities, prices, and discounts
  are immutable.
- Historical sales use the price and discount stored on the order line,
  not the product's current price.

For each completed order line:

    line_sales_rial =
        quantity × unit_price_rial − line_discount_rial

`line_discount_rial` is the discount for the entire line, not a per-unit
discount.

The completed order total is the sum of its line sales.

Shipping and tax are excluded from version 1.

## 2. Payments

- Each payment transaction belongs to an order.
- An order can have multiple payment transactions.
- Payment states include `pending`, `succeeded`, and `failed`.
- Only successful (`succeeded`) transactions count as cash collected.
- Payments are stored at transaction grain, separately from order lines.
- Payment amounts must not be duplicated across the lines of an order.

The store and payment provider are independent sources.

A completed order without a corresponding successful payment is a
reconciliation exception. Its sales still count: payment status does not
replace the completed-order rule for recognizing sales.

## 3. Refunds

- Each refund has a stable `refund_id`.
- Each refund references an `order_item_id`.
- Multiple partial refunds against the same order line are allowed.
- Refund records represent completed refunds.
- Refund corrections and reversals are outside version 1.

Refund amounts are reported on the refund's business date, not on the
original sale's date.

Repeated delivery of the same refund must not create another business
refund, including when it appears in a different file.

## 4. Money

- Warehouse monetary amounts use integer rial.
- PostgreSQL monetary columns use `BIGINT` where appropriate.
- Every source amount must have an explicitly defined unit.
- Rial versus toman must never be inferred from an amount's size.
- Source amounts are normalized to integer rial before aggregation.
- Multiple currencies are outside version 1.

The exact unit of each source field is defined in the source contracts.

## 5. Time

- Store timestamps using PostgreSQL `timestamptz`.
- Use UTC for extraction and processing windows.
- Use the named timezone `Asia/Tehran` for business reporting dates.
- Derive a business date by converting the relevant event timestamp to
  `Asia/Tehran` before taking its date.
- Ingestion time does not replace the business event time.

A sale, payment, and refund associated with the same order can occur on
different business dates.

## 6. Reporting definitions

| Measure | Definition | Reporting date |
| --- | --- | --- |
| Sales | Sum of completed order-line sales | Order completion date |
| Refunds | Sum of completed refund amounts | Refund date |
| Net sales activity | Sales occurring that day minus refunds occurring that day | Business activity date |
| Cash collected | Sum of successful payment amounts | Payment date |
| Payment discrepancy | Completed order total minus successful payment total | Order-level reconciliation; not a daily cash measure |

All reporting dates use `Asia/Tehran`. All monetary measures use rial.

### 6.1 Net sales activity

For business date D:

    net_sales_activity_rial(D) =
        sales_rial(D) − refunds_rial(D)

A refund on a later date does not rewrite the original sales date.

A date can therefore have negative net sales activity when refunds exceed
sales on that date.

### 6.2 Payment reconciliation

For each completed order:

    payment_discrepancy_rial =
        completed_order_total_rial − successful_payment_total_rial

An order with no successful payments has a successful payment total of zero.

Interpretation:

- Positive: successful payments are less than the completed order total.
- Zero: the amounts match.
- Negative: successful payments exceed the completed order total.

Aggregate order lines by order and successful payments by order separately,
then compare those totals. Do not sum payment amounts after joining payment
transactions directly to order lines.

Refunds do not enter this discrepancy formula. It is not a comprehensive
accounting or net-cash calculation.

A business discrepancy is a reportable result, not automatically a failed
pipeline.

## 7. Historical customer and product attributes

Customer and product dimensions use Type 1 updates: current descriptive
attributes overwrite previous values.

Consequently, historical sales grouped by product category may reflect the
product's current category rather than its category at the time of sale.

This does not change historical order-line prices or discounts.

Full customer/product attribute history and arbitrary historical
reconstruction are outside version 1.

## 8. Illustrative example

All dates below are business dates in `Asia/Tehran`.

On day 1, an order completes with one line:

- Quantity: 2
- Unit price: 100,000 rial
- Total line discount: 10,000 rial

Its sales amount is:

    2 × 100,000 − 10,000 = 190,000 rial

A successful payment of 190,000 rial occurs on day 1.
A completed refund of 40,000 rial occurs on day 2.

| Measure | Day 1 | Day 2 |
| --- | ---: | ---: |
| Sales | 190,000 | 0 |
| Refunds | 0 | 40,000 |
| Net sales activity | 190,000 | -40,000 |
| Cash collected | 190,000 | 0 |

The order's payment discrepancy is zero:

    190,000 − 190,000 = 0

The later refund does not rewrite day 1 sales or change this payment
discrepancy formula.

## 9. Additional version 1 decisions

The following decisions resolve details not specified by the initial briefing.
They extend the rules above.

### 9.1 Refunds are amount-based

Each refund records a monetary amount against one order line.

Refunds do not record returned quantities. Inventory returns, restocking,
and reporting of returned units are outside version 1.

A partial refund means a monetary refund smaller than the line's remaining
refundable amount. It does not imply a particular number of returned units.

### 9.2 Refund eligibility and limits

- The referenced order line must belong to a completed order.
- Each refund amount must be a strictly positive integer in rial.
- A refund's `refunded_at` must be at or after the order's `completed_at`.
- The cumulative amount of distinct refunds against an order line must
  not exceed its original line sales amount after discount.
- A line with zero sales value cannot receive a monetary refund.
- Refund eligibility does not depend on whether a matching successful
  payment has been observed.

For each order line:

    line_sales_rial =
        quantity × unit_price_rial − line_discount_rial

    remaining_refundable_rial =
        line_sales_rial − cumulative_refunded_rial

The cumulative refund calculation counts each `refund_id` once, regardless
of how many files contain it.

Individual refunds that violate these rules are rejected with a reason.
Missing order or order-line references remain recoverable through
revalidation after the referenced data arrives.

If individually valid, distinct refunds collectively exceed the line's
sales amount, publication is blocked. The pipeline must not select a
subset based on file arrival order.

A blocking failure preserves raw records and leaves the previous published
analytics and checkpoints unchanged.

### 9.3 Business event timestamps

The business event timestamp fields are:

| Entity | Field | Meaning |
| --- | --- | --- |
| Order | `completed_at` | When the order completed |
| Payment | `succeeded_at` | When the payment succeeded |
| Refund | `refunded_at` | When the refund completed |

- Completed orders require `completed_at`.
- Pending and cancelled orders have no `completed_at`.
- Successful payments require `succeeded_at`.
- Pending and failed payments have no `succeeded_at`.
- Every refund requires `refunded_at`, because refund records represent
  completed refunds.

HTTP and CSV event timestamps must use ISO 8601 with an explicit UTC
designator or numeric UTC offset. Naive timestamps are rejected.

Store event timestamps as PostgreSQL `timestamptz`.
Use `Asia/Tehran` to derive their reporting dates.

Creation, update, and ingestion timestamps serve different purposes.
They must not substitute for missing business event timestamps.

### 9.4 Payment lifecycle

Allowed payment transitions are:

    pending -> succeeded
    pending -> failed

`succeeded` and `failed` are terminal states.

A retry after a failed payment attempt creates a new payment transaction
with a new stable payment ID. It does not change the failed transaction
back to pending or succeeded.

A payment may first be observed in any allowed state. The warehouse does
not require an earlier observation of its pending state.

For each payment:

- The amount is strictly positive.
- The order reference and amount are immutable after creation.
- `succeeded_at` is set when the payment succeeds and is then immutable.
- Repeated observations of the same state are allowed.

A payment's success timestamp need not equal or follow order completion.
Payments and orders are independent source events.

Successful payments count as cash collected even if the associated order
has not completed. They do not cause that order to count as sales.

Payment reversals, chargebacks, and corrections to terminal payment
transactions are outside version 1.

### 9.5 Immutable refund identity

Refund records are immutable.

Repeated occurrences of the same `refund_id` with the same normalized
business fields represent one refund.

If the same `refund_id` appears with conflicting order-line references,
amounts, or event timestamps, publication is blocked. The pipeline must
not silently overwrite the refund or treat the conflicting occurrence
as another refund.

Refund corrections and reversals remain outside version 1.