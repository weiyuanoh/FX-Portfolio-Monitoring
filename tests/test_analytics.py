import pandas as pd
import pytest

from analytics import cvar_from_pnl, limit_var_from_pnl, spot_pnl, var_from_pnl


def test_buy_usd_gains_when_usd_strengthens() -> None:
    assert spot_pnl(1_000_000, 5.0, 5.5) == pytest.approx(90_909.09, abs=0.01)


def test_sell_usd_loses_when_usd_strengthens() -> None:
    assert spot_pnl(-1_000_000, 5.0, 5.5) == pytest.approx(-90_909.09, abs=0.01)


def test_var_uses_second_and_third_worst_days() -> None:
    pnl = pd.Series([-100.0, -80.0, -60.0, 20.0], index=pd.date_range("2026-01-01", periods=4))
    value, tail_dates = var_from_pnl(pnl)
    assert value == 70.0
    assert list(tail_dates) == list(pnl.index[1:3])


def test_expected_shortfall_averages_losses_beyond_the_var_cutoff() -> None:
    pnl = pd.Series([-100.0, -80.0, -60.0, 20.0])
    value, _ = cvar_from_pnl(pnl, var_usd=70.0)
    assert value == 90.0


def test_limit_monitor_uses_fifth_worst_loss_for_500_day_var() -> None:
    pnl = pd.Series([-500.0, -400.0, -300.0, -200.0, -100.0, -50.0])
    value, tail_dates = limit_var_from_pnl(pnl, lookback_days=500)
    assert value == 100.0
    assert list(tail_dates) == [4]
