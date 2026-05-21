"""Funnel + pipeline analytics. Shared data layer for the dashboard and exports.

Units & rules:
- The conversion funnel is MCL -> MQL -> SAL -> SQL -> Customer. The unit of
  conversion is a (contact, cycle) pair, because a contact can run the funnel more
  than once (resell). Cohort anchors at MCL by default.
- MTL is reported separately as a Target Pool with an MTL->MCL activation rate.
- Two modes, both point-in-time (never current status):
    cohort: of cycles that entered the anchor stage in the window, how many ever
            reached each later stage. Clean end-to-end conversion.
    period: how many cycles crossed each stage during the window.
- Accounts: contacts roll up to accounts; account-level counts are distinct
  accounts touching a stage, and a Strategic (Named Target) vs Other split.
- SQL $ ties each deal to the (contact, cycle) whose SQL preceded the deal, so
  resell revenue lands on the right cycle and the funnel/revenue never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

import config


@dataclass
class Filters:
    start: datetime
    end: datetime
    campaigns: list = field(default_factory=list)
    opp_stages: list = field(default_factory=list)
    opp_statuses: list = field(default_factory=list)
    cycle_types: list = field(default_factory=lambda: list(config.CYCLE_TYPES))
    account_segment: str = "all"            # all | strategic | other
    attribution: tuple = ("first_touch", "last_touch", "influenced")


def _in(values):
    return "(" + ",".join("?" * len(values)) + ")"


def allowed_contacts(conn, f: Filters) -> set:
    if f.campaigns:
        sql = (f"SELECT DISTINCT contact_id FROM contact_campaigns "
               f"WHERE campaign_id IN {_in(f.campaigns)} AND attribution IN {_in(f.attribution)}")
        base = set(pd.read_sql_query(sql, conn, params=[*f.campaigns, *f.attribution]).contact_id)
    else:
        base = set(pd.read_sql_query("SELECT contact_id FROM contacts", conn).contact_id)
    if f.account_segment in ("strategic", "other"):
        want = 1 if f.account_segment == "strategic" else 0
        seg = pd.read_sql_query(
            "SELECT c.contact_id FROM contacts c JOIN accounts a ON c.account_id = a.account_id "
            "WHERE a.is_strategic = ?", conn, params=[want])
        base &= set(seg.contact_id)
    return base


def contact_account_map(conn) -> dict:
    df = pd.read_sql_query(
        "SELECT c.contact_id, c.account_id, COALESCE(a.is_strategic,0) AS is_strategic "
        "FROM contacts c LEFT JOIN accounts a ON c.account_id = a.account_id", conn)
    return {r.contact_id: (r.account_id, int(r.is_strategic)) for r in df.itertuples()}


def _events(conn, f: Filters) -> pd.DataFrame:
    se = pd.read_sql_query(
        "SELECT contact_id, cycle, cycle_type, stage, entered_at FROM contact_stage_events",
        conn, parse_dates=["entered_at"])
    se = se[se.contact_id.isin(allowed_contacts(conn, f))]
    se = se[se.cycle_type.isin(f.cycle_types)]
    se = se.copy()
    se["key"] = se.contact_id + ":" + se.cycle.astype(str)
    return se


def _accounts_for(keys, camap) -> set:
    out = set()
    for k in keys:
        cid = k.split(":")[0]
        acc = camap.get(cid)
        if acc and acc[0]:
            out.add(acc[0])
    return out


def compute_funnel(conn, f: Filters, mode: str = "cohort", anchor: str | None = None):
    """Return (funnel_df, contact_sets, account_sets).

    funnel_df: stage, label, count (cycles), accounts, step_conversion, overall_conversion.
    contact_sets[stage] = set of 'contact:cycle' keys; account_sets[stage] = set of account_ids.
    """
    anchor = anchor or config.FUNNEL_STAGES[0]
    ai = config.FUNNEL_STAGES.index(anchor)
    se = _events(conn, f)
    camap = contact_account_map(conn)
    start, end = pd.Timestamp(f.start), pd.Timestamp(f.end)

    contact_sets = {}
    if mode == "period":
        for s in config.FUNNEL_STAGES:
            ss = se[(se.stage == s) & (se.entered_at >= start) & (se.entered_at <= end)]
            contact_sets[s] = set(ss.key)
        report_stages = config.FUNNEL_STAGES
        top = config.FUNNEL_STAGES[0]
    else:  # cohort
        cohort = set(se[(se.stage == anchor) & (se.entered_at >= start) & (se.entered_at <= end)].key)
        for s in config.FUNNEL_STAGES:
            if config.FUNNEL_STAGES.index(s) < ai:
                contact_sets[s] = set()
            else:
                contact_sets[s] = set(se[(se.stage == s) & (se.key.isin(cohort))].key)
        report_stages = config.FUNNEL_STAGES[ai:]
        top = anchor

    account_sets = {s: _accounts_for(contact_sets[s], camap) for s in config.FUNNEL_STAGES}
    top_count = len(contact_sets[top])

    rows, prev = [], None
    for s in report_stages:
        c = len(contact_sets[s])
        rows.append({
            "stage": s, "label": config.FUNNEL_LABELS[s], "count": c,
            "accounts": len(account_sets[s]),
            "step_conversion": (c / prev) if prev else None,
            "overall_conversion": (c / top_count) if top_count else None,
        })
        prev = c
    return pd.DataFrame(rows), contact_sets, account_sets


def target_pool(conn, f: Filters) -> dict:
    """MTL target pool + MTL->MCL activation within the window (and filters)."""
    se = _events(conn, f)
    camap = contact_account_map(conn)
    start, end = pd.Timestamp(f.start), pd.Timestamp(f.end)
    targets = set(se[(se.stage == "MTL") & (se.entered_at >= start) & (se.entered_at <= end)].key)
    activated = set(se[(se.stage == "MCL") & (se.key.isin(targets))].key)
    return {
        "targets": len(targets),
        "activated": len(activated),
        "activation_rate": (len(activated) / len(targets)) if targets else None,
        "target_accounts": len(_accounts_for(targets, camap)),
    }


def account_breakdown(conn, f: Filters, contact_sets: dict) -> pd.DataFrame:
    """Per-stage distinct accounts split Strategic (Named Target) vs Other."""
    camap = contact_account_map(conn)
    rows = []
    for s in config.FUNNEL_STAGES:
        strat, other = set(), set()
        for key in contact_sets.get(s, ()):
            acc = camap.get(key.split(":")[0])
            if not acc or not acc[0]:
                continue
            (strat if acc[1] == 1 else other).add(acc[0])
        rows.append({"stage": s, "label": config.FUNNEL_LABELS[s],
                     "strategic": len(strat), "other": len(other)})
    return pd.DataFrame(rows)


def _attribute_deals(conn, f: Filters) -> pd.DataFrame:
    """One row per deal with the (contact, cycle) whose SQL best precedes it."""
    deals = pd.read_sql_query("SELECT * FROM deals", conn)
    if deals.empty:
        return deals.assign(key=pd.Series(dtype=str))
    dc = pd.read_sql_query("SELECT deal_id, contact_id FROM deal_contacts", conn)
    se = _events(conn, f)
    sqlc = se[se.stage == "SQL"][["contact_id", "cycle", "entered_at"]]
    by_contact = {}
    for r in sqlc.itertuples():
        by_contact.setdefault(r.contact_id, []).append((r.entered_at, int(r.cycle)))
    for v in by_contact.values():
        v.sort()

    deals = deals.copy()
    deals["create_dt"] = pd.to_datetime(deals["create_date"], errors="coerce")
    create_by_deal = dict(zip(deals.deal_id, deals.create_dt))
    contacts_by_deal = dc.groupby("deal_id").contact_id.apply(list).to_dict()

    chosen = {}  # deal_id -> (contact_id, cycle)
    for did, contacts in contacts_by_deal.items():
        create_dt = create_by_deal.get(did)
        best = None
        for cid in contacts:
            for ts, cyc in by_contact.get(cid, []):
                if create_dt is not None and ts <= create_dt and (best is None or ts > best[0]):
                    best = (ts, cid, cyc)
        if best is None:  # deal created before any SQL -> earliest SQL among its contacts
            cands = [(ts, cid, cyc) for cid in contacts for ts, cyc in by_contact.get(cid, [])]
            if cands:
                best = min(cands)
        if best:
            chosen[did] = (best[1], best[2])

    deals["contact_id"] = deals.deal_id.map(lambda d: chosen[d][0] if d in chosen else None)
    deals["cycle"] = deals.deal_id.map(lambda d: chosen[d][1] if d in chosen else None)
    deals = deals.dropna(subset=["contact_id"])
    deals["key"] = deals.contact_id + ":" + deals.cycle.astype(int).astype(str)
    return deals


def sql_deals(conn, f: Filters, sql_keys: set) -> pd.DataFrame:
    """Deals tied to the funnel's SQL (contact, cycle) set, filtered by opp stage/status."""
    cols = ["deal_id", "name", "amount", "pipeline", "stage", "status", "create_date", "close_date", "owner", "key"]
    if not sql_keys:
        return pd.DataFrame(columns=cols)
    deals = _attribute_deals(conn, f)
    if deals.empty:
        return pd.DataFrame(columns=cols)
    deals = deals[deals.key.isin(sql_keys)]
    if f.opp_stages:
        deals = deals[deals.stage.isin(f.opp_stages)]
    if f.opp_statuses:
        deals = deals[deals.status.isin(f.opp_statuses)]
    return deals


