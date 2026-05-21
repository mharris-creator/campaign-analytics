"""Campaign Analytics dashboard.

Run:  streamlit run app.py
"""

import os
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import funnel
from db import load

st.set_page_config(page_title="Campaign Analytics", layout="wide", initial_sidebar_state="expanded")

STAGE_COLORS = {"MCL": "#1f6feb", "MQL": "#2ea043", "SAL": "#d29922", "SQL": "#8957e5", "Customer": "#bf3989"}
MTL_COLOR = "#8c959f"


def fmt_money(v) -> str:
    if v is None or pd.isna(v):
        return "$0"
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:,.0f}"


def fmt_pct(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v*100:.1f}%"


HELP_TEXT = """
**What this is:** the journey people take from *"a company we'd love to sell to"* all
the way to *"paying customer"* — how many make it through each step, how long it takes,
how many dollars are in play, and which campaigns and accounts are driving it.

#### The stages, in plain English
Think of it as a set of doors someone walks through, in order. Each door is narrower
than the last — that's the "funnel."

| Stage | What it means |
|---|---|
| **MTL** — Marketing Target Lead | A company we *want* to reach, but who hasn't raised their hand yet. Our "aiming at" list. *(Shown separately as the **Target Pool** — not everyone starts here.)* |
| **MCL** — Marketing Captured Lead | They raised their hand (filled out a form, grabbed a download). **This is where the funnel really starts.** |
| **MQL** — Marketing Qualified Lead | They look like a good fit, so marketing says *"worth sales' time."* |
| **SAL** — Sales Accepted Lead | Sales agrees and picks it up. |
| **SQL** — Sales Qualified Lead | Sales confirms a real opportunity — **this is where a dollar value gets attached.** |
| **Customer** | They bought. |

The **conversion %** is simply: of the people at one door, what share made it to the next.

#### A few words you'll see
- **Cycle / Resell.** After someone becomes a Customer, we often sell them something
  new later — so they walk the whole path *again*. We call each trip a **cycle**. A
  **resell** cycle is a contact who was already a customer. (Filter: New vs. Resell.)
- **Person vs. Account.** A **person** is one contact; an **account** is their company.
  One company can have many people. **Strategic Accounts (Named Targets)** are the
  specific companies we deliberately chose to pursue — the chart splits these from
  everyone else so you can watch our priority accounts.
- **Cohort vs. Period** (top of the left panel):
  *Cohort* follows **the same group** that entered in your date range to see how far it
  got — best for *"of the leads we got in Q1, how many became customers?"* (true rates).
  *Period* just counts how many crossed each line during the window — best for *"how
  busy were we this month?"*
- **SQL Pipeline $** is the dollar value of the real opportunities tied to the leads in
  your current view — split into **Open** (still in play), **Won**, and **Lost**.

#### How to use it
1. **Filters on the left** slice everything by date, campaign, account type, cycle type,
   and deal stage/status. The whole page updates instantly.
2. **Hover** over any chart to see exact numbers.
3. **Generate exec PDF** (bottom of the left panel) downloads a one-page summary to
   email or drop into a deck.
"""


def render_guide():
    st.title("Guide — How to read this dashboard")
    st.caption("A plain-English walkthrough for executives. Switch back to the data with the **Page** selector in the left sidebar.")
    st.markdown(HELP_TEXT)


