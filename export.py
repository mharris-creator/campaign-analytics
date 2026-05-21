"""Render the current filtered view to an executive PDF.

Used by the dashboard's "Generate exec PDF" button and by scheduled runs
(sync.py --report / cron). Pulls from funnel.py so the PDF matches the dashboard.

CLI:  python export.py            # full range, cohort @ MCL, all campaigns
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

import config  # noqa: E402
import funnel  # noqa: E402
from db import load  # noqa: E402

STAGE_COLORS = {"MCL": "#1f6feb", "MQL": "#2ea043", "SAL": "#d29922", "SQL": "#8957e5", "Customer": "#bf3989"}


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


def _funnel_png(fdf: pd.DataFrame) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6.2, 3.0))
    counts, stages = fdf["count"].tolist(), fdf["stage"].tolist()
    maxc = max(counts) or 1
    for i, (c, s) in enumerate(zip(counts, stages)):
        w = c / maxc
        ax.barh(i, w, left=(1 - w) / 2, color=STAGE_COLORS.get(s, "#999"), height=0.62)
        ax.text(0.5, i - 0.34, f"{c:,}", ha="center", va="center", color="#24292f", fontsize=9, fontweight="bold")
    ax.set_ylim(-0.6, len(counts) - 0.4)
    ax.set_xlim(0, 1)
    ax.set_yticks(range(len(counts)))
    ax.set_yticklabels(stages, fontsize=9, fontweight="bold")
    ax.invert_yaxis()
    ax.set_xticks([])
    for sp in ("top", "right", "bottom", "left"):
        ax.spines[sp].set_visible(False)
    return _save(fig)


def _account_png(acct_bd: pd.DataFrame) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    x = range(len(acct_bd))
    ax.bar([i - 0.2 for i in x], acct_bd.strategic, width=0.4, label="Strategic (Named Targets)", color="#1f6feb")
    ax.bar([i + 0.2 for i in x], acct_bd.other, width=0.4, label="Other accounts", color="#8c959f")
    ax.set_xticks(list(x))
    ax.set_xticklabels(acct_bd.stage, fontsize=9)
    ax.set_ylabel("Distinct accounts")
    ax.legend(fontsize=8, frameon=False, ncol=2, loc="upper right")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return _save(fig)


def _trend_png(trend: pd.DataFrame) -> io.BytesIO | None:
    if trend.empty:
        return None
    fig, ax = plt.subplots(figsize=(6.6, 2.6))
    for s in config.FUNNEL_STAGES:
        if s in trend.columns:
            ax.plot(trend.index, trend[s], marker="o", markersize=3, label=s, color=STAGE_COLORS.get(s, "#999"))
    ax.set_ylabel("Stage entries")
    ax.legend(ncol=5, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.2))
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.autofmt_xdate(rotation=30)
    return _save(fig)


def _save(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _kpi_table(headers, values, header_bg="#1f6feb"):
    t = Table([headers, values], colWidths=[(6.9 / len(headers)) * inch] * len(headers))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 8), ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, 1), 5), ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.white)]))
    return t


def build_report(f: funnel.Filters, mode: str, scope: str = "All campaigns", anchor: str | None = None) -> str:
    anchor = anchor or config.FUNNEL_STAGES[0]
    conn = load.connect()
    fdf, cs, acs = funnel.compute_funnel(conn, f, mode, anchor)
    tp = funnel.target_pool(conn, f)
    deals = funnel.sql_deals(conn, f, cs["SQL"])
    trend = funnel.funnel_trend(conn, f, "M")
    acct_bd = funnel.account_breakdown(conn, f, cs)
    sync = funnel.last_sync(conn)
    conn.close()

    counts = dict(zip(fdf.stage, fdf["count"]))
    pipeline_total = deals.amount.sum() if not deals.empty else 0.0
    won_total = deals.loc[deals.status == "won", "amount"].sum() if not deals.empty else 0.0
    conv_sql = fdf.loc[fdf.stage == "SQL", "overall_conversion"]
    conv_cust = fdf.loc[fdf.stage == "Customer", "overall_conversion"]

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = str(config.REPORTS_DIR / f"campaign-report-{datetime.now():%Y%m%d-%H%M%S}.pdf")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#57606a"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)

    doc = SimpleDocTemplate(out_path, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.55 * inch)
    seg = {"all": "All accounts", "strategic": "Strategic (Named Targets)", "other": "Other accounts"}[f.account_segment]
    cyc = "All cycles" if len(f.cycle_types) == 2 else f.cycle_types[0].title()
    mode_label = f"Cohort @ {anchor}" if mode == "cohort" else "Period-entry"

    story = [Paragraph("Campaign Analytics — Executive Summary", h1),
             Paragraph(f"{scope} &nbsp;·&nbsp; {seg} &nbsp;·&nbsp; {cyc} &nbsp;·&nbsp; "
                       f"{f.start:%b %d, %Y} – {f.end:%b %d, %Y} &nbsp;·&nbsp; {mode_label} funnel", sub)]
    if sync:
        story.append(Paragraph(f"Data as of {sync.get('finished_at', '—')} (source: {sync.get('status', '—')})", sub))
    story.append(Spacer(1, 8))

    story.append(_kpi_table(
        ["Target Pool", "MCL", "MQL", "SAL", "SQL", "Customer"],
        [f"{tp['targets']:,}"] + [f"{counts.get(s, 0):,}" for s in config.FUNNEL_STAGES]))
    story.append(Spacer(1, 4))
    story.append(_kpi_table(
        ["SQL Pipeline", "Won $", "MCL→SQL", "MCL→Customer", "Activation"],
        [fmt_money(pipeline_total), fmt_money(won_total),
         fmt_pct(conv_sql.iloc[0] if not conv_sql.empty else None),
         fmt_pct(conv_cust.iloc[0] if not conv_cust.empty else None),
         fmt_pct(tp["activation_rate"])], header_bg="#2da44e"))

    story.append(Paragraph("Lead Progression Funnel (MCL → Customer)", h2))
    story.append(Image(_funnel_png(fdf), width=6.2 * inch, height=3.0 * inch))

    conv = [["Stage", "Cycles", "Accounts", "Step %", "Overall %"]]
    for _, r in fdf.iterrows():
        conv.append([r["label"], f"{int(r['count']):,}", f"{int(r['accounts']):,}",
                     fmt_pct(r["step_conversion"]), fmt_pct(r["overall_conversion"])])
    ct = Table(conv, colWidths=[2.4 * inch, 1.0 * inch, 1.1 * inch, 1.0 * inch, 1.1 * inch])
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaeef2")), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")])]))
    story.append(ct)

    story.append(Paragraph("Accounts Through the Funnel — Strategic vs. Other", h2))
    story.append(Image(_account_png(acct_bd), width=6.4 * inch, height=2.6 * inch))

    tbuf = _trend_png(trend)
    if tbuf:
        story.append(Paragraph("Progress Over Time (stage entries per month)", h2))
        story.append(Image(tbuf, width=6.6 * inch, height=2.6 * inch))

    doc.build(story)
    return out_path


if __name__ == "__main__":
    conn = load.connect()
    lo, hi = funnel.date_bounds(conn)
    conn.close()
    f = funnel.Filters(start=lo.to_pydatetime() if lo is not None else datetime.now() - timedelta(days=365),
                       end=hi.to_pydatetime() if hi is not None else datetime.now())
    print(f"Wrote {build_report(f, 'cohort', 'All campaigns')}")
