"""Discovers candidate tables with search_entries and inspects summary payload shape."""

from __future__ import annotations

import time

from google.cloud import dataplex_v1

from catalog_config import DATASET_ID, PROJECT_ID, REGION, sanitize_display_text

catalog_client = dataplex_v1.CatalogServiceClient()
search_parent = f"projects/{PROJECT_ID}/locations/global"

predicate_query = f"name:{DATASET_ID} system=BIGQUERY type=TABLE location={REGION}"
discovered_entries = []
for attempt in range(12):
    pager = catalog_client.search_entries(
        request=dataplex_v1.SearchEntriesRequest(
            name=search_parent,
            query=predicate_query,
            page_size=10,
        )
    )
    discovered_entries = [item.dataplex_entry for item in pager]
    if len(discovered_entries) >= 3:
        break
    time.sleep(3)

assert len(discovered_entries) == 3, (
    f"Expected 3 BigQuery table entries for {DATASET_ID}, got {len(discovered_entries)}"
)

print(f"Predicate search ({predicate_query}) matched {len(discovered_entries)} entries:")
for entry in sorted(discovered_entries, key=lambda e: e.name):
    aspect_count = len(entry.aspects)
    assert aspect_count == 0, "search_entries summary records never populate entry.aspects"
    short_id = entry.entry_source.display_name or entry.name.rsplit("/", 1)[-1]
    print(
        f"  - {short_id}: entry_name={sanitize_display_text(entry.name)} | "
        f"aspects_returned={aspect_count}"
    )

nl_query = "B2B order payment dispute holds and restocking fees"
semantic_hits = list(
    catalog_client.search_entries(
        request=dataplex_v1.SearchEntriesRequest(
            name=search_parent,
            query=nl_query,
            semantic_search=True,
            page_size=5,
        )
    )
)
matched_names = [
    h.dataplex_entry.name.rsplit("/", 1)[-1]
    for h in semantic_hits
    if DATASET_ID in h.dataplex_entry.name
]
assert "order_adjustments" in matched_names, (
    f"Expected semantic search to surface order_adjustments, got {matched_names}"
)
print(f"Semantic search ('{nl_query}') surfaced dataset tables: {matched_names}")