def db_mtime() -> float:
    return os.path.getmtime(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0.0


@st.cache_data(show_spinner=False)
def _meta(_mtime):
    conn = load.connect()
    out = (funnel.list_campaigns(conn), funnel.list_opp_stages(conn),
           *funnel.date_bounds(conn), funnel.last_sync(conn))
    conn.close()
    return out


def main():
    page = st.sidebar.radio("Page", ["Dashboard", "Guide"],
                            help="Guide explains the funnel and how to use this dashboard.")
    if page == "Guide":
        render_guide()
        return

    if not os.path.exists(config.DB_PATH):
        # On a fresh host (e.g. Streamlit Cloud) there is no DB yet — seed sample
        # data so the app is viewable immediately. Live data replaces this via sync.
        with st.spinner("First run — generating sample data…"):
            from sample_data.generate import main as generate_sample
            generate_sample()

    camps, opp_stages, lo, hi, sync = _meta(db_mtime())
    if sync and sync.get("status") == "sample":
        st.warning("Showing **sample data** — not connected to live HubSpot yet.")
    name_by_id = dict(zip(camps.campaign_id, camps.name))
    id_by_name = {v: k for k, v in name_by_id.items()}

    # ---------------- Sidebar ----------------
    st.sidebar.title("Filters")
    default_start = max(lo, hi - timedelta(days=180)) if pd.notna(lo) else hi - timedelta(days=180)
    dr = st.sidebar.date_input("Date range", value=(default_start.date(), hi.date()),
                               min_value=lo.date(), max_value=hi.date())
    start_d, end_d = dr if isinstance(dr, tuple) and len(dr) == 2 else (default_start.date(), hi.date())
    start = datetime.combine(start_d, datetime.min.time())
    end = datetime.combine(end_d, datetime.max.time())

    mode = st.sidebar.radio(
        "Funnel mode", ["cohort", "period"],
        format_func=lambda m: "Cohort (true end-to-end)" if m == "cohort" else "Period entries",
        help="Cohort: of cycles that entered the anchor stage in the window, how far they got. "
             "Period: how many cycles crossed each stage during the window.")
    anchor = config.FUNNEL_STAGES[0]
    if mode == "cohort":
        anchor = st.sidebar.selectbox("Cohort anchor (funnel top)", config.FUNNEL_STAGES, index=0,
                                      help="Default MCL — the point where leads enter the funnel.")

    chosen_campaigns = st.sidebar.multiselect("Campaigns (blank = all)", options=list(name_by_id.values()))
    campaign_ids = [id_by_name[n] for n in chosen_campaigns]

    seg_label = st.sidebar.radio("Accounts", ["All accounts", "Strategic (Named Targets)", "Other accounts"])
    account_segment = {"All accounts": "all", "Strategic (Named Targets)": "strategic", "Other accounts": "other"}[seg_label]

    cyc_label = st.sidebar.radio("Cycle type", ["All cycles", "New business", "Resell"],
                                 help="Resell = a cycle for a contact who reached Customer in an earlier cycle.")
    cycle_types = {"All cycles": list(config.CYCLE_TYPES), "New business": ["new"], "Resell": ["resell"]}[cyc_label]

    chosen_stages = st.sidebar.multiselect("Opportunity stage (blank = all)", options=opp_stages)
    chosen_status = st.sidebar.multiselect("Opportunity status (blank = all)", options=config.DEAL_STATUSES)
    freq = "W" if st.sidebar.radio("Trend granularity", ["Weekly", "Monthly"], horizontal=True) == "Weekly" else "M"

    f = funnel.Filters(start=start, end=end, campaigns=campaign_ids, opp_stages=chosen_stages,
                       opp_statuses=chosen_status, cycle_types=cycle_types, account_segment=account_segment)

    st.sidebar.divider()
    if sync:
        st.sidebar.caption(f"Data as of {sync.get('finished_at', '—')}  ·  source: {sync.get('status', '—')}")

    # ---------------- Compute ----------------
    conn = load.connect()
    fdf, cs, acs = funnel.compute_funnel(conn, f, mode, anchor)
    tp = funnel.target_pool(conn, f)
    deals = funnel.sql_deals(conn, f, cs["SQL"])
    trend = funnel.funnel_trend(conn, f, freq)
    acct_bd = funnel.account_breakdown(conn, f, cs)
    assets = funnel.asset_performance(conn, f)
    conn.close()

    counts = dict(zip(fdf.stage, fdf["count"]))
    pipeline_total = deals.amount.sum() if not deals.empty else 0.0
    won_total = deals.loc[deals.status == "won", "amount"].sum() if not deals.empty else 0.0
    conv_sql = fdf.loc[fdf.stage == "SQL", "overall_conversion"]
    conv_cust = fdf.loc[fdf.stage == "Customer", "overall_conversion"]

    # ---------------- Header ----------------
    scope = "All campaigns" if not chosen_campaigns else ", ".join(chosen_campaigns)
    st.title("Campaign Analytics")
    st.caption(f"{scope}  ·  {seg_label}  ·  {cyc_label}  ·  {start_d:%b %d, %Y} – {end_d:%b %d, %Y}  ·  "
               f"{'Cohort @ ' + anchor if mode == 'cohort' else 'Period-entry'} funnel")
    st.caption("New here? Open the **Guide** page (Page selector, top of the left sidebar) for a plain-English walkthrough.")

    # ---------------- Target pool + funnel KPIs ----------------
    k = st.columns(6)
    k[0].metric("Target Pool (MTL)", f"{tp['targets']:,}",
                delta=f"{fmt_pct(tp['activation_rate'])} → MCL", delta_color="off")
    for i, s in enumerate(config.FUNNEL_STAGES, start=1):
        k[i].metric(s, f"{counts.get(s, 0):,}")

    k2 = st.columns(5)
    k2[0].metric("SQL Pipeline $", fmt_money(pipeline_total))
    k2[1].metric("Won $", fmt_money(won_total))
    k2[2].metric("MCL → SQL", fmt_pct(conv_sql.iloc[0] if not conv_sql.empty else None))
    k2[3].metric("MCL → Customer", fmt_pct(conv_cust.iloc[0] if not conv_cust.empty else None))
    k2[4].metric("Accounts in funnel", f"{len(acs.get('MCL', set())):,}")

    st.divider()

    # ---------------- Funnel + conversion ----------------
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Lead progression funnel")
        fig = go.Figure(go.Funnel(
            y=fdf.label, x=fdf["count"], textinfo="value+percent initial",
            marker={"color": [STAGE_COLORS.get(s, "#999") for s in fdf.stage]},
            connector={"line": {"color": "#d0d7de"}}))
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=360)
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader("Conversion")
        show = fdf[["label", "count", "accounts", "step_conversion", "overall_conversion"]].copy()
        show.columns = ["Stage", "Cycles", "Accounts", "Step %", "Overall %"]
        show["Step %"] = show["Step %"].map(fmt_pct)
        show["Overall %"] = show["Overall %"].map(fmt_pct)
        st.dataframe(show, hide_index=True, use_container_width=True)
        st.caption("Cycles = (contact × funnel pass). Step % vs. previous stage; Overall % vs. anchor.")

    # ---------------- Accounts through the funnel ----------------
    st.subheader("Accounts through the funnel — Strategic vs. Other")
    if acct_bd[["strategic", "other"]].to_numpy().sum() == 0:
        st.info("No account activity for these filters.")
    else:
        abfig = go.Figure()
        abfig.add_trace(go.Bar(x=acct_bd.stage, y=acct_bd.strategic, name="Strategic (Named Targets)", marker_color="#1f6feb"))
        abfig.add_trace(go.Bar(x=acct_bd.stage, y=acct_bd.other, name="Other accounts", marker_color="#8c959f"))
        abfig.update_layout(barmode="group", margin=dict(l=10, r=10, t=10, b=10), height=300,
                            legend={"orientation": "h", "y": 1.15}, yaxis_title="Distinct accounts")
        st.plotly_chart(abfig, use_container_width=True)
        st.caption("Distinct accounts that reached each stage, split by Strategic Account (Named Target) status.")

    # ---------------- Progress over time ----------------
    st.subheader("Progress over time")
    if trend.empty:
        st.info("No stage activity in this window.")
    else:
        tfig = go.Figure()
        for s in config.FUNNEL_STAGES:
            if s in trend.columns:
                tfig.add_trace(go.Scatter(x=trend.index, y=trend[s], mode="lines+markers", name=s,
                                          line={"color": STAGE_COLORS.get(s, "#999"), "width": 2}))
        tfig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=320,
                           legend={"orientation": "h", "y": 1.12}, yaxis_title="Stage entries (cycles)")
        st.plotly_chart(tfig, use_container_width=True)
        st.caption(f"Cycles first entering each stage per {('week' if freq == 'W' else 'month')} — true throughput.")

    st.divider()

    # ---------------- SQL $ ----------------
    st.subheader("SQL pipeline ($)")
    m = st.columns(4)
    open_total = deals.loc[deals.status == "open", "amount"].sum() if not deals.empty else 0.0
    lost_total = deals.loc[deals.status == "lost", "amount"].sum() if not deals.empty else 0.0
    m[0].metric("Total SQL $", fmt_money(pipeline_total))
    m[1].metric("Open $", fmt_money(open_total))
    m[2].metric("Won $", fmt_money(won_total))
    m[3].metric("Lost $", fmt_money(lost_total))
    if not deals.empty:
        c1, c2 = st.columns(2)
        by_stage = deals.groupby("stage").amount.sum().sort_values()
        c1.plotly_chart(go.Figure(go.Bar(x=by_stage.values, y=by_stage.index, orientation="h", marker_color="#1f6feb"))
                        .update_layout(margin=dict(l=10, r=10, t=30, b=10), height=280, title="$ by opportunity stage"),
                        use_container_width=True)
        by_status = deals.groupby("status").amount.sum()
        c2.plotly_chart(go.Figure(go.Bar(x=by_status.index, y=by_status.values,
                                         marker_color=["#2ea043", "#cf222e", "#8957e5"][:len(by_status)]))
                        .update_layout(margin=dict(l=10, r=10, t=30, b=10), height=280, title="$ by status"),
                        use_container_width=True)
        with st.expander(f"Deal detail ({len(deals)} deals)"):
            dshow = deals[["name", "amount", "stage", "status", "create_date", "close_date", "owner"]].copy()
            dshow["amount"] = dshow["amount"].map(lambda v: f"${v:,.0f}")
            st.dataframe(dshow, hide_index=True, use_container_width=True)
    else:
        st.info("No SQL-linked deals match the current filters.")

    st.divider()

    # ---------------- Assets ----------------
    st.subheader("Asset performance")
    if assets is None or assets.empty:
        st.info("No assets for the selected campaign(s).")
    else:
        render_assets(assets)

    # ---------------- Export ----------------
    st.sidebar.divider()
    if st.sidebar.button("Generate exec PDF", use_container_width=True):
        import export
        with st.spinner("Building report…"):
            path = export.build_report(f, mode, scope, anchor)
        with open(path, "rb") as fh:
            st.sidebar.download_button("Download report", fh, file_name=os.path.basename(path),
                                       mime="application/pdf", use_container_width=True)
        st.sidebar.success("Report ready.")


ASSET_COLS = {
    "form": ["name", "views", "submissions", "conversion_rate"],
    "landing_page": ["name", "views", "submissions", "conversion_rate", "bounce_rate"],
    "email": ["name", "sent", "delivered", "opens", "clicks", "open_rate", "click_rate"],
    "social": ["name", "impressions", "clicks", "engagement_rate"],
}
ASSET_LABELS = {"form": "Forms", "landing_page": "Landing Pages", "email": "Emails", "social": "Social Posts"}
RATE_COLS = {"conversion_rate", "bounce_rate", "open_rate", "click_rate", "engagement_rate"}


def render_assets(assets: pd.DataFrame):
    present = [t for t in ASSET_COLS if t in set(assets.asset_type)]
    for tab, atype in zip(st.tabs([ASSET_LABELS[t] for t in present]), present):
        with tab:
            cols = [c for c in ASSET_COLS[atype] if c in assets.columns]
            sub = assets[assets.asset_type == atype][cols].copy()
            for c in cols:
                if c in RATE_COLS:
                    sub[c] = sub[c].map(fmt_pct)
                elif c != "name":
                    sub[c] = sub[c].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
            sub.columns = [c.replace("_", " ").title() for c in cols]
            st.dataframe(sub, hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
