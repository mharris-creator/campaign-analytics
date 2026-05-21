-- Campaign Analytics local store.
-- Design goals:
--  1) TRUE funnel: conversion comes from WHEN a contact entered each stage, never
--     from current lifecycle membership. History lives in contact_stage_events.
--  2) Multi-cycle: contacts can run the funnel more than once (resell). A
--     (contact, stage) pair recurs across cycles, so events carry a cycle number.
--  3) Account view: contacts roll up to accounts; accounts can be Strategic
--     (Named Targets) or not, for ABM-style breakdowns.

CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    name         TEXT,
    domain       TEXT,
    is_strategic INTEGER DEFAULT 0,   -- 1 = Strategic Account / Named Target
    tier         TEXT,
    owner        TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id      TEXT PRIMARY KEY,
    email           TEXT,
    first_name      TEXT,
    last_name       TEXT,
    company         TEXT,
    account_id      TEXT,             -- primary associated company
    create_date     TEXT,
    current_stage   TEXT,
    original_source TEXT,
    owner           TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- Funnel backbone. UNIQUE(contact_id, cycle, stage) keeps the FIRST entry of a
-- stage WITHIN a cycle. Across cycles, a stage can recur (resell).
CREATE TABLE IF NOT EXISTS contact_stage_events (
    contact_id  TEXT NOT NULL,
    cycle       INTEGER NOT NULL,     -- 1-based pass through the funnel
    cycle_type  TEXT,                 -- new | resell (resell = a prior cycle hit Customer)
    stage       TEXT NOT NULL,        -- MTL/MCL/MQL/SAL/SQL/Customer
    entered_at  TEXT NOT NULL,        -- ISO8601 UTC, first entry of this stage in this cycle
    source      TEXT,                 -- history | datestamp | snapshot
    PRIMARY KEY (contact_id, cycle, stage),
    FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    name        TEXT,
    type        TEXT,
    start_date  TEXT,
    end_date    TEXT
);

CREATE TABLE IF NOT EXISTS contact_campaigns (
    contact_id  TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    attribution TEXT NOT NULL,        -- first_touch | last_touch | influenced
    PRIMARY KEY (contact_id, campaign_id, attribution),
    FOREIGN KEY (contact_id) REFERENCES contacts(contact_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
);

CREATE TABLE IF NOT EXISTS deals (
    deal_id     TEXT PRIMARY KEY,
    name        TEXT,
    amount      REAL,
    pipeline    TEXT,
    stage       TEXT,                 -- opportunity stage label
    status      TEXT,                 -- open | won | lost
    create_date TEXT,
    close_date  TEXT,
    owner       TEXT
);

CREATE TABLE IF NOT EXISTS deal_contacts (
    deal_id    TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    PRIMARY KEY (deal_id, contact_id),
    FOREIGN KEY (deal_id) REFERENCES deals(deal_id),
    FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id     TEXT PRIMARY KEY,
    campaign_id  TEXT,
    asset_type   TEXT,                -- form | landing_page | email | social
    name         TEXT,
    published_at TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
);

CREATE TABLE IF NOT EXISTS asset_stats (
    asset_id   TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL,
    as_of_date TEXT NOT NULL,
    PRIMARY KEY (asset_id, metric, as_of_date),
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    status      TEXT,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_stage_events_stage_time ON contact_stage_events(stage, entered_at);
CREATE INDEX IF NOT EXISTS idx_stage_events_contact ON contact_stage_events(contact_id);
CREATE INDEX IF NOT EXISTS idx_contacts_account ON contacts(account_id);
CREATE INDEX IF NOT EXISTS idx_contact_campaigns_campaign ON contact_campaigns(campaign_id);
CREATE INDEX IF NOT EXISTS idx_deal_contacts_contact ON deal_contacts(contact_id);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_assets_campaign ON assets(campaign_id);
