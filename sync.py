"""Sync HubSpot -> local SQLite. Run on a schedule (cron) or on demand.

  python sync.py                 # full sync (contacts, deals, campaigns/assets)
  python sync.py --report        # sync, then write an exec PDF to reports/

Schedule example (daily 6am):  0 6 * * *  cd /path/to/campaign-analytics && \
    .venv/bin/python sync.py --report >> sync.log 2>&1
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import config
from db import load
from hubspot import extract
from hubspot.client import HubSpotClient, HubSpotError


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def run_sync() -> dict:
    conn = load.connect()
    load.init_db(conn)
    started = _now()
    detail = []

    client = HubSpotClient()
    mapping = config.stage_mapping()
    account_cfg = config.account_config()

    accounts = extract.extract_companies(client, account_cfg)
    load.upsert_many(conn, "accounts", accounts)
    strategic = sum(a["is_strategic"] for a in accounts)
    detail.append(f"{len(accounts)} accounts ({strategic} strategic)")

    contacts, events = extract.extract_contacts(client, mapping)
    load.upsert_many(conn, "contacts", contacts)
    load.insert_stage_events(conn, events)
    detail.append(f"{len(contacts)} contacts, {len(events)} stage events")

    deals, deal_contacts = extract.extract_deals(client)
    load.upsert_many(conn, "deals", deals)
    load.upsert_many(conn, "deal_contacts", deal_contacts)
    detail.append(f"{len(deals)} deals")

    try:
        campaigns, cc, assets, asset_stats = extract.extract_campaigns_and_assets(client)
        load.upsert_many(conn, "campaigns", campaigns)
        load.upsert_many(conn, "contact_campaigns", cc)
        load.upsert_many(conn, "assets", assets)
        load.upsert_many(conn, "asset_stats", asset_stats)
        detail.append(f"{len(campaigns)} campaigns, {len(assets)} assets")
    except (HubSpotError, Exception) as e:  # noqa: BLE001 - campaigns are optional
        detail.append(f"campaigns/assets skipped: {e}")

    summary = "; ".join(detail)
    load.upsert_many(conn, "sync_runs", [{
        "started_at": started, "finished_at": _now(), "status": "live", "detail": summary,
    }])
    conn.close()
    return {"summary": summary}


def main():
    try:
        result = run_sync()
    except HubSpotError as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Sync complete: {result['summary']}")

    if "--report" in sys.argv:
        import funnel
        import export
        conn = load.connect()
        lo, hi = funnel.date_bounds(conn)
        conn.close()
        f = funnel.Filters(start=lo.to_pydatetime(), end=hi.to_pydatetime())
        path = export.build_report(f, "cohort", "All campaigns")
        print(f"Report written: {path}")


if __name__ == "__main__":
    main()
