"""Grounds Gemini SQL generation with compiled lookup_context and verifies BigQuery output."""

from __future__ import annotations

from decimal import Decimal

from google import genai
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
    resolve_latest_ga_flash_model,
    sanitize_display_text,
)

bq_client = bigquery.Client(project=PROJECT_ID)
catalog_client = dataplex_v1.CatalogServiceClient()

# Step 1 of agent tool flow: discover candidate tables dynamically with search_entries
search_hits = list(
    catalog_client.search_entries(
        request=dataplex_v1.SearchEntriesRequest(
            name=f"projects/{PROJECT_ID}/locations/global",
            query=f"name:{DATASET_ID} system=BIGQUERY type=TABLE location={REGION}",
            page_size=10,
        )
    )
)
discovered_resources = sorted(item.dataplex_entry.name for item in search_hits)
assert len(discovered_resources) == 3

# Step 2 of agent tool flow: compile schema, EntryLink glossary terms, and guidelines
compiled_context = catalog_client.lookup_context(
    request=dataplex_v1.LookupContextRequest(
        name=f"projects/{PROJECT_ID}/locations/{REGION}",
        resources=discovered_resources,
        options={"format": "yaml", "all_schema_fields": "true"},
    )
).context
assert "Net Settled Revenue" in compiled_context
assert "Approved Dispute Loss" in compiled_context

# Step 3 of agent tool flow: generate and execute governed BigQuery SQL via Gemini
genai_client = genai.Client(
    vertexai=True, project=PROJECT_ID, location=GEMINI_LOCATION
)
model_id = resolve_latest_ga_flash_model(genai_client)

grounded_prompt = (
    f"Project ID: `{PROJECT_ID}`\n"
    f"Catalog context (YAML):\n{compiled_context}\n\n"
    f"Question: {BUSINESS_QUESTION}"
)
ai_response = genai_client.models.generate_content(
    model=model_id,
    contents=grounded_prompt,
    config=types.GenerateContentConfig(
        system_instruction=(
            "You are a BigQuery SQL analyst. Write valid GoogleSQL using fully "
            "qualified table names (`project.dataset.table`) and follow every "
            "glossary term definition and table guideline in the catalog context."
        ),
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=GeneratedSqlQuery,
    ),
)
parsed_sql = GeneratedSqlQuery.model_validate_json(ai_response.text)
rows = list(bq_client.query(parsed_sql.sql_query).result())
governed_val = Decimal(str(rows[0]["net_settled_revenue_usd"])).quantize(Decimal("0.01"))

assert governed_val == Decimal("4570.00"), (
    f"Expected governed Net Settled Revenue of 4570.00, got {governed_val} "
    f"from SQL:\n{parsed_sql.sql_query}"
)

single_line_sql = " ".join(sanitize_display_text(parsed_sql.sql_query).split())
print(f"Grounded Gemini model ({GEMINI_LOCATION}): {model_id}")
print(f"Generated SQL (project ID masked): {single_line_sql}")
print(f"Applied catalog rules: {parsed_sql.applied_rules_summary}")
print(f"Verified BigQuery net_settled_revenue_usd: {governed_val}")
