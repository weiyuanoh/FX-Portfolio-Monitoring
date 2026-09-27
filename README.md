# FX Portfolio Monitoring Dashboard

This is a portfolio-management dashboard for a long/short EM-FX spot book. I built it around how I think one PM would actually track his book during the day: first understand the overall outcome, then identify what is driving it, then look at the risk behind it, and finally compare that risk with limits and explicit stresses.

The current implementation is intentionally focused on linear FX spot. It would be easy to add more products or more risk pages, but that would not be meaningful without the right position data, valuation logic, and market-risk factors behind them. Keeping the first version focused lets the P&L, historical simulation, and risk attribution stay transparent and internally consistent.

## How the dashboard flows

1. **Performance Monitor** starts with the PM-level picture: daily, MTD, YTD, and inception P&L, cumulative return, Sharpe, drawdown, and headline VaR.
2. **Trade Ledger** is where the PM can investigate what is behind that picture. The book can be expanded or collapsed, and live table columns can be dragged into **Group by** to view the same positions by Strategy, Underlying, Region, Trader, Type, or another categorical field added later.
3. **Historical VaR** uses the same flexible grouping, but applies it to VaR, Expected Shortfall, and Marginal VaR. This is intended to move from “how much risk do I have?” to “where is the risk coming from?”
4. **Limits & Scenarios** puts the risk in context. It compares VaR across recent and earlier market regimes, monitors peak-to-trough drawdown and daily loss, and shows a small set of transparent EM-FX scenarios.

The grouping flexibility is intentional. A PM may think about a book by strategy first, then by currency pair. A senior PM overseeing a pod of sub-PMs and analysts may first want to look by trader. The underlying valuation and risk methodology do not change when the view changes; only the angle used to investigate the book does.

## Run locally

```bash
uv sync --locked
uv run python src/main.py
```

Then open `http://127.0.0.1:8050`.

## Deploy on Render

The repository includes `render.yaml` for a public review deployment. Push the repository to GitHub, then in Render select **New → Blueprint**, connect the repository, and deploy the detected service. Render will provide an `onrender.com` URL that can be sent to the reviewer; do not send the local `127.0.0.1:8050` address.

The service uses Gunicorn in production and binds to the port supplied by Render. The free plan may take a short time to wake after inactivity.

## Project structure

- `src/main.py` — Dash application, layouts, callbacks, grouping interaction, and charts
- `src/analytics.py` — FX spot valuation, P&L history, VaR, Expected Shortfall, and risk attribution
- `src/market_data.py` — price retrieval and local caching
- `src/assets/` — dashboard styling and drag-to-group browser interaction
- `data/portfolio.csv` — input spot-trade file
- `data/limits.csv` — editable sample limits for the Limits & Scenarios view
- `data/cache/` — locally cached Yahoo Finance closes; excluded from Git
- `tests/` — calculation tests

## Methodology and assumptions

### FX spot valuation and P&L

Each trade is treated as a USD/local-currency spot position. A **Buy USD** trade is long USD and short a fixed local-currency amount at the entry spot; a **Sell USD** trade has the opposite exposure. USD P&L is calculated by fully revaluing that fixed local-currency leg at the current spot.

The same convention is used for daily, MTD, YTD, and inception P&L. In the Trade Ledger, **start NPV** is the prior-close mark, **current NPV** is the latest market-close mark, and their difference is the one-day P&L change.

Daily return is daily portfolio P&L divided by the stated $250m sample NAV. Sharpe uses the trailing 63 business days and is annualised. Drawdown is measured from the high-water mark of NAV plus cumulative P&L.

### Historical VaR and Expected Shortfall

Historical VaR holds today’s EOD positions and current spots fixed, then applies historical daily FX returns to those positions. It does not use historical positions or historical notionals. This makes the risk comparison about how today’s book would have behaved in different return environments.

For the 252-day view, 99% VaR follows the convention used in the assignment: it is the average loss on the second- and third-worst simulations. The 500-day limit-monitor VaR uses the fifth-worst simulated loss, which is the 99% empirical observation in a 500-day sample.

Component VaR is each position or group’s average P&L on the portfolio tail dates. It therefore reconciles to portfolio VaR. Marginal VaR increases a selected position or group by 1% in its current direction, reprices portfolio VaR, and reports the incremental effect per additional $1m gross notional.

Expected Shortfall is the average portfolio loss beyond the displayed VaR cutoff. Expected-Shortfall contributions use the same portfolio tail dates, so they reconcile to portfolio Expected Shortfall.

### Limits, PTT, and scenarios

The Limits & Scenarios view compares the current book over three separate 500-day return samples: EOD, Year −1, and Year −2. The positions stay fixed; only the return history changes. This is useful because a risk number can look very different depending on the market regime used to generate it.

PTT means peak-to-trough drawdown: the loss from a portfolio high-water mark to the following trough. Current PTT, maximum PTT, and one-day loss are compared with the editable thresholds in `data/limits.csv`.

Scenarios apply explicitly stated spot shocks to current EOD marks and fully reprice the open book. They are deterministic sensitivities, not market forecasts. The scenario table also identifies the largest loss driver so the PM can see what is behind the headline number.

## Data, limitations, and what I would improve next

The application requests daily closes from Yahoo Finance via `yfinance` and keeps a local cache for reliability. It requests roughly six years of history so the 252-day and 500-day EOD, Year −1, and Year −2 simulations can be calculated. In production, the current CSV and public market-data inputs should be replaced by integration to the firm's approved position, reference-data, and market-data databases. This is necessary for controlled data lineage, consistent identifiers, reliable EOD marks, and aggregation across the wider group or pod.

The portfolio is a sample EM-FX spot book. The position data, strategies, and limits are illustrative; the historical prices currently loaded are daily public market closes. The supplied limits and scenario shocks are not production limits and would require formal risk-governance approval.

The next expansion is not simply more tabs. Once the book includes FX forwards, OIS, equity, or other interest-rate products, the position model needs more product and strategy coverage. The data layer would then need clean integration with the firm’s position, reference-data, and market-data sources, as well as correct aggregation across PM, sub-PM, strategy, and product levels. The risk framework would need to evolve with the book: carry and curve risk for forwards, PV01/key-rate ladders for OIS and rates, and delta/gamma/vega plus volatility scenarios for options.

At a group or pod level for a senior PM, correlation should be introduced once reliable daily P&L or return histories are available by sub-PM and strategy. A group correlation and exposure view would help distinguish apparent diversification from genuinely independent risk, especially where several strategies are exposed to the same EM-FX factors.

For the same reason, a future time-to-maturity view should be added as meaningful tenor buckets rather than raw dates or raw days to maturity. Any new categorical field can be surfaced in the existing drag-to-group interaction, without changing the core valuation or risk logic.
