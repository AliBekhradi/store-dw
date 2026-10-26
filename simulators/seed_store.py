import os
import sys
from datetime import UTC, datetime, timedelta
from getpass import getpass

import psycopg
from psycopg.conninfo import conninfo_to_dict

from store_dw.config import load_source_dsn


def source_connection_kwargs():
    """Use the application's DSN name, preserving the original simulator alias."""
    env = dict(os.environ)
    if "STORE_DW_SOURCE_DSN" not in env and "STORE_DATABASE_DSN" in env:
        env["STORE_DW_SOURCE_DSN"] = env["STORE_DATABASE_DSN"]
        print("STORE_DATABASE_DSN is a legacy alias; prefer STORE_DW_SOURCE_DSN.", file=sys.stderr)
    dsn = load_source_dsn(env)
    kwargs = {"conninfo": dsn}
    if not conninfo_to_dict(dsn).get("password") and not env.get("PGPASSWORD"):
        kwargs["password"] = getpass("PostgreSQL password: ")
    return kwargs

def build_customers():
    return [
        {
            "first_name": "Ali",
            "last_name": "Test",
            "email": "ali.seed@example.com",
            "phone": None,
        },
        {
            "first_name": "Sara",
            "last_name": "Test",
            "email": "sara.seed@example.com",
            "phone": None,
        },
    ]

def build_products():
    return [
        {
            "sku": "SEEDNOTE001",
            "product_name": "Notebook",
            "category": "Stationery",
            "price_rial": 1_000_000,
            "stock_quantity": 100,
        },
        {
            "sku": "SEEDPEN001",
            "product_name": "Pen",
            "category": "Stationery",
            "price_rial": 2500_000,
            "stock_quantity": 200,
        },
        {
            "sku": "SEEDBAG001",
            "product_name": "Backpack",
            "category": "Accessories",
            "price_rial": 8_000_000,
            "stock_quantity": 30,
        },
    ]

def build_orders():
    placed_at = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
    line_created_at = placed_at + timedelta(minutes=1)
    completed_at = placed_at + timedelta(minutes=5)

    return [
        {
            "scenario": "completed_discount",
            "customer_email": "ali.seed@example.com",
            "final_status": "completed",
            "created_at": placed_at,
            "completed_at": completed_at,
            "items": [
                {
                    "product_sku": "SEEDNOTE001",
                    "quantity": 2,
                    "unit_price_rial": 1_000_000,
                    "line_discount_rial": 100_000,
                    "created_at": line_created_at,
                },
                {
                    "product_sku": "SEEDPEN001",
                    "quantity": 4,
                    "unit_price_rial": 250_000,
                    "line_discount_rial": 0,
                    "created_at": line_created_at,
                },
            ],
        },
        {
            "scenario": "completed_historical_price",
            "customer_email": "sara.seed@example.com",
            "final_status": "completed",
            "created_at": placed_at,
            "completed_at": completed_at,
            "items": [
                {
                    "product_sku": "SEEDBAG001",
                    "quantity": 1,
                    "unit_price_rial": 7_500_000,
                    "line_discount_rial": 500_000,
                    "created_at": line_created_at,
                },
            ],
        },
        {
            "scenario": "pending_basket",
            "customer_email": "ali.seed@example.com",
            "final_status": "pending",
            "created_at": placed_at,
            "completed_at": None,
            "items": [
                {
                    "product_sku": "SEEDPEN001",
                    "quantity": 2,
                    "unit_price_rial": 250_000,
                    "line_discount_rial": 0,
                    "created_at": line_created_at,
                },
            ],
        },
        {
            "scenario": "cancelled_basket",
            "customer_email": "sara.seed@example.com",
            "final_status": "cancelled",
            "created_at": placed_at,
            "completed_at": None,
            "items": [
                {
                    "product_sku": "SEEDBAG001",
                    "quantity": 1,
                    "unit_price_rial": 8_000_000,
                    "line_discount_rial": 0,
                    "created_at": line_created_at,
                },
            ],
        },
    ]

def check_order_scenarios(orders):
    expected_totals = {
        "completed_discount": 2_900_000,
        "completed_historical_price": 7_000_000,
        "pending_basket": 500_000,
        "cancelled_basket": 8_000_000,
    }

    scenarios = [order["scenario"] for order in orders]

    assert len(scenarios) == len(set(scenarios)), "Duplicate scenario labels"
    assert set(scenarios) == set(expected_totals), "Unexpected scenario set"

    completed_sales_rial = 0

    for order in orders:
        total_rial = sum(
            item["quantity"] * item["unit_price_rial"]
            - item["line_discount_rial"]
            for item in order["items"]
        )

        scenario = order["scenario"]

        assert total_rial == expected_totals[scenario], (
            f"{scenario}: expected {expected_totals[scenario]}, "
            f"got {total_rial}"
        )

        if order["final_status"] == "completed":
            completed_sales_rial += total_rial

        print(
            f"{scenario}: {total_rial:,} IRR "
            f"({order['final_status']})"
        )

    assert completed_sales_rial == 9_900_000, (
        f"Expected completed sales of 9900000, got {completed_sales_rial}"
    )

    print(f"Completed-order sales: {completed_sales_rial:,} IRR")

