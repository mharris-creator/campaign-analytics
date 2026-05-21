"""Generate a realistic offline dataset so the dashboard runs without HubSpot.

Models the real shape of the funnel:
- Contacts roll up to accounts; some accounts are Strategic (Named Targets).
- Conversion funnel is MCL -> MQL -> SAL -> SQL -> Customer. MTL is a pre-funnel
  target/resting state (named targets nurtured before capture, or customers reset
  for resell). Net-new contacts may enter organically straight at MCL.
- Contacts can run the funnel multiple times (resell). We emit a realistic
  transition history per contact and run it through cycles.segment(), exactly like
  the live HubSpot path — so the sample exercises the same code.

Run:  python -m sample_data.generate
"""

import os
import random
from datetime import datetime, timedelta

import config
import cycles
from db import load

random.seed(42)
NOW = datetime(2026, 5, 20, 12, 0, 0)
WINDOW_START = NOW - timedelta(days=540)

CAMPAIGNS = [
    {"campaign_id": "cmp_ebook_itbuy", "name": "2026 IT Buying Decision Ebook", "type": "Content", "leads": 320, "mult": 1.1, "strat_bias": 0.20},
    {"campaign_id": "cmp_path_pipeline", "name": "Path to Pipeline Webinar", "type": "Webinar", "leads": 210, "mult": 1.0, "strat_bias": 0.25},
    {"campaign_id": "cmp_abm_panw", "name": "ABM - Palo Alto Networks", "type": "ABM", "leads": 120, "mult": 1.3, "strat_bias": 0.95},
    {"campaign_id": "cmp_geo_aeo", "name": "GEO/AEO Scorecard", "type": "Tool", "leads": 260, "mult": 0.9, "strat_bias": 0.12},
    {"campaign_id": "cmp_platformization", "name": "Platformization Reality Check", "type": "Report", "leads": 175, "mult": 0.95, "strat_bias": 0.30},
]

STRATEGIC_ACCOUNTS = ["Palo Alto Networks", "Cisco", "Dell Technologies", "IBM", "Oracle",
                      "SAP", "ServiceNow", "Snowflake", "Databricks", "NVIDIA",
                      "Microsoft", "Amazon Web Services", "Google Cloud", "Salesforce", "VMware"]
OTHER_ACCOUNTS = ["Acme Corp", "Globex", "Initech", "Umbrella", "Soylent", "Hooli", "Pied Piper",
                  "Cyberdyne", "Massive Dynamic", "Vandelay", "Wonka Industries", "Tyrell Corp",
                  "Stark Industries", "Wayne Enterprises", "Nakatomi", "Gekko & Co", "Bluth Company",
                  "Dunder Mifflin", "Prestige Worldwide", "Sterling Cooper", "Hanso Foundation",
                  "Oscorp", "Aperture", "Black Mesa", "Weyland-Yutani", "Combine", "Abstergo",
                  "Tyrell Subsidiaries", "Genco", "Wernham Hogg", "Los Pollos", "Vehement Capital",
                  "Frobozz", "Spacely Sprockets", "Cogswell Cogs"]

SOURCES = ["Organic Search", "Paid Social", "Email Marketing", "Direct Traffic", "Referrals", "Offline Sources"]
OWNERS = ["A. Rivera", "J. Chen", "M. Okafor", "S. Patel", "L. Novak"]
FIRST = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Sam", "Jamie", "Drew", "Quinn", "Avery", "Reese"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Garcia", "Miller", "Davis", "Martinez", "Lopez", "Wilson", "Lee", "Walker"]

DEAL_STAGES_NEW = [("Discovery", "open", 0.30), ("Proposal", "open", 0.22), ("Negotiation", "open", 0.13),
                   ("Closed Won", "won", 0.20), ("Closed Lost", "lost", 0.15)]
DEAL_STAGES_STRAT = [("Discovery", "open", 0.22), ("Proposal", "open", 0.22), ("Negotiation", "open", 0.16),
                     ("Closed Won", "won", 0.30), ("Closed Lost", "lost", 0.10)]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def rand_dt(start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=random.random() * (end - start).total_seconds())


def weighted_choice(options):
    r, cum = random.random(), 0.0
    for *value, w in options:
        cum += w
        if r <= cum:
            return value
    return list(options[-1][:-1])


def progress_from_mcl(mcl_dt, strategic, mult):
    out = [(mcl_dt, "MCL")]
    dt = mcl_dt
    steps = [("MQL", (1, 18), 0.52), ("SAL", (2, 22), 0.62), ("SQL", (4, 38), 0.55),
             ("Customer", (10, 60), 0.50 if strategic else 0.40)]
    for stage, (lo, hi), p in steps:
        if random.random() > min(0.95, p * mult):
            break
        dt = dt + timedelta(days=random.uniform(lo, hi))
        if dt > NOW:
            break
        out.append((dt, stage))
    return out, dt, out[-1][1]


