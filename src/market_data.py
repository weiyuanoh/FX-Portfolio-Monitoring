"""Market-data retrieval and deterministic offline fallback for the dashboard."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Yahoo Finance uses these symbols for USD against the local currency.
YAHOO_SYMBOLS = {
    "USDBRL": "BRL=X",
    "USDMXN": "MXN=X",
    "USDZAR": "ZAR=X",
    "USDTRY": "TRY=X",
    "USDINR": "INR=X",
    "USDPLN": "PLN=X",
}

# Used only when the public feed or cached prices are unavailable.
REFERENCE_SPOTS = {
    "USDBRL": 5.32,
    "USDMXN": 18.70,
    "USDZAR": 18.25,
    "USDTRY": 41.20,
    "USDINR": 87.85,
    "USDPLN": 3.70,
}


def _cache_path(cache_dir: Path, pair: str) -> Path:
    return cache_dir / f"{pair}.csv"


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _read_cache(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, parse_dates=["date"])
        series = pd.Series(frame["close"].to_numpy(), index=frame["date"], name=path.stem)
        return pd.to_numeric(series, errors="coerce").dropna()
    except (OSError, KeyError, ValueError):
        return None


def _write_cache(path: Path, series: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": series.index, "close": series.values}).to_csv(path, index=False)


def _download_history(symbol: str, start: date) -> pd.Series | None:
    """Download one Yahoo Finance close series; return None on an expected feed failure."""
    try:
        frame = yf.download(
            symbol,
            start=start,
            end=_today_utc() + timedelta(days=1),
            interval="1d",
            auto_adjust=False,
            progress=False,
            timeout=10,
            multi_level_index=False,
        )
        if frame.empty or "Close" not in frame:
            return None
        close = frame["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index).tz_localize(None)
        return pd.to_numeric(close, errors="coerce").dropna()
    except Exception:  # noqa: BLE001 - any provider failure should select the safe fallback
        # A dashboard must still open when a public market-data provider is down.
        return None


def _fallback_prices(pairs: list[str], periods: int) -> pd.DataFrame:
    """Create a reproducible, correlated fallback history ending at reference spots."""
    rng = np.random.default_rng(20260926)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=periods)
    market_factor = rng.standard_t(df=6, size=periods) * 0.005
    regional_factor = {
        "LatAm": rng.standard_t(df=7, size=periods) * 0.004,
        "EMEA": rng.standard_t(df=7, size=periods) * 0.0045,
        "Asia": rng.standard_t(df=7, size=periods) * 0.0025,
    }
    region = {"USDBRL": "LatAm", "USDMXN": "LatAm", "USDZAR": "EMEA", "USDTRY": "EMEA", "USDINR": "Asia", "USDPLN": "EMEA"}
    volatility = {"USDBRL": 0.009, "USDMXN": 0.007, "USDZAR": 0.010, "USDTRY": 0.013, "USDINR": 0.004, "USDPLN": 0.006}
    prices = pd.DataFrame(index=dates)
    for pair in pairs:
        idiosyncratic = rng.standard_t(df=7, size=periods) * volatility[pair] * 0.68
        log_returns = market_factor * 0.35 + regional_factor[region[pair]] + idiosyncratic
        path = np.exp(np.cumsum(log_returns) - np.cumsum(log_returns)[-1])
        prices[pair] = REFERENCE_SPOTS[pair] * path
    return prices


def load_prices(pairs: list[str], data_dir: Path, history_days: int = 1_600) -> tuple[pd.DataFrame, str]:
    """Load price history, preferring live Yahoo data, then cache, then a marked fallback.

    Public FX data can have brief outages. The source string is surfaced in the UI so
    nobody mistakes a cache or fallback series for a live valuation.
    """
    unsupported = set(pairs).difference(YAHOO_SYMBOLS)
    if unsupported:
        raise ValueError(f"No market-data mapping for: {', '.join(sorted(unsupported))}")

    cache_dir = data_dir / "cache"
    start = _today_utc() - timedelta(days=int(history_days * 1.7))
    series_by_pair: dict[str, pd.Series] = {}
    live_count = 0
    cache_count = 0
    skip_live = os.getenv("FXPM_SKIP_LIVE_PRICES", "").lower() in {"1", "true", "yes"}

    for pair in pairs:
        history = None if skip_live else _download_history(YAHOO_SYMBOLS[pair], start)
        if history is not None and len(history) >= 252:
            _write_cache(_cache_path(cache_dir, pair), history)
            series_by_pair[pair] = history
            live_count += 1
            continue
        cached = _read_cache(_cache_path(cache_dir, pair))
        if cached is not None and len(cached) >= history_days:
            series_by_pair[pair] = cached
            cache_count += 1

    if len(series_by_pair) != len(pairs):
        return _fallback_prices(pairs, history_days), "Deterministic fallback (market data unavailable)"

    prices = pd.concat([series_by_pair[pair].rename(pair) for pair in pairs], axis=1).sort_index().ffill().dropna()
    prices = prices.tail(history_days)
    if live_count == len(pairs):
        return prices, "Yahoo Finance · latest available close"
    return prices, "Cached Yahoo Finance closes"
