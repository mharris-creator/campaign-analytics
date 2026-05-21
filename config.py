"""Central configuration: funnel definition, stage->HubSpot mapping, account
classification, paths.

Two portal-specific configs are produced by hubspot/discover.py and reviewed by
you before syncing:
  - stage_mapping.json   : how MTL/MCL/MQL/SAL/SQL/Customer map to lifecyclestage
                           (and optionally hs_lead_status) values.
  - account_config.json  : which company property marks a Strategic Account
                           (Named Target) and which values count as strategic.

Because contacts can run the funnel more than once (resell back into customers),
stage entries are reconstructed from PROPERTY HISTORY and segmented into cycles
(see cycles.py) — single date-stamp properties only capture one entry and would
lose every repeat pass.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = os.getenv("CAMPAIGN_DB_PATH") or str(BASE_DIR / "data" / "campaign_analytics.db")
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
STAGE_MAPPING_PATH = BASE_DIR / "stage_mapping.json"
ACCOUNT_CONFIG_PATH = BASE_DIR / "account_config.json"
REPORTS_DIR = BASE_DIR / "reports"

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
DEAL_PIPELINES = [p.strip() for p in os.getenv("DEAL_PIPELINES", "").split(",") if p.strip()]

# The CONVERSION funnel starts at MCL, because net-new contacts/accounts can enter
# organically as an MCL (e.g., an inbound form fill) without ever being a target.
# Every conversion % is computed against this list, anchored at MCL.
FUNNEL_STAGES = ["MCL", "MQL", "SAL", "SQL", "Customer"]

# MTL (Marketing Target Lead) is a PRE-funnel / resting state: a named target that
# has not engaged yet, or a contact reset to start a new cycle. It is reported
# separately as a "Target Pool" with an MTL->MCL activation rate, NOT as the funnel
# denominator. It is still tracked per cycle so we can show activation.
TARGET_STAGE = "MTL"

FUNNEL_LABELS = {
    "MTL": "Marketing Target Lead",
    "MCL": "Marketing Captured Lead",
    "MQL": "Marketing Qualified Lead",
    "SAL": "Sales Accepted Lead",
    "SQL": "Sales Qualified Lead",
    "Customer": "Customer",
}

DEAL_STATUSES = ["open", "won", "lost"]
CYCLE_TYPES = ["new", "resell"]
ACCOUNT_SEGMENTS = ["all", "strategic", "other"]

# Placeholder mapping. Each stage maps to the (property, value) representing it.
# Extraction reads the property HISTORY of every referenced property, maps each
# historical value to its stage, then cycles.segment() splits into funnel cycles.
_DEFAULT_STAGE_MAPPING = {
    "stages": {
        "MTL": {"property": "lifecyclestage", "value": "subscriber"},
        "MCL": {"property": "lifecyclestage", "value": "lead"},
        "MQL": {"property": "lifecyclestage", "value": "marketingqualifiedlead"},
        "SAL": {"property": "hs_lead_status", "value": "SALES_ACCEPTED"},
        "SQL": {"property": "lifecyclestage", "value": "salesqualifiedlead"},
        "Customer": {"property": "lifecyclestage", "value": "customer"},
    }
}

_DEFAULT_ACCOUNT_CONFIG = {
    "strategic_property": "hs_target_account",
    "strategic_true_values": ["true", "True", "STRATEGIC", "Strategic Account", "Named Target"],
}


def stage_mapping() -> dict:
    if STAGE_MAPPING_PATH.exists():
        return json.loads(STAGE_MAPPING_PATH.read_text())
    return _DEFAULT_STAGE_MAPPING


def account_config() -> dict:
    if ACCOUNT_CONFIG_PATH.exists():
        return json.loads(ACCOUNT_CONFIG_PATH.read_text())
    return _DEFAULT_ACCOUNT_CONFIG
