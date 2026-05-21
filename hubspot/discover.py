"""Inspect a HubSpot portal and propose how the funnel + accounts map to its config.

Run AFTER creating the token:  python -m hubspot.discover

Prints a report and writes (REVIEW before syncing):
  - discovery_report.json   : everything found
  - stage_mapping.json       : MTL/MCL/MQL/SAL/SQL/Customer -> (property, value)
  - account_config.json      : which company property marks a Strategic Account
                               (Named Target) and which values count as strategic

Extraction reads the PROPERTY HISTORY of the referenced properties (not single
date stamps) and segments it into funnel cycles, so repeated/resell passes are
captured. MTL/MCL/SAL are typically custom, so the proposal is a best-effort
heuristic — confirm the values before running sync.py.
"""

from __future__ import annotations

import json

import config
from hubspot.client import HubSpotClient

STAGE_HINTS = {
    "MTL": ["target", "mtl", "subscriber"],
    "MCL": ["captured", "mcl", "lead"],
    "MQL": ["marketing qualified", "marketingqualifiedlead", "mql"],
    "SAL": ["accepted", "sales accepted", "sal"],
    "SQL": ["sales qualified", "salesqualifiedlead", "sql"],
    "Customer": ["customer"],
}
STRATEGIC_PROP_HINTS = ["target_account", "target account", "strategic", "named", "abm", "icp", "account_tier", "tier"]
STRATEGIC_VALUE_HINTS = ["strategic", "target", "named", "tier 1", "tier1", "true", "yes"]


def _match(options, hints):
    for hint in hints:
        for o in options:
            if hint in f"{o.get('label','')} {o.get('value','')}".lower():
                return o
    return None


def discover(client: HubSpotClient | None = None) -> dict:
    client = client or HubSpotClient()
    report: dict = {}

    cprops = client.get("/crm/v3/properties/contacts").get("results", [])
    lifecycle = next((p for p in cprops if p["name"] == "lifecyclestage"), None)
    lead_status = next((p for p in cprops if p["name"] == "hs_lead_status"), None)
    lifecycle_opts = lifecycle.get("options", []) if lifecycle else []
    lead_opts = lead_status.get("options", []) if lead_status else []
    report["lifecycle_stages"] = [{"label": o["label"], "value": o["value"]} for o in lifecycle_opts]
    report["lead_status_options"] = [{"label": o["label"], "value": o["value"]} for o in lead_opts]

    pipelines = client.get("/crm/v3/pipelines/deals").get("results", [])
    report["deal_pipelines"] = [{
        "id": p["id"], "label": p["label"],
        "stages": [{"label": s["label"], "status": _stage_status(s)} for s in p.get("stages", [])],
    } for p in pipelines]

    coprops = client.get("/crm/v3/properties/companies").get("results", [])
    candidates = [p for p in coprops
                  if any(h in f"{p['name']} {p.get('label','')}".lower() for h in STRATEGIC_PROP_HINTS)]
    report["strategic_property_candidates"] = [
        {"name": p["name"], "label": p.get("label"),
         "options": [{"label": o["label"], "value": o["value"]} for o in p.get("options", [])]}
        for p in candidates]

    report["proposed_stage_mapping"] = _propose_stages(lifecycle_opts, lead_opts)
    report["proposed_account_config"] = _propose_account(candidates)
    report["notes"] = _notes(report)
    return report


def _stage_status(stage):
    md = stage.get("metadata", {})
    if str(md.get("isClosed")).lower() == "true":
        return "won" if str(md.get("probability")) in ("1.0", "1") else "lost"
    return "open"


def _propose_stages(lifecycle_opts, lead_opts):
    stages = {}
    for canon, hints in STAGE_HINTS.items():
        lc = _match(lifecycle_opts, hints)
        if lc:
            stages[canon] = {"property": "lifecyclestage", "value": lc["value"], "_matched": lc["label"]}
            continue
        ls = _match(lead_opts, hints)
        if ls:
            stages[canon] = {"property": "hs_lead_status", "value": ls["value"], "_matched": f"lead status: {ls['label']}"}
            continue
        stages[canon] = {**config._DEFAULT_STAGE_MAPPING["stages"][canon], "_unresolved": True}
    return {"stages": stages}


def _propose_account(candidates):
    if not candidates:
        return {**config._DEFAULT_ACCOUNT_CONFIG, "_unresolved": True}
    # Prefer an explicit target-account flag if present.
    best = next((c for c in candidates if "target" in c["name"].lower()), candidates[0])
    opts = best.get("options", [])
    true_vals = [o["value"] for o in opts
                 if any(h in f"{o.get('label','')} {o.get('value','')}".lower() for h in STRATEGIC_VALUE_HINTS)]
    if not true_vals:  # likely a boolean flag
        true_vals = ["true", "True"]
    return {"strategic_property": best["name"], "strategic_true_values": true_vals, "_matched": best.get("label")}


def _notes(report):
    notes = []
    for canon, m in report["proposed_stage_mapping"]["stages"].items():
        if m.get("_unresolved"):
            notes.append(f"{canon}: NOT auto-resolved — set property/value in stage_mapping.json.")
        else:
            notes.append(f"{canon}: {m['property']} = '{m['value']}' (matched {m.get('_matched')}).")
    ac = report["proposed_account_config"]
    if ac.get("_unresolved"):
        notes.append("Strategic accounts: no candidate company property found — set strategic_property manually.")
    else:
        notes.append(f"Strategic accounts: company property '{ac['strategic_property']}' "
                     f"true when value in {ac['strategic_true_values']}.")
    return notes


def _clean(d):
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if not k.startswith("_")}
    return d


def main():
    report = discover()
    (config.BASE_DIR / "discovery_report.json").write_text(json.dumps(report, indent=2))
    config.STAGE_MAPPING_PATH.write_text(json.dumps(_clean(report["proposed_stage_mapping"]), indent=2))
    config.ACCOUNT_CONFIG_PATH.write_text(json.dumps(_clean(report["proposed_account_config"]), indent=2))

    print("=== Lifecycle stages ===")
    for s in report["lifecycle_stages"]:
        print(f"  {s['label']:<28} value={s['value']}")
    print("\n=== Lead status options ===")
    for o in report["lead_status_options"]:
        print(f"  {o['label']:<28} value={o['value']}")
    print("\n=== Strategic-account property candidates (companies) ===")
    for c in report["strategic_property_candidates"]:
        print(f"  {c['name']} ({c['label']}) options={[o['value'] for o in c['options']][:8]}")
    print("\n=== Deal pipelines ===")
    for p in report["deal_pipelines"]:
        print(f"  {p['label']}: " + ", ".join(f"{s['label']}[{s['status']}]" for s in p["stages"]))
    print("\n=== Proposed mapping ===")
    for n in report["notes"]:
        print(f"  • {n}")
    print(f"\nWrote discovery_report.json, stage_mapping.json, account_config.json to {config.BASE_DIR}")
    print("REVIEW stage_mapping.json and account_config.json before running sync.py.")


if __name__ == "__main__":
    main()
