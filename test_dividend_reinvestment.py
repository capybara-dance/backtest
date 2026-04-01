"""Tests for dividend reinvestment logic in backtest_logic.py."""

import pandas as pd
import pytest

from backtest_logic import (
    build_dividend_df,
    run_lump_sum,
    run_dca,
    run_backtest,
)


def _make_price_df(prices_dict):
    """Build a price DataFrame from a dict of {symbol: [price, ...]} lists."""
    dates = pd.date_range("2020-01-01", periods=len(next(iter(prices_dict.values()))), freq="B")
    return pd.DataFrame(prices_dict, index=dates)


def _make_dividend_df(dividends_dict, index):
    """Build a dividend DataFrame aligned to the given index."""
    return pd.DataFrame(dividends_dict, index=index).fillna(0.0)


# ---------------------------------------------------------------------------
# build_dividend_df
# ---------------------------------------------------------------------------

def test_build_dividend_df_extracts_dividends():
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    df_a = pd.DataFrame(
        {"Close": [100, 101, 102, 103, 104], "Dividends": [0, 0, 1.0, 0, 0]},
        index=dates,
    )
    ticker_data = {"A": {"df": df_a, "info": {}}}
    div_df = build_dividend_df(ticker_data, dates[0].date(), dates[-1].date())
    assert div_df["A"].iloc[2] == 1.0
    assert div_df["A"].iloc[0] == 0.0


def test_build_dividend_df_missing_column():
    """Symbols without a Dividends column should be treated as zero dividends."""
    dates = pd.date_range("2020-01-01", periods=3, freq="B")
    df_a = pd.DataFrame({"Close": [100, 101, 102]}, index=dates)
    ticker_data = {"A": {"df": df_a, "info": {}}}
    div_df = build_dividend_df(ticker_data, dates[0].date(), dates[-1].date())
    assert (div_df["A"] == 0.0).all()


# ---------------------------------------------------------------------------
# run_lump_sum with dividend reinvestment
# ---------------------------------------------------------------------------

def test_lump_sum_dividend_reinvest_increases_value():
    """When dividends are paid and reinvested, portfolio value should be higher."""
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    prices = [100.0, 100.0, 100.0, 100.0, 100.0]
    price_df = _make_price_df({"A": prices})
    # Dividend of 5 on day 2
    dividends = [0, 0, 5.0, 0, 0]
    div_df = _make_dividend_df({"A": dividends}, price_df.index)
    weights = {"A": 1.0}

    portfolio_with, _ = run_lump_sum(
        price_df, 1000, weights, False, None,
        dividend_df=div_df, reinvest_dividends=True,
    )
    portfolio_without, _ = run_lump_sum(
        price_df, 1000, weights, False, None,
        dividend_df=div_df, reinvest_dividends=False,
    )

    # After dividend reinvestment the portfolio should be worth more
    assert portfolio_with.iloc[-1] > portfolio_without.iloc[-1]


def test_lump_sum_dividend_reinvest_calculates_exact_shares():
    """Verify the exact share count after reinvesting a known dividend."""
    dates = pd.date_range("2020-01-01", periods=3, freq="B")
    price_df = _make_price_df({"A": [100.0, 100.0, 100.0]})
    # Day 1: dividend of 10 per share
    div_df = _make_dividend_df({"A": [0, 10.0, 0]}, price_df.index)
    weights = {"A": 1.0}

    portfolio, _ = run_lump_sum(
        price_df, 1000, weights, False, None,
        dividend_df=div_df, reinvest_dividends=True,
    )

    # Initial shares: 1000 / 100 = 10
    # After dividend on day 1: 10 * 10 / 100 = 1 extra share → 11 shares
    # Day 2 portfolio value: 11 * 100 = 1100
    assert portfolio.iloc[-1] == pytest.approx(1100.0)


def test_lump_sum_no_dividend_df_unchanged():
    """Passing dividend_df=None should leave the portfolio value unchanged."""
    dates = pd.date_range("2020-01-01", periods=3, freq="B")
    price_df = _make_price_df({"A": [100.0, 110.0, 120.0]})
    weights = {"A": 1.0}

    portfolio_none, _ = run_lump_sum(
        price_df, 1000, weights, False, None,
        dividend_df=None, reinvest_dividends=True,
    )
    portfolio_base, _ = run_lump_sum(
        price_df, 1000, weights, False, None,
    )
    pd.testing.assert_series_equal(portfolio_none, portfolio_base)


# ---------------------------------------------------------------------------
# run_dca with dividend reinvestment
# ---------------------------------------------------------------------------

def test_dca_dividend_reinvest_increases_value():
    """DCA: dividends reinvested should result in a higher final value."""
    dates = pd.date_range("2020-01-01", periods=10, freq="B")
    prices = [100.0] * 10
    price_df = _make_price_df({"A": prices})
    dividends = [0, 0, 0, 5.0, 0, 0, 0, 0, 0, 0]
    div_df = _make_dividend_df({"A": dividends}, price_df.index)
    weights = {"A": 1.0}

    portfolio_with, _ = run_dca(
        price_df, 1000, weights, False, None,
        dividend_df=div_df, reinvest_dividends=True,
    )
    portfolio_without, _ = run_dca(
        price_df, 1000, weights, False, None,
        dividend_df=div_df, reinvest_dividends=False,
    )

    assert portfolio_with.iloc[-1] > portfolio_without.iloc[-1]


# ---------------------------------------------------------------------------
# run_backtest (integration)
# ---------------------------------------------------------------------------

def test_run_backtest_passes_reinvest_option():
    """run_backtest correctly delegates reinvest_dividends to run_lump_sum."""
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    price_df = _make_price_df({"A": [100.0, 100.0, 100.0, 100.0, 100.0]})
    div_df = _make_dividend_df({"A": [0, 0, 10.0, 0, 0]}, price_df.index)
    weights = {"A": 1.0}

    p_true, _ = run_backtest(
        price_df, "거치식", 1000, 0, weights, False, None,
        dividend_df=div_df, reinvest_dividends=True,
    )
    p_false, _ = run_backtest(
        price_df, "거치식", 1000, 0, weights, False, None,
        dividend_df=div_df, reinvest_dividends=False,
    )

    assert p_true.iloc[-1] > p_false.iloc[-1]
