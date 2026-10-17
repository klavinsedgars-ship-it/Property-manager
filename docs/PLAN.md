# Property Manager — research, plan & structure

## 1. What exists on the market (and what we took)

| Tool | Best idea | Taken? |
|---|---|---|
| **Stessa** | Portfolio dashboard: value, equity, cash flow; per-property income/expense categories | ✅ Dashboard + categories |
| **Landlord Studio** | Snap a receipt when entering an expense; per-property P&L | ✅ Receipt upload on every entry → Documents |
| **Baselane** | Bookkeeping separated per property | ✅ Every entry belongs to one property |
| **DealCheck** | Cash-on-cash, cap rate, IRR, multi-year projection | ✅ Metrics + 15-year sell/hold table |
| **TenantCloud / Innago / RentRedi** | Tenant register, rent ledger, late-rent overview | ✅ Leases, rent roll, arrears on dashboard |
| **Arthur Online** | Reminders and workflows | ✅ Lightweight: lease-end & document-expiry reminders |
| **Mashvisor** | Market comparables and yield benchmarks | ✅ VZD registered deals + CSP index + yield benchmarks |
| **Rendin (EE)** | Digital handover act with photos | ✅ "Handover act" document category |
| Buildium, Rentec, Hemlane | Trust accounting, owner statements, vendor marketplaces | ❌ Bloat for a private landlord |

No Latvian landlord app has real traction — the local market runs on ss.lv / city24 + Excel. That's the gap this app fills.

**Deliberately left out:** multi-user roles, tenant portal / online payments, listing syndication,
tenant screening, US tax forms, bank feeds (can be added later via CSV import), AI chat.

## 2. Metrics (definitions used in the app)

| Metric | Formula |
|---|---|
| Cost basis (total invested) | purchase price + purchase costs + renovation/improvements (capex) |
| Own cash | cost basis − loan amount |
| NOI (12 m) | rent & other income − running costs (excl. loan, capex, income tax) |
| Gross yield | annual rent ÷ cost basis |
| Net yield | NOI ÷ cost basis |
| Cap rate | NOI ÷ current value |
| Cash-on-cash | (NOI − debt service) ÷ own cash |
| Equity | value − loan balance |
| Total return | cash flow to date + (value − selling costs − CGT − loan balance) − own cash |
| ROI | total return ÷ own cash |
| IRR | XIRR of dated equity cash flows incl. hypothetical sale today |
| Payback | own cash ÷ annual after-tax cash flow |
| Next-year ROE (sell/hold) | (Δ net sale proceeds + next year's cash flow) ÷ today's net proceeds |

**Sell/hold logic.** Every year the equity tied up in a property could be released by selling.
Holding is worth it while *next year's return on that equity* (rent cash flow + price growth − loan
interest − taxes) beats a hurdle rate (default 6% — what the money would earn elsewhere). As equity
grows and leverage falls, that return declines; the first year it crosses below the hurdle is the
suggested sale window. The advice also warns about the Latvian CGT exemption (60 months + declared
residence) so you don't sell weeks before it.

## 3. Latvia-specific rules built in (editable in Settings)

* Rental income tax **10% of gross** (private landlord registered with VID).
* Capital-gains tax **25.5%** (from 2025; 20% for older deals), on price − purchase − improvements −
  selling costs. **Exempt** if owned ≥ 60 months *and* declared residence ≥ 12 months.
* Land-book state duty **1.5%** (cap €50 000) — "Estimate" button on the purchase-costs field.
* NĪN reminder: Rīga charges 1.5% instead of 0.2–0.6% if nobody is declared in the flat on 1 January.
* Building series (602, 119, 467, Lithuanian project, new project…) — prices differ strongly by series.

## 4. Market data (real, official, no API keys)

| Data | Source | How it's used |
|---|---|---|
| House price index, quarterly | CSP table **PCI050c**, PxWeb API v2 `api.stat.gov.lv` (v1 fallback) | Index-based value estimate per property; default price-growth rate = 5-yr trend |
| Euribor 3M/6M/12M, monthly | ECB Data Portal SDMX (`FM.M.U2.EUR.RT.MM.EURIBOR6MD_.HSTA`) | Shown next to your loan rate |
| Registered transaction prices | **VZD NĪTIS open data on data.gov.lv** (CKAN API) | Comparables: median €/m² for similar-size flats in the same city/district, 24 months; €/m² trend per city |
| Yield / price benchmarks | Global Property Guide, Arco, Latio (static, with source) | Reference only |

The app discovers VZD files through the data.gov.lv CKAN API (`package_search`,
organization `valsts-zemes-dienests`), lets you import any CSV/ZIP with one click, and parses the
Latvian headers by keyword (darījuma datums, summa/cena, platība, adrese, kadastra numurs,
novads…). A manually downloaded CSV can be uploaded too. Everything is cached in SQLite.

## 5. Architecture

```
run.py                  entry point (python run.py → http://127.0.0.1:5000)
app/
  __init__.py           app factory, optional password (PM_PASSWORD)
  schema.sql            SQLite schema
  db.py                 connection, settings, migrations
  finance.py            all calculations — pure functions, unit-tested
  market.py             CSP / ECB / data.gov.lv connectors + comparables
  services.py           DB rows → metrics
  views.py              routes
  templates/            server-rendered Jinja pages
  static/               CSS, Chart.js (bundled, works offline)
scripts/seed_demo.py    demo data
tests/                  pytest
data/                   your database + uploaded documents (git-ignored — back this up)
```

**Why this stack:** one Python process, one SQLite file, no build step, no cloud account. Runs on a
laptop, a Raspberry Pi or a €4 VPS. Your financial data never leaves your machine.

### Data model

* **properties** — unit details, purchase, mortgage, sale, expected rent
* **transactions** — every income/expense; category decides the group (income / capex / opex / tax)
* **tenants**, **leases** — rent, deposit, dates; rent payments link to a lease → arrears tracking
* **documents** — files linked to property / tenant / transaction, optional expiry date
* **valuations** — manual / appraisal / comparables values over time
* **market_data**, **market_deals** — cached official data
* **settings** — tax rates and projection assumptions

## 6. Roadmap (suggested next steps)

1. **Bank CSV import** (Swedbank / SEB / Citadele statement exports) with auto-categorisation rules — biggest time saver.
2. **Recurring expenses** (apsaimniekošana, insurance, NĪN) auto-generated monthly.
3. **Meter readings & utility re-billing** per tenant.
4. **Annual tax report** — rental income per year for the VID declaration, CGT calculation sheet for a sale.
5. **Deal analyser** for a prospective purchase (same engine, no DB record).
6. **Rent benchmark** from listings (ss.lv has no API — would need manual entry or a scraper; skipped for now).
7. Euribor-linked loan rate (margin + latest 6M Euribor, reset every 6 months).
