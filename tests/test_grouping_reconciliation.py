from __future__ import annotations

from pathlib import Path

import pandas as pd

from analytics import cvar_from_pnl, load_portfolio, var_from_pnl

PORTFOLIO_PATH = Path(__file__).parents[1] / "data" / "portfolio.csv"
GROUPING_FIELDS = ["trader", "strategy", "region", "currency_pair", "product_type", "side"]
MEASURES = ["notional_usd", "signed_notional_usd"]


def assert_hierarchy_reconciles(frame: pd.DataFrame, remaining_fields: list[str], visited: set[tuple[tuple[str, ...], str]]) -> None:
    """Every selectable grouping must split a parent into disjoint trade subsets."""
    parent_totals = frame[MEASURES].sum()
    for field in remaining_fields:
        key = (tuple(sorted(frame["trade_id"])), field)
        if key in visited:
            continue
        visited.add(key)
        children = list(frame.groupby(field, dropna=False, sort=True))
        child_totals = pd.concat([subset[MEASURES] for _, subset in children]).sum()
        pd.testing.assert_series_equal(child_totals, parent_totals, check_names=False)
        next_fields = [candidate for candidate in remaining_fields if candidate != field]
        for _, subset in children:
            assert_hierarchy_reconciles(subset, next_fields, visited)


def test_sample_book_has_multiple_strategies_per_currency_pair() -> None:
    trades = load_portfolio(PORTFOLIO_PATH)
    strategies_per_pair = trades.groupby("currency_pair")["strategy"].nunique()
    assert (strategies_per_pair >= 4).all()


def test_every_grouping_and_nested_hierarchy_reconciles_to_the_book() -> None:
    trades = load_portfolio(PORTFOLIO_PATH)
    book_totals = trades[MEASURES].sum()

    for field in GROUPING_FIELDS:
        grouped_totals = trades.groupby(field, dropna=False)[MEASURES].sum().sum()
        pd.testing.assert_series_equal(grouped_totals, book_totals, check_names=False)

    assert_hierarchy_reconciles(trades, GROUPING_FIELDS, set())


def test_grouped_pnl_and_tail_contributions_reconcile_for_every_field() -> None:
    """A grouping is a partition of trades, so it cannot duplicate scenario P&L."""
    trades = load_portfolio(PORTFOLIO_PATH)
    scenarios = pd.DataFrame(
        {
            trade_id: [index * -120.0, index * 80.0, index * -45.0, index * 25.0]
            for index, trade_id in enumerate(trades["trade_id"], start=1)
        },
        index=pd.date_range("2026-01-01", periods=4),
    )
    portfolio_pnl = scenarios.sum(axis=1)
    portfolio_var, var_tail_dates = var_from_pnl(portfolio_pnl)
    portfolio_es, es_tail_dates = cvar_from_pnl(portfolio_pnl, portfolio_var)

    for field in GROUPING_FIELDS:
        group_pnl = [scenarios[group["trade_id"].tolist()].sum(axis=1) for _, group in trades.groupby(field, dropna=False)]
        assert sum(group_pnl).equals(portfolio_pnl)
        assert sum(float(-pnl.loc[var_tail_dates].mean()) for pnl in group_pnl) == portfolio_var
        assert sum(float(-pnl.loc[es_tail_dates].mean()) for pnl in group_pnl) == portfolio_es