def funnel_trend(conn, f: Filters, freq: str = "W") -> pd.DataFrame:
    """Cycle stage-entries bucketed over time (the 'progress over time' view)."""
    se = _events(conn, f)
    start, end = pd.Timestamp(f.start), pd.Timestamp(f.end)
    se = se[(se.entered_at >= start) & (se.entered_at <= end) & (se.stage.isin(config.FUNNEL_STAGES))]
    if se.empty:
        return pd.DataFrame(columns=config.FUNNEL_STAGES)
    se = se.copy()
    se["bucket"] = se.entered_at.dt.to_period(freq).dt.start_time
    pivot = (se.groupby(["bucket", "stage"]).key.nunique()
             .unstack(fill_value=0)
             .reindex(columns=config.FUNNEL_STAGES, fill_value=0)
             .sort_index())
    return pivot


def asset_performance(conn, f: Filters) -> pd.DataFrame:
    assets = pd.read_sql_query("SELECT * FROM assets", conn)
    if f.campaigns:
        assets = assets[assets.campaign_id.isin(f.campaigns)]
    if assets.empty:
        return assets
    stats = pd.read_sql_query("SELECT asset_id, metric, value FROM asset_stats", conn)
    stats = stats[stats.asset_id.isin(set(assets.asset_id))]
    wide = stats.pivot_table(index="asset_id", columns="metric", values="value", aggfunc="last").reset_index()
    return assets.merge(wide, on="asset_id", how="left")


# ---- slicer option helpers ----

def list_campaigns(conn) -> pd.DataFrame:
    return pd.read_sql_query("SELECT campaign_id, name, type FROM campaigns ORDER BY name", conn)


def list_opp_stages(conn) -> list:
    df = pd.read_sql_query("SELECT DISTINCT stage FROM deals WHERE stage IS NOT NULL ORDER BY stage", conn)
    return df.stage.tolist()


def list_opp_statuses(conn) -> list:
    return config.DEAL_STATUSES


def date_bounds(conn):
    df = pd.read_sql_query(
        "SELECT MIN(entered_at) AS lo, MAX(entered_at) AS hi FROM contact_stage_events", conn,
        parse_dates=["lo", "hi"])
    return df.lo.iloc[0], df.hi.iloc[0]


def last_sync(conn):
    df = pd.read_sql_query("SELECT finished_at, status, detail FROM sync_runs ORDER BY id DESC LIMIT 1", conn)
    return None if df.empty else df.iloc[0].to_dict()
