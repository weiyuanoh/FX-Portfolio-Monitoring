"""Transparent FX spot P&L and historical-VaR calculations."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_PORTFOLIO_COLUMNS = {
    "trade_id",
    "trade_date",
    "currency_pair",
    "side",
    "notional_usd",
    "entry_price",
}


def load_portfolio(path: Path) -> pd.DataFrame:
    """Read and validate the sample spot-trade file."""
    trades = pd.read_csv(path, parse_dates=["trade_date"])
    missing = REQUIRED_PORTFOLIO_COLUMNS.difference(trades.columns)
    if missing:
        raise ValueError(f"Portfolio file is missing: {', '.join(sorted(missing))}")
    if trades["trade_id"].duplicated().any():
        raise ValueError("trade_id must be unique")
    valid_sides = {"Buy USD": 1.0, "Sell USD": -1.0}
    if not set(trades["side"]).issubset(valid_sides):
        raise ValueError("side must be either 'Buy USD' or 'Sell USD'")
    if (trades[["notional_usd", "entry_price"]] <= 0).any().any():
        raise ValueError("notional_usd and entry_price must be positive")
    trades = trades.copy()
    trades["sign"] = trades["side"].map(valid_sides)
    trades["signed_notional_usd"] = trades["notional_usd"] * trades["sign"]
    return trades.sort_values(["trade_date", "trade_id"]).reset_index(drop=True)


def spot_pnl(signed_notional: float, entry_spot: float, spot: float) -> float:
    """USD P&L of a USD/local-currency spot trade, fully repriced at `spot`.

    A Buy USD trade owns USD and has a fixed local-currency payable entered at
    `entry_spot`; its USD value is N - N*entry_spot/spot. Sell USD is the negative.
    """
    return signed_notional * (1.0 - entry_spot / spot)


def _spot_on_or_before(prices: pd.Series, timestamp: pd.Timestamp) -> float:
    available = prices.loc[prices.index <= timestamp]
    return float(available.iloc[-1] if not available.empty else prices.iloc[0])


def trade_metrics(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Calculate current, daily, MTD, YTD, and since-inception P&L per trade."""
    as_of = prices.index[-1]
    prior_month_end = (as_of.to_period("M").start_time - pd.Timedelta(days=1))
    prior_year_end = pd.Timestamp(as_of.year - 1, 12, 31)
    rows = []
    for trade in trades.itertuples(index=False):
        pair_prices = prices[trade.currency_pair]
        current_spot = float(pair_prices.iloc[-1])
        previous_spot = float(pair_prices.iloc[-2])
        mtd_spot = trade.entry_price if trade.trade_date > prior_month_end else _spot_on_or_before(pair_prices, prior_month_end)
        ytd_spot = trade.entry_price if trade.trade_date > prior_year_end else _spot_on_or_before(pair_prices, prior_year_end)
        current_pnl = spot_pnl(trade.signed_notional_usd, trade.entry_price, current_spot)
        rows.append(
            {
                **trade._asdict(),
                "as_of_spot": current_spot,
                "start_npv_usd": spot_pnl(trade.signed_notional_usd, trade.entry_price, previous_spot),
                "current_npv_usd": current_pnl,
                "daily_pnl_usd": current_pnl - spot_pnl(trade.signed_notional_usd, trade.entry_price, previous_spot),
                "mtd_pnl_usd": current_pnl - spot_pnl(trade.signed_notional_usd, trade.entry_price, mtd_spot),
                "ytd_pnl_usd": current_pnl - spot_pnl(trade.signed_notional_usd, trade.entry_price, ytd_spot),
                "inception_pnl_usd": current_pnl,
            }
        )
    return pd.DataFrame(rows)


