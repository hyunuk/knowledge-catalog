"""Shared configuration, active GA Gemini model discovery, and masking helpers."""

from __future__ import annotations

import os
import re
from typing import List

from google import genai
from pydantic import BaseModel, Field

PROJECT_ID = os.environ.get("PROJECT_ID", "your-project-id").strip()
if not PROJECT_ID or PROJECT_ID in ("your-project-id", "YOUR-PROJECT-ID"):
    raise ValueError(
        "Set PROJECT_ID to an active Google Cloud project ID before running this script."
    )

REGION = os.environ.get("REGION", "us-central1").strip()
GEMINI_LOCATION = "global"

DATASET_ID = "kc_order_metrics"
ASPECT_TYPE_ID = "metric-calculation-contract"
GLOSSARY_ID = "commerce-finance-glossary"

NET_REVENUE_TERM_ID = "net-settled-revenue"
APPROVED_LOSS_TERM_ID = "approved-dispute-loss"

ENTRY_LINK_IDS = (
    "def-orders-gross-revenue",
    "def-adjustments-chargeback-loss",
    "rel-revenue-dispute-terms",
)

BUSINESS_QUESTION = (
    f"Using the BigQuery tables in dataset `{DATASET_ID}`, write a single GoogleSQL "
    "query that returns one row with column `net_settled_revenue_usd` (NUMERIC) "
    "representing total Net Settled Revenue across all orders in Q3 2026."
)

_GA_FLASH_RE = re.compile(r"^gemini-(\d+)(?:\.(\d+))?-flash$")
_PROJECT_NUM_PATH_RE = re.compile(r"projects/\d{10,12}\b")
_PROJECT_NUM_ASPECT_RE = re.compile(r"\b\d{10,12}\.([a-z0-9-]+)\.([a-z0-9_-]+)")


class GeneratedSqlQuery(BaseModel):
    """Structured SQL output returned by Gemini."""

    sql_query: str = Field(
        description="Executable BigQuery GoogleSQL SELECT statement returning net_settled_revenue_usd."
    )
    applied_rules_summary: str = Field(
        description="One-sentence explanation of how adjustments and fees were handled."
    )


def table_entry_name(table_id: str) -> str:
    """Returns the Knowledge Catalog @bigquery Entry resource name for a BigQuery table."""
    return (
        f"projects/{PROJECT_ID}/locations/{REGION}/entryGroups/@bigquery/entries/"
        f"bigquery.googleapis.com/projects/{PROJECT_ID}/datasets/{DATASET_ID}/tables/{table_id}"
    )


def all_table_entry_names() -> List[str]:
    """Returns the Entry names for all three commerce tables."""
    return [
        table_entry_name("orders"),
        table_entry_name("order_items"),
        table_entry_name("order_adjustments"),
    ]


def sanitize_display_text(text: str) -> str:
    """Replaces live project IDs and numeric project numbers for clean console output."""
    masked = text.replace(PROJECT_ID, "your-project-id")
    masked = _PROJECT_NUM_PATH_RE.sub("projects/your-project-number", masked)
    masked = _PROJECT_NUM_ASPECT_RE.sub(r"your-project-number.\1.\2", masked)
    return masked


def resolve_latest_ga_flash_model(client: genai.Client) -> str:
    """Dynamically selects the newest GA flash model from the active Gemini endpoint."""
    candidates = []
    for model in client.models.list():
        raw_name = (getattr(model, "name", "") or "").split("/")[-1]
        match = _GA_FLASH_RE.match(raw_name)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2) or 0)
            candidates.append(((major, minor), raw_name))
    if not candidates:
        raise RuntimeError(
            "No active GA Gemini flash model returned by client.models.list()."
        )
    candidates.sort(reverse=True)
    return candidates[0][1]
