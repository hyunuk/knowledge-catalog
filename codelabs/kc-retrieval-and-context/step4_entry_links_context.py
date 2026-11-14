"""Links Business Glossary terms to columns via EntryLink and compiles lookup_context."""

from __future__ import annotations

import time

from google.api_core import exceptions
from google.cloud import dataplex_v1
from google.protobuf import field_mask_pb2, struct_pb2

from catalog_config import (
    APPROVED_LOSS_TERM_ID,
    ASPECT_TYPE_ID,
    GLOSSARY_ID,
    NET_REVENUE_TERM_ID,
    PROJECT_ID,
    REGION,
    all_table_entry_names,
    sanitize_display_text,
    table_entry_name,
)

catalog_client = dataplex_v1.CatalogServiceClient()
glossary_client = dataplex_v1.BusinessGlossaryServiceClient()

orders_entry = table_entry_name("orders")
adjustments_entry = table_entry_name("order_adjustments")

# Extract numeric PROJECT_NUMBER from the normalized aspect key attached in Step 3
hydrated_adj = catalog_client.lookup_entry(
    request=dataplex_v1.LookupEntryRequest(
        name=f"projects/{PROJECT_ID}/locations/{REGION}",
        entry=adjustments_entry,
        view=dataplex_v1.EntryView.ALL,
    )
)
suffix_marker = f".{REGION}.{ASPECT_TYPE_ID}@Schema.chargeback_hold_amount"
normalized_key = next(k for k in hydrated_adj.aspects if k.endswith(suffix_marker))
project_number = normalized_key.split(".", 1)[0]

glossary_parent = f"projects/{PROJECT_ID}/locations/{REGION}"
glossary_name = f"{glossary_parent}/glossaries/{GLOSSARY_ID}"

try:
    glossary_client.create_glossary(
        request=dataplex_v1.CreateGlossaryRequest(
            parent=glossary_parent,
            glossary_id=GLOSSARY_ID,
            glossary=dataplex_v1.Glossary(
                display_name="Commerce Finance Glossary",
                description="Governed B2B revenue and dispute settlement definitions.",
            ),
        )
    ).result()
    print(f"Created regional Business Glossary: {GLOSSARY_ID} ({REGION})")
except exceptions.AlreadyExists:
    print(f"Reusing existing Business Glossary: {GLOSSARY_ID} ({REGION})")

term_definitions = [
    (
        NET_REVENUE_TERM_ID,
        "Net Settled Revenue",
        (
            "Total gross_item_amount minus promo_discount_amount from orders, "
            "minus chargeback_hold_amount from order_adjustments ONLY where "
            "dispute_status = 'APPROVED_LOSS', plus restocking_fee_amount from "
            "order_adjustments across all rows. Exclude sales_tax_amount and "
            "exclude PENDING_REVIEW disputes."
        ),
    ),
    (
        APPROVED_LOSS_TERM_ID,
        "Approved Dispute Loss",
        (
            "Finalized payment chargeback loss recorded in order_adjustments with "
            "dispute_status = 'APPROVED_LOSS'. Rows marked 'PENDING_REVIEW' or "
            "'NONE' are open holds and are never subtracted from Net Settled Revenue."
        ),
    ),
]

for term_id, display_name, description in term_definitions:
    try:
        glossary_client.create_glossary_term(
            request=dataplex_v1.CreateGlossaryTermRequest(
                parent=glossary_name,
                term_id=term_id,
                term=dataplex_v1.GlossaryTerm(
                    parent=glossary_name,
                    display_name=display_name,
                    description=description,
                ),
            )
        )
    except exceptions.AlreadyExists:
        pass


def mirrored_term_entry_name(term_id: str) -> str:
    """Builds the @dataplex catalog Entry path for a regional GlossaryTerm."""
    return (
        f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@dataplex/entries/"
        f"projects/{project_number}/locations/{REGION}/glossaries/{GLOSSARY_ID}/terms/{term_id}"
    )


net_revenue_term_entry = mirrored_term_entry_name(NET_REVENUE_TERM_ID)
approved_loss_term_entry = mirrored_term_entry_name(APPROVED_LOSS_TERM_ID)

for term_entry in (net_revenue_term_entry, approved_loss_term_entry):
    for attempt in range(15):
        try:
            catalog_client.lookup_entry(
                request=dataplex_v1.LookupEntryRequest(
                    name=f"projects/{PROJECT_ID}/locations/{REGION}",
                    entry=term_entry,
                    view=dataplex_v1.EntryView.BASIC,
                )
            )
            break
        except (exceptions.NotFound, exceptions.PermissionDenied):
            if attempt == 14:
                raise
            time.sleep(3)

bq_entry_group = f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@bigquery"
dp_entry_group = f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@dataplex"

