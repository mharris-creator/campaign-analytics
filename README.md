# Campaign Analytics

A Streamlit dashboard for HubSpot lead + opportunity pipeline reporting. Built for
BI-style slicing of campaign performance, a **true** point-in-time lead-progression
funnel, account-level (ABM) views, and exec-ready exports.

## The model

- **Conversion funnel: MCL → MQL → SAL → SQL → Customer.** MCL is the top, because
  net-new contacts/accounts can enter organically as an MCL without ever being a
  target.
- **MTL (Marketing Target Lead)** is a pre-funnel/resting state (a named target not
  yet engaged, or a contact reset to start a new cycle). It is reported as a
  **Target Pool** with an **MTL→MCL activation rate**, not as the funnel denominator.
- **Multi-cycle / resell.** A contact can run the funnel repeatedly. The unit of
  conversion is a **(contact, cycle)** pair. A new cycle starts each time a contact
  enters MCL; a cycle is **resell** once an earlier cycle reached Customer. Filter by
  New vs. Resell.
- **Accounts.** Contacts roll up to accounts, flagged **Strategic (Named Target)** or
  not. Every stage shows distinct accounts and a Strategic-vs-Other breakdown.

## Why it's a *true* funnel

A current-status report counts where contacts are *now* ("how many are MQL today").
That can't produce accurate conversion rates, because a contact that already moved
to SQL no longer counts as MQL — and it loses repeat passes entirely. This app
reconstructs **one timestamped row per stage entry per cycle** (`contact_stage_events`,
from HubSpot property *history*) and computes the funnel two ways:

- **Cohort** — fix the cycles that *entered the anchor stage (default MCL)* during the
  window, then measure how many of that same set ever reached each later stage.
  Clean end-to-end conversion for the intake cohort.
- **Period entries** — count cycles that *crossed each stage during the window*.
  Answers "how did we do this period".

Both are point-in-time. SQL dollars tie to the exact SQL (contact, cycle) set the
funnel produces — so resell revenue lands on the right cycle and revenue/funnel
never disagree.

## Quick start (sample data — no HubSpot needed)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m sample_data.generate      # writes data/campaign_analytics.db
.venv/bin/streamlit run app.py
```

Open the local URL Streamlit prints. Use the sidebar to slice by date range,
funnel mode, campaign(s), opportunity stage, and opportunity status. Click
**Generate exec PDF** to download a one-glance executive report.

## Connecting to live HubSpot

### 1. Create a private app token

In HubSpot: **Settings → Integrations → Private Apps → Create a private app**.
Name it (e.g. "Campaign Analytics"), then under **Scopes** add:

- `crm.objects.contacts.read`
- `crm.objects.deals.read`
- `crm.objects.companies.read` *(accounts / Strategic breakdown)*
- `crm.schemas.contacts.read`, `crm.schemas.deals.read`, `crm.schemas.companies.read`
- For campaign/asset analytics (Marketing Hub **Enterprise**):
  `marketing.campaigns.read`, `forms`, `content`, `business-intelligence`

Create the app, copy the **access token** (starts with `pat-`), then:

```bash
cp .env.example .env
# edit .env and paste:  HUBSPOT_TOKEN=pat-...
```

### 2. Discover your stage + account mapping

MTL/MCL/SAL aren't default HubSpot lifecycle stages, and the Strategic-account flag
is portal-specific, so we inspect your portal:

```bash
.venv/bin/python -m hubspot.discover
```

This prints your lifecycle stages, lead-status options, deal pipelines, and
candidate company properties for "Strategic / Named Target", then writes **proposed**
`stage_mapping.json` and `account_config.json`. **Open both and confirm/adjust** how
MTL/MCL/MQL/SAL/SQL/Customer map (each is a `{property, value}` on `lifecyclestage`
or `hs_lead_status`) and which company property/values mark a Strategic Account.
Extraction reads these properties' **history** and segments it into funnel cycles.

### 3. Sync and run

```bash
.venv/bin/python sync.py            # pulls HubSpot -> data/campaign_analytics.db
.venv/bin/streamlit run app.py
```

## Scheduling

`sync.py` is the scheduled entry point. Example cron (daily 6am, also writes a PDF):

```cron
0 6 * * * cd /Users/meghan/Desktop/campaign-analytics && .venv/bin/python sync.py --report >> sync.log 2>&1
```

## Project structure

```
config.py              # funnel definition, stage->HubSpot mapping, account config, paths
cycles.py              # segment property history into funnel cycles (MCL-bounded, MTL-aware)
db/schema.sql          # SQLite schema (contact_stage_events is the funnel backbone)
db/load.py             # connection + idempotent upserts
funnel.py              # cohort/period funnel, target pool, account split, SQL $, trend (shared)
app.py                 # Streamlit dashboard
export.py              # exec PDF (matplotlib + reportlab), reused by cron
hubspot/client.py      # REST client (auth, pagination, rate-limit retry)
hubspot/discover.py    # inspect portal + propose stage_mapping.json / account_config.json
hubspot/extract.py     # pull companies/contacts(history)/deals/campaigns into DB rows
sync.py                # orchestrate extract -> load (scheduled entry point)
sample_data/generate.py# realistic offline dataset (accounts, multi-cycle, resell)
```

## Known limitations / planned

- **Asset metrics are current snapshots**, so the date slicer subsets the funnel
  and revenue (fully time-aware) but not asset stats. Storing dated asset snapshots
  for asset time-series is the next enhancement.
- Campaign/asset extraction requires Marketing Hub Enterprise; the funnel core
  (contacts, stage events, deals) works on any tier with CRM scopes.
- Deal→campaign attribution flows through associated contacts; each deal is tied to
  the (contact, cycle) whose SQL precedes it, and is counted once.
- Accounts roll up via each contact's **primary** company association.
- SQLite is fine to ~hundreds of thousands of contacts; for larger portals point
  `CAMPAIGN_DB_PATH` at Postgres and the same SQL applies.
```
