"""Attaches column-level Aspects and compares lookup_entry across EntryView modes."""

from __future__ import annotations

from google.api_core import exceptions
from google.cloud import dataplex_v1
from google.protobuf import field_mask_pb2, struct_pb2

from catalog_config import (
    ASPECT_TYPE_ID,
    PROJECT_ID,
    REGION,
    sanitize_display_text,
    table_entry_name,
)

catalog_client = dataplex_v1.CatalogServiceClient()
aspect_type_path = f"projects/{PROJECT_ID}/locations/{REGION}/aspectTypes/{ASPECT_TYPE_ID}"

try:
    catalog_client.create_aspect_type(
        request=dataplex_v1.CreateAspectTypeRequest(
            parent=f"projects/{PROJECT_ID}/locations/{REGION}",
            aspect_type_id=ASPECT_TYPE_ID,
            aspect_type=dataplex_v1.AspectType(
                description="Structured SQL calculation contract for financial metric columns.",
                metadata_template=dataplex_v1.AspectType.MetadataTemplate(
                    name="MetricCalculationContract",
                    type_="record",
                    record_fields=[
                        dataplex_v1.AspectType.MetadataTemplate(
                            index=1,
                            name="metric_role",
                            type_="enum",
                            enum_values=[
                                dataplex_v1.AspectType.MetadataTemplate.EnumValue(index=1, name="ADDITIVE_BASE"),
                                dataplex_v1.AspectType.MetadataTemplate.EnumValue(index=2, name="CONDITIONAL_DEDUCTION"),
                                dataplex_v1.AspectType.MetadataTemplate.EnumValue(index=3, name="FEE_OFFSET"),
                                dataplex_v1.AspectType.MetadataTemplate.EnumValue(index=4, name="PASS_THROUGH_EXCLUDED"),
                            ],
                            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(required=True),
                        ),
                        dataplex_v1.AspectType.MetadataTemplate(
                            index=2,
                            name="sql_rule",
                            type_="string",
                            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(required=True),
                        ),
                        dataplex_v1.AspectType.MetadataTemplate(
                            index=3,
                            name="owner_team",
                            type_="string",
                            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(required=True),
                        ),
                    ],
                ),
            ),
        )
    ).result()
    print(f"Created custom AspectType: {ASPECT_TYPE_ID} ({REGION})")
except exceptions.AlreadyExists:
    print(f"AspectType already exists: {ASPECT_TYPE_ID} ({REGION})")


def attach_column_contracts(table_id: str, column_specs: dict[str, dict[str, str]]) -> None:
    """Attaches column-scoped @Schema.<column> Aspects to a BigQuery table Entry."""
    entry_name = table_entry_name(table_id)
    aspects_map = {}
    aspect_keys = []
    for col_name, payload in column_specs.items():
        map_key = f"{PROJECT_ID}.{REGION}.{ASPECT_TYPE_ID}@Schema.{col_name}"
        data_struct = struct_pb2.Struct()
        data_struct.update(payload)
        aspects_map[map_key] = dataplex_v1.Aspect(
            aspect_type=aspect_type_path,
            path=f"Schema.{col_name}",
            data=data_struct,
        )
        aspect_keys.append(map_key)

    catalog_client.update_entry(
        request=dataplex_v1.UpdateEntryRequest(
            entry=dataplex_v1.Entry(name=entry_name, aspects=aspects_map),
            update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
            aspect_keys=aspect_keys,
        )
    )


attach_column_contracts(
    "orders",
    {
        "gross_item_amount": {
            "metric_role": "ADDITIVE_BASE",
            "sql_rule": "Include in Net Settled Revenue before subtracting promo_discount_amount.",
            "owner_team": "revenue-accounting",
        },
        "sales_tax_amount": {
            "metric_role": "PASS_THROUGH_EXCLUDED",
            "sql_rule": "Collected government tax; always exclude from Net Settled Revenue.",
            "owner_team": "tax-compliance",
        },
    },
)

attach_column_contracts(
    "order_adjustments",
    {
        "chargeback_hold_amount": {
            "metric_role": "CONDITIONAL_DEDUCTION",
            "sql_rule": "Subtract ONLY rows where dispute_status = 'APPROVED_LOSS'; ignore PENDING_REVIEW holds.",
            "owner_team": "payments-risk",
        },
        "restocking_fee_amount": {
            "metric_role": "FEE_OFFSET",
            "sql_rule": "Add retained restocking_fee_amount across all rows into Net Settled Revenue.",
            "owner_team": "returns-ops",
        },
    },
)
print("Attached @Schema.<column> Aspects to orders and order_adjustments.")

adj_entry_name = table_entry_name("order_adjustments")
lookup_scope = f"projects/{PROJECT_ID}/locations/{REGION}"
target_suffix_root = f".{REGION}.{ASPECT_TYPE_ID}"

view_specs = [
    ("BASIC", dataplex_v1.EntryView.BASIC, []),
    ("FULL", dataplex_v1.EntryView.FULL, []),
    ("CUSTOM", dataplex_v1.EntryView.CUSTOM, [aspect_type_path]),
    ("ALL", dataplex_v1.EntryView.ALL, []),
]

print("Comparing lookup_entry EntryView modes on order_adjustments:")
custom_entry = None
for label, view_enum, custom_types in view_specs:
    entry = catalog_client.lookup_entry(
        request=dataplex_v1.LookupEntryRequest(
            name=lookup_scope,
            entry=adj_entry_name,
            view=view_enum,
            aspect_types=custom_types,
        )
    )
    matching_keys = [k for k in entry.aspects if target_suffix_root in k]
    populated_contracts = [
        k for k in matching_keys if entry.aspects[k].data is not None and len(entry.aspects[k].data) > 0
    ]
    print(
        f"  - EntryView.{label:<6}: aspect_keys={len(entry.aspects)} | "
        f"contract_keys={len(matching_keys)} | populated_contract_payloads={len(populated_contracts)}"
    )
    if label == "BASIC":
        assert len(entry.aspects) == 0
    elif label == "FULL":
        assert len(matching_keys) == 2 and len(populated_contracts) == 0
    elif label == "CUSTOM":
        assert len(entry.aspects) == 2 and len(populated_contracts) == 2
        custom_entry = entry
    elif label == "ALL":
        assert len(entry.aspects) > 2 and len(populated_contracts) == 2

assert custom_entry is not None
print("Verified column Aspect contracts (matched by suffix, normalized to PROJECT_NUMBER):")
for raw_key, aspect in sorted(custom_entry.aspects.items()):
    fields = dict(aspect.data)
    print(
        f"  - key={sanitize_display_text(raw_key)} | "
        f"role={fields['metric_role']} | rule={fields['sql_rule']}"
    )
