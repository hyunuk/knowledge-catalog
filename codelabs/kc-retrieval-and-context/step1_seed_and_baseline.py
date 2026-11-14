"""Seeds commerce tables in BigQuery and runs baseline SQL generation with raw context."""

from __future__ import annotations

import time

from google import genai
from google.api_core import exceptions
from google.cloud import bigquery
from google.cloud import dataplex_v1
from google.genai import types

from catalog_config import (
    BUSINESS_QUESTION,
    DATASET_ID,
    GEMINI_LOCATION,
    GeneratedSqlQuery,
    PROJECT_ID,
    REGION,
    all_table_entry_names,
    resolve_latest_ga_flash_model,
    sanitize_display_text,
)

bq_client = bigquery.Client(project=PROJECT_ID)
catalog_client = dataplex_v1.CatalogServiceClient()

dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
dataset_ref.location = REGION
dataset_ref.description = "B2B commerce order fulfillment and settlement records."
bq_client.create_dataset(dataset_ref, exists_ok=True)

orders_table = bigquery.Table(
    f"{PROJECT_ID}.{DATASET_ID}.orders",
    schema=[
        bigquery.SchemaField("order_id", "STRING", mode="REQUIRED", description="Unique B2B order identifier"),
        bigquery.SchemaField("order_quarter", "STRING", mode="REQUIRED", description="Fiscal quarter label"),
        bigquery.SchemaField("gross_item_amount", "NUMERIC", mode="REQUIRED", description="Subtotal of ordered items before discounts"),
        bigquery.SchemaField("promo_discount_amount", "NUMERIC", mode="REQUIRED", description="Promotional discount amount"),
        bigquery.SchemaField("sales_tax_amount", "NUMERIC", mode="REQUIRED", description="Collected state and local tax amount"),
    ],
)
orders_table.description = "Order-level subtotals, promotional discounts, and collected tax."
bq_client.create_table(orders_table, exists_ok=True)

items_table = bigquery.Table(
    f"{PROJECT_ID}.{DATASET_ID}.order_items",
    schema=[
        bigquery.SchemaField("order_id", "STRING", mode="REQUIRED", description="Parent order identifier"),
        bigquery.SchemaField("sku_code", "STRING", mode="REQUIRED", description="Catalog SKU identifier"),
        bigquery.SchemaField("quantity", "INT64", mode="REQUIRED", description="Units fulfilled"),
        bigquery.SchemaField("line_amount", "NUMERIC", mode="REQUIRED", description="Extended SKU subtotal"),
    ],
)
items_table.description = "SKU-level fulfillment breakdown for each B2B order."
bq_client.create_table(items_table, exists_ok=True)

adjustments_table = bigquery.Table(
    f"{PROJECT_ID}.{DATASET_ID}.order_adjustments",
    schema=[
        bigquery.SchemaField("order_id", "STRING", mode="REQUIRED", description="Parent order identifier"),
        bigquery.SchemaField("dispute_status", "STRING", mode="REQUIRED", description="Dispute review status code"),
        bigquery.SchemaField("chargeback_hold_amount", "NUMERIC", mode="REQUIRED", description="Payment processor disputed hold amount"),
        bigquery.SchemaField("restocking_fee_amount", "NUMERIC", mode="REQUIRED", description="Fee retained on returned B2B merchandise"),
    ],
)
adjustments_table.description = "Post-order payment dispute holds and return restocking fees."
bq_client.create_table(adjustments_table, exists_ok=True)