def portfolio_history(trades: pd.DataFrame, prices: pd.DataFrame, nav_usd: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build trade P&L histories and a portfolio return/drawdown series."""
    trade_pnl = pd.DataFrame(index=prices.index)
    for trade in trades.itertuples(index=False):
        path = spot_pnl(trade.signed_notional_usd, trade.entry_price, prices[trade.currency_pair])
        path.loc[path.index < trade.trade_date] = 0.0
        trade_pnl[trade.trade_id] = path
    cumulative_pnl = trade_pnl.sum(axis=1)
    daily_pnl = cumulative_pnl.diff().fillna(0.0)
    equity = nav_usd + cumulative_pnl
    running_peak = equity.cummax()
    history = pd.DataFrame(
        {
            "daily_pnl_usd": daily_pnl,
            "cumulative_pnl_usd": cumulative_pnl,
            "daily_return": daily_pnl / nav_usd,
            "drawdown_usd": equity - running_peak,
            "drawdown_pct": equity / running_peak - 1.0,
        }
    )
    return trade_pnl, history


def var_from_pnl(simulated_pnl: pd.Series) -> tuple[float, pd.Index]:
    """99% historical VaR = mean loss on the second- and third-worst days."""
    if len(simulated_pnl) < 3:
        raise ValueError("At least three historical scenarios are required for VaR")
    ranked = simulated_pnl.nsmallest(3)
    tail = ranked.iloc[1:3]
    return float(-tail.mean()), tail.index


def limit_var_from_pnl(simulated_pnl: pd.Series, lookback_days: int) -> tuple[float, pd.Index]:
    """Return the VaR convention used by the Limits & Scenarios monitor.

    The 252-day measure follows the assignment-specific convention: average the
    second- and third-worst losses.  For a 500-observation 99% sample, the
    empirical percentile is the fifth-worst loss.  Keeping this explicit avoids
    silently applying the 252-day convention to the longer limit-monitor window.
    """
    if lookback_days == 252:
        return var_from_pnl(simulated_pnl)
    if lookback_days != 500:
        raise ValueError("Limit-monitor VaR supports 252-day and 500-day windows")
    if len(simulated_pnl) < 5:
        raise ValueError("At least five historical scenarios are required for 500-day VaR")
    fifth_worst = simulated_pnl.nsmallest(5).iloc[-1:]
    return float(-fifth_worst.iloc[0]), fifth_worst.index


def cvar_from_pnl(simulated_pnl: pd.Series, var_usd: float | None = None) -> tuple[float, pd.Index]:
    """Historical 99% Expected Shortfall: mean loss beyond the VaR cutoff."""
    threshold = var_usd if var_usd is not None else var_from_pnl(simulated_pnl)[0]
    tail = simulated_pnl.loc[simulated_pnl <= -threshold]
    if tail.empty:
        tail = simulated_pnl.nsmallest(1)
    return float(-tail.mean()), tail.index


def simulated_trade_pnl(
    trades: pd.DataFrame,
    prices: pd.DataFrame,
    lookback_days: int = 252,
    vintage: str = "eod",
) -> pd.DataFrame:
    """Replay a chosen return window against the fixed current EOD trade vector.

    ``year_1`` and ``year_2`` shift the return sample back one and two complete
    lookback windows respectively; the open positions and current spots do not
    change. This keeps the later limits comparison like-for-like.
    """
    if vintage not in {"eod", "year_1", "year_2"}:
        raise ValueError("vintage must be eod, year_1, or year_2")
    returns = prices.pct_change().dropna()
    offset = {"eod": 0, "year_1": lookback_days, "year_2": 2 * lookback_days}[vintage]
    stop = len(returns) - offset if offset else len(returns)
    start = stop - lookback_days
    if start < 0:
        raise ValueError(f"Need at least {(offset + lookback_days):,} daily returns for this simulation")
    sampled_returns = returns.iloc[start:stop]
    current_spots = prices.iloc[-1]
    scenarios = pd.DataFrame(index=sampled_returns.index)
    for trade in trades.itertuples(index=False):
        shocked_spot = current_spots[trade.currency_pair] * (1.0 + sampled_returns[trade.currency_pair])
        scenarios[trade.trade_id] = trade.signed_notional_usd * trade.entry_price * (
            1.0 / current_spots[trade.currency_pair] - 1.0 / shocked_spot
        )
    return scenarios


def historical_var(
    trades: pd.DataFrame,
    prices: pd.DataFrame,
    lookback_days: int = 252,
    vintage: str = "eod",
) -> tuple[pd.DataFrame, float, pd.Series]:
    """Return trade-level risk, portfolio VaR, and historical simulations.

    Each historical percentage move is applied to today's spot for every open
    trade; no historical notionals are used. Component VaR reconciles exactly
    to portfolio VaR because it uses the portfolio's two selected tail dates.
    Marginal VaR is a one-percent increase in each trade's own direction,
    reported as USD VaR per additional USD 1m gross notional.
    """
    scenarios = simulated_trade_pnl(trades, prices, lookback_days, vintage)
    trade_to_pair: dict[str, str] = {}
    trade_notional: dict[str, float] = {}
    for trade in trades.itertuples(index=False):
        trade_to_pair[trade.trade_id] = trade.currency_pair
        trade_notional[trade.trade_id] = trade.notional_usd

    portfolio_simulated_pnl = scenarios.sum(axis=1)
    portfolio_var, tail_dates = var_from_pnl(portfolio_simulated_pnl)
    pair_risk: dict[str, dict[str, float]] = {}
    for pair in sorted(set(trade_to_pair.values())):
        trade_ids = [trade_id for trade_id, trade_pair in trade_to_pair.items() if trade_pair == pair]
        pair_simulated_pnl = scenarios[trade_ids].sum(axis=1)
        pair_var, _ = var_from_pnl(pair_simulated_pnl)
        pair_bumped_var, _ = var_from_pnl(portfolio_simulated_pnl + 0.01 * pair_simulated_pnl)
        pair_bump_in_millions = 0.01 * sum(trade_notional[trade_id] for trade_id in trade_ids) / 1_000_000
        pair_risk[pair] = {
            "position_var_usd": pair_var,
            "component_var_usd": float(-pair_simulated_pnl.loc[tail_dates].mean()),
            "marginal_var_per_usd_m": (pair_bumped_var - portfolio_var) / pair_bump_in_millions,
        }

    rows = []
    for trade_id in scenarios.columns:
        standalone_var, _ = var_from_pnl(scenarios[trade_id])
        bumped_var, _ = var_from_pnl(portfolio_simulated_pnl + 0.01 * scenarios[trade_id])
        bump_in_millions = 0.01 * trade_notional[trade_id] / 1_000_000
        rows.append(
            {
                "trade_id": trade_id,
                "currency_pair": trade_to_pair[trade_id],
                "position_var_usd": standalone_var,
                "component_var_usd": float(-scenarios.loc[tail_dates, trade_id].mean()),
                "marginal_var_per_usd_m": (bumped_var - portfolio_var) / bump_in_millions,
                "pair_position_var_usd": pair_risk[trade_to_pair[trade_id]]["position_var_usd"],
                "pair_component_var_usd": pair_risk[trade_to_pair[trade_id]]["component_var_usd"],
                "pair_marginal_var_per_usd_m": pair_risk[trade_to_pair[trade_id]]["marginal_var_per_usd_m"],
            }
        )
    return pd.DataFrame(rows), portfolio_var, portfolio_simulated_pnl


def summary_by_pair(metrics: pd.DataFrame, risk: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the trade book into the position-level overview table."""
    grouped = metrics.groupby("currency_pair", as_index=False).agg(
        gross_notional_usd=("notional_usd", "sum"),
        net_notional_usd=("signed_notional_usd", "sum"),
        spot=("as_of_spot", "last"),
        daily_pnl_usd=("daily_pnl_usd", "sum"),
        mtd_pnl_usd=("mtd_pnl_usd", "sum"),
        ytd_pnl_usd=("ytd_pnl_usd", "sum"),
        inception_pnl_usd=("inception_pnl_usd", "sum"),
    )
    risk_by_pair = risk.groupby("currency_pair", as_index=False).agg(
        position_var_usd=("pair_position_var_usd", "first"),
        component_var_usd=("pair_component_var_usd", "first"),
        marginal_var_per_usd_m=("pair_marginal_var_per_usd_m", "first"),
    )
    result = grouped.merge(risk_by_pair, on="currency_pair", how="left")
    result["direction"] = np.where(result["net_notional_usd"] >= 0, "Net long USD", "Net short USD")
    return result.sort_values("inception_pnl_usd", ascending=False).reset_index(drop=True)


def rolling_var(daily_pnl: pd.Series, window: int = 60) -> pd.Series:
    """Rolling version of the dashboard's historical VaR convention."""
    def calculate(window_pnl: pd.Series) -> float:
        return var_from_pnl(window_pnl)[0]

    return daily_pnl.rolling(window=window, min_periods=window).apply(calculate, raw=False)
