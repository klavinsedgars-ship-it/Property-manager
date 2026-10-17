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
| `PM_SECRET_KEY` | dev key | Set a random value in production |

**Backup:** copy the `data/` folder.

### Docker

```bash
docker build -t property-manager .
docker run -p 5000:5000 -v $PWD/data:/app/data -e PM_PASSWORD=change-me property-manager
```

## Tests

```bash
pip install pytest && python -m pytest
```