def build_transitions(strategic, mult):
    txns, reached, end_dt = [], None, None
    t0 = rand_dt(WINDOW_START, NOW)
    start_as_target = random.random() < (0.65 if strategic else 0.35)

    if start_as_target:
        txns.append((t0, "MTL"))
        if random.random() < (0.70 if strategic else 0.50):
            mcl_dt = t0 + timedelta(days=random.uniform(3, 30))
            if mcl_dt <= NOW:
                prog, end_dt, reached = progress_from_mcl(mcl_dt, strategic, mult)
                txns += prog
    else:
        prog, end_dt, reached = progress_from_mcl(t0, strategic, mult)
        txns += prog

    resell_p = 0.50 if strategic else 0.30
    guard = 0
    while reached == "Customer" and random.random() < resell_p and guard < 2:
        guard += 1
        reset_dt = end_dt + timedelta(days=random.uniform(30, 150))
        if reset_dt > NOW:
            break
        txns.append((reset_dt, "MTL"))
        mcl_dt = reset_dt + timedelta(days=random.uniform(3, 30))
        if mcl_dt > NOW:
            break
        prog, end_dt, reached = progress_from_mcl(mcl_dt, strategic, mult)
        txns += prog
        resell_p *= 0.6
    return txns


def current_stage(events):
    if not events:
        return None
    maxc = max(e["cycle"] for e in events)
    funnel = [e["stage"] for e in events if e["cycle"] == maxc and e["stage"] in config.FUNNEL_STAGES]
    return max(funnel, key=config.FUNNEL_STAGES.index) if funnel else "MTL"


def wipe(conn):
    for t in ["asset_stats", "assets", "deal_contacts", "deals", "contact_campaigns",
              "contact_stage_events", "contacts", "accounts", "campaigns", "sync_runs"]:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def main():
    # Sample data is fully regenerable: start from a clean file so schema changes
    # (new columns/tables) always apply. (The live sync path never deletes the DB.)
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    conn = load.connect()
    load.init_db(conn)
    wipe(conn)

    # ---- accounts ----
    accounts = []
    acc_ids = {"strategic": [], "other": []}
    for i, nm in enumerate(STRATEGIC_ACCOUNTS):
        aid = f"acc_s{i:03d}"
        accounts.append({"account_id": aid, "name": nm, "domain": nm.lower().replace(" ", "") + ".com",
                         "is_strategic": 1, "tier": "Named Target", "owner": random.choice(OWNERS)})
        acc_ids["strategic"].append(aid)
    for i, nm in enumerate(OTHER_ACCOUNTS):
        aid = f"acc_o{i:03d}"
        accounts.append({"account_id": aid, "name": nm, "domain": nm.lower().replace(" ", "") + ".com",
                         "is_strategic": 0, "tier": random.choice(["Tier 2", "Tier 3"]), "owner": random.choice(OWNERS)})
        acc_ids["other"].append(aid)

    load.upsert_many(conn, "accounts", accounts)
    load.upsert_many(conn, "campaigns", [
        {"campaign_id": c["campaign_id"], "name": c["name"], "type": c["type"],
         "start_date": iso(WINDOW_START), "end_date": iso(NOW)} for c in CAMPAIGNS])

    contacts, stage_events, contact_campaigns = [], [], []
    deals, deal_contacts, assets, asset_stats = [], [], [], []
    cid = 0

    for camp in CAMPAIGNS:
        mcl_in_campaign = 0
        for _ in range(camp["leads"]):
            cid += 1
            contact_id = f"c{cid:05d}"
            strategic = random.random() < camp["strat_bias"]
            account_id = random.choice(acc_ids["strategic"] if strategic else acc_ids["other"])

            txns = build_transitions(strategic, camp["mult"])
            events = cycles.segment([(iso(dt), s) for dt, s in txns])
            for e in events:
                e["contact_id"] = contact_id
                e["source"] = "datestamp"
            stage_events.extend(events)
            mcl_in_campaign += sum(1 for e in events if e["stage"] == "MCL")

            contacts.append({
                "contact_id": contact_id, "email": f"{contact_id}@example.com",
                "first_name": random.choice(FIRST), "last_name": random.choice(LAST),
                "company": next(a["name"] for a in accounts if a["account_id"] == account_id),
                "account_id": account_id,
                "create_date": min((e["entered_at"] for e in events), default=iso(NOW)),
                "current_stage": current_stage(events),
                "original_source": random.choice(SOURCES), "owner": random.choice(OWNERS),
            })
            contact_campaigns.append({"contact_id": contact_id, "campaign_id": camp["campaign_id"], "attribution": "first_touch"})

            # one deal per cycle that reached SQL
            by_cycle = {}
            for e in events:
                by_cycle.setdefault(e["cycle"], {})[e["stage"]] = e["entered_at"]
            for cyc, stages in by_cycle.items():
                if "SQL" not in stages:
                    continue
                sql_dt = datetime.fromisoformat(stages["SQL"])
                stage, status = weighted_choice(DEAL_STAGES_STRAT if strategic else DEAL_STAGES_NEW)
                amount = round(random.uniform(40000, 280000) if strategic else random.uniform(12000, 120000), -2)
                create = sql_dt + timedelta(days=random.uniform(0, 6))
                close = None
                if status in ("won", "lost"):
                    close = min(create + timedelta(days=random.uniform(10, 70)), NOW)
                did = f"d{cid:05d}_{cyc}"
                deals.append({"deal_id": did, "name": f"{contacts[-1]['company']} - {camp['type']}",
                              "amount": amount, "pipeline": "Sales Pipeline", "stage": stage, "status": status,
                              "create_date": iso(create), "close_date": iso(close) if close else None,
                              "owner": random.choice(OWNERS)})
                deal_contacts.append({"deal_id": did, "contact_id": contact_id})

        _build_assets(camp, mcl_in_campaign, assets, asset_stats)

    load.upsert_many(conn, "contacts", contacts)
    load.insert_stage_events(conn, stage_events)
    load.upsert_many(conn, "contact_campaigns", contact_campaigns)
    load.upsert_many(conn, "deals", deals)
    load.upsert_many(conn, "deal_contacts", deal_contacts)
    load.upsert_many(conn, "assets", assets)
    load.upsert_many(conn, "asset_stats", asset_stats)
    load.upsert_many(conn, "sync_runs", [{"started_at": iso(NOW), "finished_at": iso(NOW),
                                          "status": "sample", "detail": "Synthetic sample data"}])

    cyc_count = len({(e["contact_id"], e["cycle"]) for e in stage_events})
    resell = len({(e["contact_id"], e["cycle"]) for e in stage_events if e["cycle_type"] == "resell"})
    print(f"Generated: {len(contacts)} contacts in {len(accounts)} accounts "
          f"({len(acc_ids['strategic'])} strategic), {cyc_count} funnel cycles ({resell} resell), "
          f"{len(deals)} deals, {len(assets)} assets.")
    print(f"DB written to {config.DB_PATH}")
    conn.close()


