import argparse
import random
from datetime import datetime, timedelta, timezone
import os
from getpass import getpass
import psycopg
import hashlib
import json
from seed_store import insert_store_records


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be at least 1")
    return number


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=positive_int, default=100)
    parser.add_argument("--products", type=positive_int, default=50)
    parser.add_argument("--orders", type=positive_int, default=1_000)
    parser.add_argument("--batch", default="bulk01")
    parser.add_argument(
        "--load",
        action="store_true",
        help="Insert and verify the generated batch in PostgreSQL",
    )

    args = parser.parse_args()

    if not (
        args.batch.isascii()
        and args.batch.isalnum()
        and len(args.batch) <= 20
    ):
        parser.error("--batch must contain 1–20 ASCII letters/digits")

    args.batch = args.batch.lower()
    return args

def generate_customers(count, batch):
    return [
        {
            "first_name": f"Customer{index}",
            "last_name": "Synthetic",
            "email": f"{batch}.customer{index}@example.com",
            "phone": None,
        }
        for index in range(1, count + 1)
    ]

def generate_products(count, batch, rng):
    categories = ["books", "home", "electronics", "clothing"]

    return [
        {
            "sku": f"{batch}P{index:06d}",
            "product_name": f"Synthetic product {index}",
            "category": rng.choice(categories),
            "price_rial": rng.randint(10, 2_000) * 10_000,
            "stock_quantity": rng.randint(0, 500),
        }
        for index in range(1, count + 1)
    ]

def generate_orders(count, batch, customers, products, rng):
    if not customers or not products:
        raise ValueError("Generating orders requires customers and products")

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    orders = []

    for index in range(1, count + 1):
        customer = rng.choice(customers)

        status = rng.choices(
            ["completed", "pending", "cancelled"],
            weights=[75, 15, 10],
            k=1,
        )[0]

        created_at = start + timedelta(
            seconds=rng.randrange(30 * 24 * 60 * 60)
        )

        selected_products = rng.sample(
            products,
            k=rng.randint(1, min(5, len(products))),
        )

        items = []

        for product in selected_products:
            quantity = rng.randint(1, 5)
            unit_price_rial = product["price_rial"]
            gross_rial = quantity * unit_price_rial
            discount_percent = rng.choice([0, 0, 0, 5, 10, 15])

            items.append(
                {
                    "product_sku": product["sku"],
                    "quantity": quantity,
                    "unit_price_rial": unit_price_rial,
                    "line_discount_rial": (
                        gross_rial * discount_percent // 100
                    ),
                    "created_at": created_at,
                }
            )

        completed_at = (
            created_at + timedelta(minutes=rng.randint(1, 180))
            if status == "completed"
            else None
        )

        orders.append(
            {
                "scenario": f"{batch}_order_{index:06d}",
                "customer_email": customer["email"],
                "created_at": created_at,
                "final_status": status,
                "completed_at": completed_at,
                "items": items,
            }
        )

    return orders

def verify_generated_batch(conn, orders, order_ids):
    expected = {
        order_ids[order["scenario"]]: (
            order["final_status"],
            order["completed_at"],
            len(order["items"]),
            sum(
                item["quantity"] * item["unit_price_rial"]
                - item["line_discount_rial"]
                for item in order["items"]
            ),
        )
        for order in orders
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                o.order_id,
                o.order_status,
                o.completed_at,
                COUNT(i.order_item_id),
                COALESCE(SUM(i.line_sales_rial), 0)
            FROM store_source.orders AS o
            LEFT JOIN store_source.order_items AS i
                ON i.order_id = o.order_id
            WHERE o.order_id = ANY(%s)
            GROUP BY o.order_id, o.order_status, o.completed_at
            """,
            (list(order_ids.values()),),
        )

        actual = {
            row[0]: (row[1], row[2], row[3], row[4])
            for row in cur.fetchall()
        }

    if actual != expected:
        mismatched = [
            order_id
            for order_id, values in expected.items()
            if actual.get(order_id) != values
        ]
        raise ValueError(
            f"Database verification failed for order IDs: "
            f"{mismatched[:10]}"
        )

    completed_sales = sum(
        values[3]
        for values in actual.values()
        if values[0] == "completed"
    )

    print(
        f"Database checks passed: {len(actual)} orders, "
        f"{completed_sales:,} IRR sales"
    )

def main():
    args = parse_args()
    rng = random.Random(args.seed)

    customers = generate_customers(args.customers, args.batch)
    products = generate_products(args.products, args.batch, rng)
    orders = generate_orders(
        args.orders, args.batch, customers, products, rng
    )
    
    dataset_json = json.dumps(
        {
            "customers": customers,
            "products": products,
            "orders": orders,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat(),
    )

    fingerprint = hashlib.sha256(dataset_json.encode("utf-8")).hexdigest()
    print(f"Dataset SHA256: {fingerprint}")

    line_count = sum(len(order["items"]) for order in orders)

    completed_sales = sum(
        item["quantity"] * item["unit_price_rial"]
        - item["line_discount_rial"]
        for order in orders
        if order["final_status"] == "completed"
        for item in order["items"]
    )

    print(f"Customers: {len(customers)}")
    print(f"Products: {len(products)}")
    print(f"Orders: {len(orders)}")
    print(f"Lines: {line_count}")
    print(f"Completed sales: {completed_sales:,} IRR")
    
    if not args.load:
        print("Preview only — no database writes")
        return

    dsn = os.environ["STORE_DATABASE_DSN"]
    password = getpass("PostgreSQL password: ")

    with psycopg.connect(dsn, password=password) as conn:
        order_ids, item_ids = insert_store_records(
            conn, customers, products, orders
        )
        verify_generated_batch(conn, orders, order_ids)

    print(
        f"Committed: {len(customers)} customers, "
        f"{len(products)} products, {len(order_ids)} orders, "
        f"{len(item_ids)} order lines"
    )

if __name__ == "__main__":
    main()