seed_sql = f"""
TRUNCATE TABLE `{PROJECT_ID}.{DATASET_ID}.orders`;
INSERT INTO `{PROJECT_ID}.{DATASET_ID}.orders`
  (order_id, order_quarter, gross_item_amount, promo_discount_amount, sales_tax_amount)
VALUES
  ('ORD-1001', 'Q3 2026', NUMERIC '1000.00', NUMERIC '50.00', NUMERIC '80.00'),
  ('ORD-1002', 'Q3 2026', NUMERIC '2000.00', NUMERIC '100.00', NUMERIC '160.00'),
  ('ORD-1003', 'Q3 2026', NUMERIC '1500.00', NUMERIC '0.00', NUMERIC '120.00'),
  ('ORD-1004', 'Q3 2026', NUMERIC '500.00', NUMERIC '50.00', NUMERIC '40.00');

TRUNCATE TABLE `{PROJECT_ID}.{DATASET_ID}.order_items`;
INSERT INTO `{PROJECT_ID}.{DATASET_ID}.order_items`
  (order_id, sku_code, quantity, line_amount)
VALUES
  ('ORD-1001', 'SKU-RACK-01', 2, NUMERIC '1000.00'),
  ('ORD-1002', 'SKU-SWITCH-04', 4, NUMERIC '2000.00'),
  ('ORD-1003', 'SKU-CABLE-10', 15, NUMERIC '1500.00'),
  ('ORD-1004', 'SKU-UPS-02', 1, NUMERIC '500.00');

TRUNCATE TABLE `{PROJECT_ID}.{DATASET_ID}.order_adjustments`;
INSERT INTO `{PROJECT_ID}.{DATASET_ID}.order_adjustments`
  (order_id, dispute_status, chargeback_hold_amount, restocking_fee_amount)
VALUES
  ('ORD-1001', 'NONE', NUMERIC '0.00', NUMERIC '0.00'),
  ('ORD-1002', 'PENDING_REVIEW', NUMERIC '400.00', NUMERIC '0.00'),
  ('ORD-1003', 'APPROVED_LOSS', NUMERIC '300.00', NUMERIC '45.00'),
  ('ORD-1004', 'PENDING_REVIEW', NUMERIC '150.00', NUMERIC '25.00');
"""
bq_client.query(seed_sql).result()
print(f"Seeded 3 BigQuery tables in dataset {DATASET_ID} ({REGION}).")

entry_names = all_table_entry_names()
for entry_name in entry_names:
    for attempt in range(20):
        try:
            catalog_client.lookup_entry(
                request=dataplex_v1.LookupEntryRequest(
                    name=f"projects/{PROJECT_ID}/locations/{REGION}",
                    entry=entry_name,
                    view=dataplex_v1.EntryView.BASIC,
                )
            )
            break
        except (exceptions.NotFound, exceptions.PermissionDenied):
            if attempt == 19:
                raise
            time.sleep(3)

baseline_ctx_response = catalog_client.lookup_context(
    request=dataplex_v1.LookupContextRequest(
        name=f"projects/{PROJECT_ID}/locations/{REGION}",
        resources=entry_names,
        options={"format": "yaml", "all_schema_fields": "true"},
    )
)
baseline_yaml = baseline_ctx_response.context
assert "schema:" in baseline_yaml
assert "terms:" not in baseline_yaml
assert "guidelines:" not in baseline_yaml

print("Baseline lookup_context check: schema present=True, terms present=False, guidelines present=False")
for line in sanitize_display_text(baseline_yaml).splitlines()[:20]:
    print(f"  {line}")

genai_client = genai.Client(
    vertexai=True, project=PROJECT_ID, location=GEMINI_LOCATION
)
model_id = resolve_latest_ga_flash_model(genai_client)

baseline_prompt = (
    f"Project ID: `{PROJECT_ID}`\n"
    f"Catalog context (YAML):\n{baseline_yaml}\n\n"
    f"Question: {BUSINESS_QUESTION}"
)
ai_response = genai_client.models.generate_content(
    model=model_id,
    contents=baseline_prompt,
    config=types.GenerateContentConfig(
        system_instruction=(
            "You are a BigQuery SQL analyst. Write valid GoogleSQL using fully "
            "qualified table names (`project.dataset.table`) grounded strictly "
            "in the provided catalog context."
        ),
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=GeneratedSqlQuery,
    ),
)
parsed_sql = GeneratedSqlQuery.model_validate_json(ai_response.text)
rows = list(bq_client.query(parsed_sql.sql_query).result())
baseline_val = float(rows[0]["net_settled_revenue_usd"])
assert baseline_val > 0

print(f"Active Gemini model ({GEMINI_LOCATION}): {model_id}")
print(f"Baseline SQL summary: {parsed_sql.applied_rules_summary}")
print(f"Baseline net_settled_revenue_usd: {baseline_val:.2f} (governed finance target: 4570.00)")