def insert_store_records(conn, customers, products, orders):
    customer_ids = {}
    product_ids = {}
    order_ids = {}
    item_ids = {}

    with conn.cursor() as cur:
        for customer in customers:
            cur.execute(
                """
                INSERT INTO store_source.customers
                    (first_name, last_name, email, phone)
                VALUES
                    (%(first_name)s, %(last_name)s, %(email)s, %(phone)s)
                RETURNING customer_id
                """,
                customer,
            )
            customer_ids[customer["email"]] = cur.fetchone()[0]

        for product in products:
            cur.execute(
                """
                INSERT INTO store_source.products
                    (sku, product_name, category, price_rial, stock_quantity)
                VALUES
                    (%(sku)s, %(product_name)s, %(category)s,
                     %(price_rial)s, %(stock_quantity)s)
                RETURNING product_id
                """,
                product,
            )
            product_ids[product["sku"]] = cur.fetchone()[0]

        for order in orders:
            cur.execute(
                """
                INSERT INTO store_source.orders
                    (customer_id, order_status, created_at)
                VALUES (%s, 'pending', %s)
                RETURNING order_id
                """,
                (
                    customer_ids[order["customer_email"]],
                    order["created_at"],
                ),
            )
            order_id = cur.fetchone()[0]
            order_ids[order["scenario"]] = order_id

            for item in order["items"]:
                cur.execute(
                    """
                    INSERT INTO store_source.order_items
                        (order_id, product_id, quantity, unit_price_rial,
                         line_discount_rial, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING order_item_id
                    """,
                    (
                        order_id,
                        product_ids[item["product_sku"]],
                        item["quantity"],
                        item["unit_price_rial"],
                        item["line_discount_rial"],
                        item["created_at"],
                    ),
                )
                item_ids[(order["scenario"], item["product_sku"])] = (
                    cur.fetchone()[0]
                )

            if order["final_status"] != "pending":
                cur.execute(
                    """
                    UPDATE store_source.orders
                    SET order_status = %s, completed_at = %s
                    WHERE order_id = %s
                    """,
                    (
                        order["final_status"],
                        order["completed_at"],
                        order_id,
                    ),
                )

    return order_ids, item_ids

def verify_store_fixture(conn, order_ids):
    # Expected final status, number of lines, and line total in rials.
    expected = {
        "completed_discount": ("completed", 2, 2_900_000),
        "completed_historical_price": ("completed", 1, 7_000_000),
        "pending_basket": ("pending", 1, 500_000),
        "cancelled_basket": ("cancelled", 1, 8_000_000),
    }

    completed_sales_rial = 0

    with conn.cursor() as cur:
        for scenario, order_id in order_ids.items():
            cur.execute(
                """
                SELECT
                    o.order_status,
                    COUNT(i.order_item_id),
                    COALESCE(SUM(i.line_sales_rial), 0)
                FROM store_source.orders AS o
                LEFT JOIN store_source.order_items AS i
                    ON i.order_id = o.order_id
                WHERE o.order_id = %s
                GROUP BY o.order_id, o.order_status
                """,
                (order_id,),
            )
            actual = cur.fetchone()

            if actual != expected[scenario]:
                raise RuntimeError(
                    f"{scenario}: expected {expected[scenario]}, got {actual}"
                )

            if actual[0] == "completed":
                completed_sales_rial += actual[2]

        if completed_sales_rial != 9_900_000:
            raise RuntimeError(
                f"Unexpected completed sales: {completed_sales_rial}"
            )

    print(f"Database checks passed: {completed_sales_rial:,} IRR sales")

def main():
    customers = build_customers()
    products = build_products()
    orders = build_orders()

    check_order_scenarios(orders)

    with psycopg.connect(**source_connection_kwargs()) as conn:
        order_ids, item_ids = insert_store_records(
            conn, customers, products, orders
        )
        verify_store_fixture(conn, order_ids)

    print(
        f"Committed: {len(customers)} customers, "
        f"{len(products)} products, "
        f"{len(order_ids)} orders, "
        f"{len(item_ids)} order lines"
    )
    print(f"Order IDs: {order_ids}")

if __name__ == "__main__":
    main()
