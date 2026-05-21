"""Pull HubSpot data into the local DB row shapes.

Funnel core (companies, contacts, stage cycles, deals) is high-confidence and built
on standard CRM v3 properties + property history. Multi-cycle stage events are
reconstructed by reading the history of the mapped properties (lifecyclestage and
optionally hs_lead_status) and running cycles.segment(), the same path the sample
data uses. Campaign/asset extraction is best-effort (Marketing Hub Enterprise) and
guarded by sync.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import config
import cycles


def _norm_ts(raw) -> str | None:
    if raw in (None, ""):
        return None
    s = str(raw)
    if s.isdigit():
        dt = datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
    else:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _current_stage(events: list[dict]) -> str | None:
    if not events:
        return None
    maxc = max(e["cycle"] for e in events)
    funnel = [e["stage"] for e in events if e["cycle"] == maxc and e["stage"] in config.FUNNEL_STAGES]
    return max(funnel, key=config.FUNNEL_STAGES.index) if funnel else "MTL"


def extract_companies(client, account_cfg: dict):
    """Companies as accounts, flagged Strategic by the configured property/values."""
    prop = account_cfg.get("strategic_property", "hs_target_account")
    true_vals = {str(v).lower() for v in account_cfg.get("strategic_true_values", ["true"])}
    base = ["name", "domain", "hubspot_owner_id", prop]
    accounts = []
    for r in client.paginate("/crm/v3/objects/companies", params={"properties": ",".join(base), "limit": 100}):
        p = r.get("properties", {})
        val = p.get(prop)
        accounts.append({
            "account_id": r["id"],
            "name": p.get("name"),
            "domain": p.get("domain"),
            "is_strategic": 1 if val is not None and str(val).lower() in true_vals else 0,
            "tier": str(val) if val is not None else None,
            "owner": p.get("hubspot_owner_id"),
        })
    return accounts


def extract_contacts(client, mapping: dict):
    """Return (contacts, stage_events) with multi-cycle events from property history."""
    vmap = cycles.value_stage_map(mapping)                 # (property, value) -> canonical stage
    history_props = sorted({d["property"] for d in mapping["stages"].values()})
    base_props = ["email", "firstname", "lastname", "company", "associatedcompanyid",
                  "createdate", "lifecyclestage", "hs_analytics_source", "hubspot_owner_id"]

    params = {"properties": ",".join(base_props), "propertiesWithHistory": ",".join(history_props), "limit": 100}

    contacts, events = [], []
    for r in client.paginate("/crm/v3/objects/contacts", params=params):
        p = r.get("properties", {})
        cid = r["id"]
        hist = r.get("propertiesWithHistory", {})

        transitions = []
        for prop in history_props:
            for version in hist.get(prop, []):
                stage = vmap.get((prop, version.get("value")))
                ts = _norm_ts(version.get("timestamp"))
                if stage and ts:
                    transitions.append((ts, stage))

        cycle_events = cycles.segment(transitions)
        for e in cycle_events:
            e["contact_id"] = cid
            e["source"] = "history"
        events.extend(cycle_events)

        contacts.append({
            "contact_id": cid,
            "email": p.get("email"),
            "first_name": p.get("firstname"),
            "last_name": p.get("lastname"),
            "company": p.get("company"),
            "account_id": p.get("associatedcompanyid"),
            "create_date": _norm_ts(p.get("createdate")),
            "current_stage": _current_stage(cycle_events) or p.get("lifecyclestage"),
            "original_source": p.get("hs_analytics_source"),
            "owner": p.get("hubspot_owner_id"),
        })
    return contacts, events


def _pipeline_stage_index(client) -> dict:
    idx = {}
    for p in client.get("/crm/v3/pipelines/deals").get("results", []):
        for s in p.get("stages", []):
            md = s.get("metadata", {})
            if str(md.get("isClosed")).lower() == "true":
                status = "won" if str(md.get("probability")) in ("1.0", "1") else "lost"
            else:
                status = "open"
            idx[s["id"]] = {"label": s["label"], "status": status, "pipeline": p["label"]}
    return idx


def extract_deals(client):
    idx = _pipeline_stage_index(client)
    props = ["dealname", "amount", "dealstage", "pipeline", "createdate", "closedate", "hubspot_owner_id"]
    deals, deal_contacts = [], []
    for r in client.paginate("/crm/v3/objects/deals",
                             params={"properties": ",".join(props), "associations": "contacts", "limit": 100}):
        p = r.get("properties", {})
        did = r["id"]
        meta = idx.get(p.get("dealstage"), {"label": p.get("dealstage"), "status": "open", "pipeline": p.get("pipeline")})
        if config.DEAL_PIPELINES and meta["pipeline"] not in config.DEAL_PIPELINES:
            continue
        deals.append({
            "deal_id": did, "name": p.get("dealname"),
            "amount": float(p["amount"]) if p.get("amount") else 0.0,
            "pipeline": meta["pipeline"], "stage": meta["label"], "status": meta["status"],
            "create_date": _norm_ts(p.get("createdate")), "close_date": _norm_ts(p.get("closedate")),
            "owner": p.get("hubspot_owner_id"),
        })
        for a in r.get("associations", {}).get("contacts", {}).get("results", []):
            deal_contacts.append({"deal_id": did, "contact_id": a["id"]})
    return deals, deal_contacts


def extract_campaigns_and_assets(client):
    """Best-effort campaigns + assets (Marketing Hub Enterprise). Guarded by sync.py."""
    campaigns, contact_campaigns, assets, asset_stats = [], [], [], []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    for c in client.paginate("/marketing/v3/campaigns", params={"limit": 100}):
        cid = c.get("id")
        props = c.get("properties", c)
        campaigns.append({"campaign_id": cid, "name": props.get("hs_name") or props.get("name"),
                          "type": props.get("hs_campaign_type") or props.get("type"),
                          "start_date": _norm_ts(props.get("hs_start_date")),
                          "end_date": _norm_ts(props.get("hs_end_date"))})
        for atype, key in [("form", "FORM"), ("landing_page", "LANDING_PAGE"),
                           ("email", "MARKETING_EMAIL"), ("social", "SOCIAL_BROADCAST")]:
            try:
                data = client.get(f"/marketing/v3/campaigns/{cid}/assets/{key}")
            except Exception:
                continue
            for a in data.get("results", []):
                assets.append({"asset_id": f"{cid}:{a.get('id')}", "campaign_id": cid,
                               "asset_type": atype, "name": a.get("name"), "published_at": now})
    return campaigns, contact_campaigns, assets, asset_stats
