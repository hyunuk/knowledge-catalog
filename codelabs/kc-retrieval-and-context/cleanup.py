"""Deletes EntryLinks, GlossaryTerms, Glossary, AspectType, and BigQuery resources."""

from __future__ import annotations

from google.api_core import exceptions
from google.cloud import bigquery
from google.cloud import dataplex_v1

from catalog_config import (
    APPROVED_LOSS_TERM_ID,
    ASPECT_TYPE_ID,
    DATASET_ID,
    GLOSSARY_ID,
    NET_REVENUE_TERM_ID,
    PROJECT_ID,
    REGION,
)

catalog_client = dataplex_v1.CatalogServiceClient()
glossary_client = dataplex_v1.BusinessGlossaryServiceClient()
bq_client = bigquery.Client(project=PROJECT_ID)

entry_link_paths = [
    f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@bigquery/entryLinks/def-orders-gross-revenue",
    f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@bigquery/entryLinks/def-adjustments-chargeback-loss",
    f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@dataplex/entryLinks/rel-revenue-dispute-terms",
]
for link_path in entry_link_paths:
    try:
        catalog_client.delete_entry_link(name=link_path)
        print(f"Deleted EntryLink: {link_path.rsplit('/', 1)[-1]}")
    except exceptions.NotFound:
        print(f"EntryLink already absent: {link_path.rsplit('/', 1)[-1]}")

glossary_path = f"projects/{PROJECT_ID}/locations/{REGION}/glossaries/{GLOSSARY_ID}"
for term_id in (NET_REVENUE_TERM_ID, APPROVED_LOSS_TERM_ID):
    term_path = f"{glossary_path}/terms/{term_id}"
    try:
        glossary_client.delete_glossary_term(name=term_path)
        print(f"Deleted GlossaryTerm: {term_id}")
    except exceptions.NotFound:
        print(f"GlossaryTerm already absent: {term_id}")

try:
    glossary_client.delete_glossary(name=glossary_path).result()
    print(f"Deleted Glossary: {GLOSSARY_ID}")
except exceptions.NotFound:
    print(f"Glossary already absent: {GLOSSARY_ID}")

aspect_type_path = f"projects/{PROJECT_ID}/locations/{REGION}/aspectTypes/{ASPECT_TYPE_ID}"
try:
    catalog_client.delete_aspect_type(name=aspect_type_path).result()
    print(f"Deleted AspectType: {ASPECT_TYPE_ID}")
except exceptions.NotFound:
    print(f"AspectType already absent: {ASPECT_TYPE_ID}")

for table_id in ("order_adjustments", "order_items", "orders"):
    try:
        bq_client.delete_table(f"{PROJECT_ID}.{DATASET_ID}.{table_id}")
        print(f"Deleted BigQuery table: {DATASET_ID}.{table_id}")
    except exceptions.NotFound:
        print(f"BigQuery table already absent: {DATASET_ID}.{table_id}")

try:
    bq_client.delete_dataset(
        f"{PROJECT_ID}.{DATASET_ID}", delete_contents=True
    )
    print(f"Deleted BigQuery dataset: {DATASET_ID}")
except exceptions.NotFound:
    print(f"BigQuery dataset already absent: {DATASET_ID}")
