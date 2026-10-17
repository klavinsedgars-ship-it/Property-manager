-- Property Manager schema (SQLite). All money in EUR, dates ISO yyyy-mm-dd.

CREATE TABLE IF NOT EXISTS properties (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    address          TEXT,
    city             TEXT DEFAULT 'Rīga',
    district         TEXT,
    kind             TEXT DEFAULT 'apartment',     -- apartment | house | commercial | land | parking
    building_series  TEXT,                         -- LV series: pre-war, 103, 119, 467, 602, lit-project, new-project ...
    area_m2          REAL,
    rooms            INTEGER,
    floor            TEXT,
    cadastre_no      TEXT,
    status           TEXT DEFAULT 'renovating',    -- renovating | rented | vacant | own_use | sold
    expected_rent    REAL,                         -- market rent estimate, used before a lease exists
    purchase_date    TEXT,
    purchase_price   REAL DEFAULT 0,
    purchase_costs   REAL DEFAULT 0,               -- notary, 2% land-book duty, agent, valuation...
    declared_residence INTEGER DEFAULT 0,          -- 1 if owner declared residence here (CGT exemption)
    -- mortgage (optional)
    loan_amount      REAL DEFAULT 0,
    loan_rate        REAL DEFAULT 0,               -- total annual % (margin + Euribor) used for projections
    loan_term_months INTEGER DEFAULT 0,
    loan_start       TEXT,
    -- sale
    sale_date        TEXT,
    sale_price       REAL,
    sale_costs       REAL DEFAULT 0,
    notes            TEXT,
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tenants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT,
    email       TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id   INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    start_date    TEXT NOT NULL,
    end_date      TEXT,                            -- NULL = open-ended
    rent          REAL NOT NULL DEFAULT 0,         -- monthly
    deposit       REAL DEFAULT 0,
    payment_day   INTEGER DEFAULT 1,
    notes         TEXT
);

-- Every money movement. Positive amounts; direction given by kind.
CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    lease_id     INTEGER REFERENCES leases(id) ON DELETE SET NULL,
    date         TEXT NOT NULL,
    kind         TEXT NOT NULL,                    -- income | expense
    category     TEXT NOT NULL,
    amount       REAL NOT NULL,
    description  TEXT,
    vendor       TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_tx_prop_date ON transactions(property_id, date);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER REFERENCES properties(id) ON DELETE CASCADE,
    tenant_id    INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    category     TEXT DEFAULT 'other',
    filename     TEXT NOT NULL,                    -- stored name on disk
    original_name TEXT,
    expires_on   TEXT,                             -- e.g. insurance / lease end, shown as reminder
    uploaded_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Point-in-time valuations (manual, appraisal, listing comparison).
CREATE TABLE IF NOT EXISTS valuations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    value       REAL NOT NULL,
    source      TEXT DEFAULT 'manual',
    notes       TEXT
);

-- Cached market series: (series, period) -> value. period 'YYYY-Qn' or 'YYYY-MM'.
CREATE TABLE IF NOT EXISTS market_data (
    series      TEXT NOT NULL,
    period      TEXT NOT NULL,
    value       REAL NOT NULL,
    fetched_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (series, period)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Individual market transactions (VZD NĪTIS open data from data.gov.lv, or user-imported CSV).
CREATE TABLE IF NOT EXISTS market_deals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT,
    deal_date   TEXT,
    price       REAL,
    area_m2     REAL,
    price_m2    REAL,
    territory   TEXT,       -- municipality / city as in source
    address     TEXT,
    cadastre_no TEXT,
    rooms       INTEGER,
    floor       TEXT,
    kind        TEXT,
    UNIQUE (source, deal_date, cadastre_no, price, area_m2)
);
CREATE INDEX IF NOT EXISTS ix_deals_terr ON market_deals(territory, deal_date);
