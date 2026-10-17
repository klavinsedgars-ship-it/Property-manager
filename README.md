# Property Manager

A minimal, self-hosted CRM for private landlords, built for the **Latvian** market.

* **Properties.** Purchase price and costs, renovation and furnishing (all of it counts toward the cost basis), mortgage, sale.
* **Income and expenses.** Clear categories, with an optional receipt photo or PDF on each entry.
* **Tenants and leases.** Tracks rent, deposit and dates. Unpaid rent is detected automatically.
* **Documents.** Deeds, land book records, leases, invoices and insurance, with expiry reminders.
* **Statistics.**
  * Monthly and yearly cash flow.
  * Gross and net yield, cap rate and cash-on-cash.
  * ROI and IRR, equity, and payback period.
* **Sell/hold planner.** Shows the profit and IRR if you sell in each of the next 15 years, after selling costs, 25.5% capital gains tax and loan payoff. It recommends a sale window by comparing next year's return on your equity with a hurdle rate.
* **Real market data.**
  * CSP house price index.
  * ECB Euribor.
  * VZD registered transaction prices from **data.gov.lv**, used to find comparable deals.

See [docs/PLAN.md](docs/PLAN.md) for the market research, metric definitions and roadmap.

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python run.py                 # → http://127.0.0.1:5000
python scripts/seed_demo.py   # optional: demo data
```

Then open **Market data → Refresh** once. This loads the price index and Euribor and lists the VZD transaction files available on data.gov.lv. Click **Import** on the apartment ("telpu grupas") files for recent periods to enable comparables.

Configure it with these environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PM_DATA_DIR` | `./data` | Location of the database and uploaded files |
| `PM_PASSWORD` | – | Turns on a login prompt (HTTP basic auth). Use it when the app runs on a server. |
| `PM_HOST` / `PM_PORT` | `127.0.0.1` / `5000` | Address and port the app listens on |
| `PM_SECRET_KEY` | derived | Set a random value in production |
| `DATABASE_URL` | – | PostgreSQL URL (Neon). If set, used instead of SQLite |
| `BLOB_READ_WRITE_TOKEN` | – | Vercel Blob token. If set, documents are stored there (private) |

**Backup:** copy the `data/` folder.

### Vercel (Flask preset)

Vercel functions have no permanent disk, so the app uses **Neon Postgres** for data and a **private Vercel Blob store** for documents. Both are detected automatically from environment variables.

1. **New Project:** import the repo.
   - Root Directory: `./`
   - Application Preset: **Flask** (the entrypoint is `index.py`)
   - Leave the build settings at their defaults.
2. **Environment Variables:**
   - `PM_PASSWORD`: your login password. Strongly recommended, because the site is public.
   - `PM_SECRET_KEY`: any long random string.
3. Click **Deploy.** The first deploy shows a red "Storage not connected" banner. That is expected.
4. In the project, open **Storage → Create Database → Neon** and connect it to all environments. This sets `DATABASE_URL`.
5. Still under **Storage**, choose **Create → Blob**, set access to **Private**, and connect it. This sets `BLOB_READ_WRITE_TOKEN`.
6. Go to **Deployments → Redeploy**. The banner disappears, and the tables are created automatically on first start.

Notes:
- `vercel.json` pins the functions to Frankfurt (`fra1`). When creating the Neon database, choose **AWS eu-central-1 (Frankfurt)** too, so pages load fast.
- Uploads are limited to about 4.5 MB per file, which is a Vercel limit. Photos are shrunk in the browser automatically.
- For the large VZD transaction files, use the **Import** button (the server downloads them directly) rather than uploading the file.

**Deal analyzer (AI).** Add an environment variable `ANTHROPIC_API_KEY` (from console.anthropic.com) to enable two things: reading any listing page or pasted ad text (Facebook, city24…), and the written AI verdict. ss.lv links are parsed even without a key. Set `PM_AI_MODEL` to use a model other than `claude-opus-5-5`.

**Automatic market data.** `vercel.json` schedules `/cron/sync` every hour. Each run:
- refreshes the house price index, Euribor and the VZD dataset list once a day;
- imports one new or changed VZD sales file.

The last 5 years backfill within a few hours, and new monthly data arrives on its own after that. Optionally, set `CRON_SECRET` (any random string) to lock the endpoint down to Vercel's scheduler. Locally, use **Market data → Sync now**.

The same code runs locally on SQLite when `DATABASE_URL` is not set.

### Docker

```bash
docker build -t property-manager .
docker run -p 5000:5000 -v $PWD/data:/app/data -e PM_PASSWORD=change-me property-manager
```

## Tests

```bash
pip install pytest && python -m pytest
PM_TEST_PG=postgresql://user@localhost/postgres python -m pytest   # same suite on PostgreSQL
```