def _build_assets(camp, mcl_count, assets, asset_stats):
    now = iso(NOW)
    cslug = camp["campaign_id"]
    form_id = f"frm_{cslug}"
    assets.append({"asset_id": form_id, "campaign_id": cslug, "asset_type": "form",
                   "name": f"{camp['name']} - Gated Form", "published_at": iso(WINDOW_START)})
    submissions = max(mcl_count, 1)
    views = int(submissions / random.uniform(0.18, 0.34))
    for metric, val in {"views": views, "submissions": submissions,
                        "conversion_rate": round(submissions / views, 4)}.items():
        asset_stats.append({"asset_id": form_id, "metric": metric, "value": val, "as_of_date": now})

    lp_id = f"lp_{cslug}"
    assets.append({"asset_id": lp_id, "campaign_id": cslug, "asset_type": "landing_page",
                   "name": f"{camp['name']} - Landing Page", "published_at": iso(WINDOW_START)})
    lp_views = int(views * random.uniform(1.1, 1.6))
    for metric, val in {"views": lp_views, "submissions": submissions,
                        "conversion_rate": round(submissions / lp_views, 4),
                        "bounce_rate": round(random.uniform(0.25, 0.55), 4)}.items():
        asset_stats.append({"asset_id": lp_id, "metric": metric, "value": val, "as_of_date": now})

    for e in range(random.randint(2, 4)):
        eid = f"eml_{cslug}_{e}"
        assets.append({"asset_id": eid, "campaign_id": cslug, "asset_type": "email",
                       "name": f"{camp['name']} - Email {e + 1}", "published_at": iso(WINDOW_START)})
        sent = random.randint(2500, 12000)
        delivered = int(sent * random.uniform(0.95, 0.99))
        opens = int(delivered * random.uniform(0.28, 0.52))
        clicks = int(opens * random.uniform(0.06, 0.22))
        for metric, val in {"sent": sent, "delivered": delivered, "opens": opens, "clicks": clicks,
                            "open_rate": round(opens / delivered, 4), "click_rate": round(clicks / delivered, 4)}.items():
            asset_stats.append({"asset_id": eid, "metric": metric, "value": val, "as_of_date": now})

    for s in range(random.randint(2, 5)):
        sid = f"soc_{cslug}_{s}"
        assets.append({"asset_id": sid, "campaign_id": cslug, "asset_type": "social",
                       "name": f"{camp['name']} - Social {s + 1}", "published_at": iso(WINDOW_START)})
        impr = random.randint(3000, 40000)
        clk = int(impr * random.uniform(0.005, 0.03))
        for metric, val in {"impressions": impr, "clicks": clk,
                            "engagement_rate": round(random.uniform(0.01, 0.06), 4)}.items():
            asset_stats.append({"asset_id": sid, "metric": metric, "value": val, "as_of_date": now})


if __name__ == "__main__":
    main()