link_specs = [
    (
        bq_entry_group,
        "def-orders-gross-revenue",
        "projects/dataplex-types/locations/global/entryLinkTypes/definition",
        [
            dataplex_v1.EntryLink.EntryReference(
                name=orders_entry,
                path="Schema.gross_item_amount",
                type_=dataplex_v1.EntryLink.EntryReference.Type.SOURCE,
            ),
            dataplex_v1.EntryLink.EntryReference(
                name=net_revenue_term_entry,
                path="",
                type_=dataplex_v1.EntryLink.EntryReference.Type.TARGET,
            ),
        ],
    ),
    (
        bq_entry_group,
        "def-adjustments-chargeback-loss",
        "projects/dataplex-types/locations/global/entryLinkTypes/definition",
        [
            dataplex_v1.EntryLink.EntryReference(
                name=adjustments_entry,
                path="Schema.chargeback_hold_amount",
                type_=dataplex_v1.EntryLink.EntryReference.Type.SOURCE,
            ),
            dataplex_v1.EntryLink.EntryReference(
                name=approved_loss_term_entry,
                path="",
                type_=dataplex_v1.EntryLink.EntryReference.Type.TARGET,
            ),
        ],
    ),
    (
        dp_entry_group,
        "rel-revenue-dispute-terms",
        "projects/dataplex-types/locations/global/entryLinkTypes/related",
        [
            dataplex_v1.EntryLink.EntryReference(
                name=net_revenue_term_entry,
                type_=dataplex_v1.EntryLink.EntryReference.Type.UNSPECIFIED,
            ),
            dataplex_v1.EntryLink.EntryReference(
                name=approved_loss_term_entry,
                type_=dataplex_v1.EntryLink.EntryReference.Type.UNSPECIFIED,
            ),
        ],
    ),
]

for parent_group, link_id, link_type, references in link_specs:
    try:
        catalog_client.create_entry_link(
            request=dataplex_v1.CreateEntryLinkRequest(
                parent=parent_group,
                entry_link_id=link_id,
                entry_link=dataplex_v1.EntryLink(
                    entry_link_type=link_type,
                    entry_references=references,
                ),
            )
        )
        print(f"Created EntryLink: {link_id} ({link_type.rsplit('/', 1)[-1]})")
    except exceptions.AlreadyExists:
        print(f"Reusing existing EntryLink: {link_id}")

# Attach agent SQL join guidelines to orders so lookup_context compiles join + formula guidance
guidelines_struct = struct_pb2.Struct()
guidelines_struct.update(
    {
        "instructions": (
            "Join orders to order_adjustments on order_id for Net Settled Revenue; "
            "avoid joining order_items directly to order_adjustments to prevent SKU fan-out."
        ),
        "userManaged": True,
    }
)
catalog_client.update_entry(
    request=dataplex_v1.UpdateEntryRequest(
        entry=dataplex_v1.Entry(
            name=orders_entry,
            aspects={
                "dataplex-types.global.guidelines": dataplex_v1.Aspect(
                    aspect_type="projects/dataplex-types/locations/global/aspectTypes/guidelines",
                    data=guidelines_struct,
                )
            },
        ),
        update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
        aspect_keys=["dataplex-types.global.guidelines"],
    )
)

verified_links = list(
    catalog_client.lookup_entry_links(
        request=dataplex_v1.LookupEntryLinksRequest(
            name=f"projects/{PROJECT_ID}/locations/{REGION}",
            entry=orders_entry,
        )
    )
)
assert len(verified_links) >= 1, "Expected at least 1 EntryLink on orders"
print(f"Verified EntryLinks on orders via lookup_entry_links: count={len(verified_links)}")

compiled_yaml = ""
for attempt in range(10):
    ctx_resp = catalog_client.lookup_context(
        request=dataplex_v1.LookupContextRequest(
            name=f"projects/{PROJECT_ID}/locations/{REGION}",
            resources=all_table_entry_names(),
            options={"format": "yaml", "all_schema_fields": "true"},
        )
    )
    compiled_yaml = ctx_resp.context
    if "Net Settled Revenue" in compiled_yaml and "Approved Dispute Loss" in compiled_yaml:
        break
    time.sleep(3)

assert "Net Settled Revenue" in compiled_yaml
assert "Approved Dispute Loss" in compiled_yaml
assert "guidelines:" in compiled_yaml

print("Compiled lookup_context YAML now embeds column EntryLink terms and guidelines:")
for line in sanitize_display_text(compiled_yaml).splitlines():
    if any(tok in line for tok in ("simpleName:", "guidelines:", "name: gross_item_amount", "name: chargeback_hold_amount", "terms:")):
        print(f"  {line}")
