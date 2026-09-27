"""Dash application entry point for the FX portfolio monitoring dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dash_table, dcc, html
from plotly.subplots import make_subplots

from analytics import (
    cvar_from_pnl,
    historical_var,
    limit_var_from_pnl,
    load_portfolio,
    portfolio_history,
    rolling_var,
    simulated_trade_pnl,
    spot_pnl,
    summary_by_pair,
    trade_metrics,
    var_from_pnl,
)
from market_data import load_prices

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
PORTFOLIO_PATH = DATA_DIR / "portfolio.csv"
LIMITS_PATH = DATA_DIR / "limits.csv"
SAMPLE_NAV_USD = 250_000_000


def money(value: float, decimals: int = 2) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value) / 1_000_000:,.{decimals}f}m"


def number(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}"


def metric_card(label: str, value: str, note: str, tone: str = "neutral") -> html.Div:
    return html.Div(
        [html.Div(label, className="metric-label"), html.Div(value, className=f"metric-value {tone}"), html.Div(note, className="metric-note")],
        className="metric-card",
    )


def pnl_drawdown_chart(history: pd.DataFrame) -> go.Figure:
    chart = make_subplots(specs=[[{"secondary_y": True}]])
    chart.add_trace(
        go.Scatter(
            x=history.index,
            y=history["cumulative_pnl_usd"] / 1_000_000,
            mode="lines",
            name="Cumulative P&L",
            line={"color": "#0b8673", "width": 2.5},
            fill="tozeroy",
            fillcolor="rgba(11, 134, 115, 0.12)",
            hovertemplate="%{x|%d %b %Y}<br>Cumulative P&L: $%{y:.2f}m<extra></extra>",
        ),
        secondary_y=False,
    )
    chart.add_trace(
        go.Scatter(
            x=history.index,
            y=history["drawdown_usd"] / 1_000_000,
            mode="lines",
            name="Drawdown",
            line={"color": "#db654e", "width": 2},
            hovertemplate="%{x|%d %b %Y}<br>Drawdown: $%{y:.2f}m<extra></extra>",
        ),
        secondary_y=True,
    )
    chart.update_layout(**base_layout("Cumulative P&L and drawdown"), legend={"orientation": "h", "x": 0.02, "y": 1.15})
    chart.update_yaxes(title_text="Cumulative P&L (USD m)", secondary_y=False, gridcolor="#e9eef3")
    chart.update_yaxes(title_text="Drawdown (USD m)", secondary_y=True, showgrid=False)
    return chart


def daily_pnl_chart(history: pd.DataFrame) -> go.Figure:
    var = rolling_var(history["daily_pnl_usd"])
    colors = ["#0b8673" if pnl >= 0 else "#db654e" for pnl in history["daily_pnl_usd"]]
    chart = go.Figure()
    chart.add_trace(
        go.Bar(
            x=history.index,
            y=history["daily_pnl_usd"] / 1_000_000,
            name="Daily P&L",
            marker_color=colors,
            hovertemplate="%{x|%d %b %Y}<br>Daily P&L: $%{y:.2f}m<extra></extra>",
        )
    )
    chart.add_trace(
        go.Scatter(
            x=history.index,
            y=-var / 1_000_000,
            mode="lines",
            name="60d 99% VaR",
            line={"color": "#8b3d3d", "width": 2, "dash": "dot"},
            hovertemplate="%{x|%d %b %Y}<br>99% VaR: $%{customdata:.2f}m<extra></extra>",
            customdata=var / 1_000_000,
        )
    )
    chart.update_layout(**base_layout("Daily P&L versus rolling 99% VaR"), legend={"orientation": "h", "x": 0.02, "y": 1.15})
    chart.update_yaxes(title="USD m", gridcolor="#e9eef3")
    return chart


def base_layout(title: str) -> dict:
    return {
        "template": "plotly_white",
        "title": {"text": title, "x": 0.02, "xanchor": "left", "font": {"size": 16, "color": "#13253f"}},
        "height": 335,
        "margin": {"l": 58, "r": 58, "t": 64, "b": 42},
        "paper_bgcolor": "white",
        "plot_bgcolor": "white",
        "font": {"family": "Inter, Arial, sans-serif", "color": "#536579"},
        "hovermode": "x unified",
        "xaxis": {"showgrid": False, "zeroline": False},
    }


def position_table(position_summary: pd.DataFrame) -> dash_table.DataTable:
    display = position_summary.copy()
    display["gross_notional_usd"] = display["gross_notional_usd"].map(money)
    display["net_notional_usd"] = display["net_notional_usd"].map(money)
    display["spot"] = display["spot"].map(number)
    for field in ["daily_pnl_usd", "mtd_pnl_usd", "ytd_pnl_usd", "inception_pnl_usd", "position_var_usd", "component_var_usd", "marginal_var_per_usd_m"]:
        display[field] = display[field].map(money)
    labels = {
        "currency_pair": "Pair",
        "direction": "Net direction",
        "gross_notional_usd": "Gross notional",
        "net_notional_usd": "Net notional",
        "spot": "Current spot",
        "daily_pnl_usd": "Daily P&L",
        "mtd_pnl_usd": "MTD P&L",
        "ytd_pnl_usd": "YTD P&L",
        "inception_pnl_usd": "Since inception",
        "position_var_usd": "Position VaR",
        "component_var_usd": "Component VaR",
        "marginal_var_per_usd_m": "Marginal VaR / $1m",
    }
    fields = list(labels)
    display = display[fields].rename(columns=labels)
    return dash_table.DataTable(
        data=display.to_dict("records"),
        columns=[{"name": name, "id": name} for name in display.columns],
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#eef3f7", "border": "none", "color": "#40556d", "fontWeight": 700, "fontSize": 11, "letterSpacing": ".04em", "padding": "12px", "whiteSpace": "normal"},
        style_cell={"backgroundColor": "white", "border": "none", "borderBottom": "1px solid #edf1f5", "color": "#23364d", "fontFamily": "Inter, Arial, sans-serif", "fontSize": 12, "padding": "12px", "textAlign": "right"},
        style_cell_conditional=[{"if": {"column_id": column}, "textAlign": "left"} for column in ["Pair", "Net direction"]],
        style_data_conditional=[
            {"if": {"filter_query": '{Net direction} = "Net long USD"'}, "borderLeft": "3px solid #0b8673"},
            {"if": {"filter_query": '{Net direction} = "Net short USD"'}, "borderLeft": "3px solid #db654e"},
        ],
    )


GROUPING_OPTIONS = {
    "trader": "Trader",
    "strategy": "Strategy",
    "region": "Region",
    "currency_pair": "Underlying",
    "product_type": "Type",
}


def register_grouping_fields(trades: pd.DataFrame) -> None:
    """Expose additional categorical trade fields in both drag-to-group views.

    Identifiers, instrument labels, dates, and numeric measures stay out of the
    grouping shelf. A future tenor field should therefore be supplied as a
    categorical bucket (for example ``tenor_bucket``), not raw days to maturity.
    """
    excluded = {"trade_id", "trade_date", "instrument_name"}
    labels = {"side": "Direction"}
    for column in trades.columns:
        if column in excluded or column in GROUPING_OPTIONS or pd.api.types.is_numeric_dtype(trades[column]):
            continue
        GROUPING_OPTIONS[column] = labels.get(column, column.replace("_", " ").title())


def grouping_shelf(scope: str, grouping: list[str]) -> html.Div:
    """Render a drop shelf whose order becomes the book-view hierarchy."""
    chips = []
    for index, field in enumerate(grouping, start=1):
        chips.append(
            html.Div(
                [
                    html.Span(str(index), className="group-order"),
                    html.Span(GROUPING_OPTIONS[field]),
                    html.Button("×", className="group-remove", type="button", title=f"Remove {GROUPING_OPTIONS[field]} from grouping"),
                ],
                className="group-chip",
                draggable="true",
                **{"data-group-chip-field": field},
            )
        )
    empty = html.Span("Drag a table column here to group the book", className="group-shelf-empty") if not chips else None
    return html.Div(
        [html.Div("GROUP BY", className="input-label"), html.Div([*chips, empty], className="group-shelf", **{"data-group-scope": scope})],
        className="group-shelf-wrap",
    )


def grouping_headers(scope: str) -> html.Div:
    """Make live book dimensions draggable directly from the table header."""
    headers = [
        html.Button(
            [html.Span("⠿", className="header-drag-mark"), html.Span(label)],
            className="dimension-header",
            type="button",
            draggable="true",
            title=f"Drag {label} to Group by",
            **{"data-group-field": field, "data-group-scope": scope},
        )
        for field, label in GROUPING_OPTIONS.items()
    ]
    return html.Div(headers, className="dimension-header-row", **{"data-group-header-row": scope})


VINTAGE_LABELS = {
    "eod": "EOD · latest return sample",
    "year_1": "Year −1 · prior return sample",
    "year_2": "Year −2 · earlier return sample",
}


SCENARIO_SHOCKS = {
    "Broad EM risk-off": {"USDBRL": 0.10, "USDMXN": 0.08, "USDZAR": 0.10, "USDTRY": 0.08, "USDINR": 0.03, "USDPLN": 0.07},
    "EM risk-on reversal": {"USDBRL": -0.07, "USDMXN": -0.06, "USDZAR": -0.07, "USDTRY": -0.05, "USDINR": -0.02, "USDPLN": -0.05},
    "LatAm sell-off": {"USDBRL": 0.12, "USDMXN": 0.09},
    "Türkiye stress": {"USDTRY": 0.15},
}


def performance_layout(state: dict[str, object]) -> html.Div:
    """Build the existing high-level performance-monitoring view."""
    metrics = state["metrics"]
    history = state["history"]
    positions = state["positions"]
    current = history.iloc[-1]
    trailing = history.tail(63)["daily_return"]
    sharpe = (trailing.mean() / trailing.std(ddof=1) * (252**0.5)) if trailing.std(ddof=1) > 0 else 0.0
    gross = metrics["notional_usd"].sum()
    net = metrics["signed_notional_usd"].sum()
    tone = lambda value: "positive" if value >= 0 else "negative"
    return html.Div(
        [
            html.Div([html.Span("01", className="section-number"), html.Div([html.Div("PERFORMANCE MONITOR", className="eyebrow"), html.H2("Book Summary")]), html.Div("USD reporting currency · Gross exposure", className="section-context")], className="section-header"),
            html.Div(
                [
                    metric_card("Daily P&L", money(current.daily_pnl_usd), "Compared with prior close", tone(current.daily_pnl_usd)),
                    metric_card("MTD P&L", money(metrics.mtd_pnl_usd.sum()), "Calendar month to date", tone(metrics.mtd_pnl_usd.sum())),
                    metric_card("YTD P&L", money(metrics.ytd_pnl_usd.sum()), "Calendar year to date", tone(metrics.ytd_pnl_usd.sum())),
                    metric_card("Since inception", money(current.cumulative_pnl_usd), "Open-trade mark-to-market", tone(current.cumulative_pnl_usd)),
                    metric_card("63-day Sharpe", f"{sharpe:.2f}", "Daily returns, annualised", tone(sharpe)),
                    metric_card("Maximum drawdown", money(history.drawdown_usd.min()), "From NAV + P&L high-water mark", "negative"),
                    metric_card("99% historical VaR", money(state["portfolio_var"]), "252 days · 2nd/3rd worst loss", "negative"),
                    metric_card("Gross / net", f"{gross / 1e6:.1f} / {net / 1e6:.1f}m", "USD spot notional", "neutral"),
                ],
                className="metrics-grid",
            ),
            html.Div([html.Div([html.Div("Position contribution", className="panel-heading"), position_table(positions)], className="panel table-panel"), html.Div([dcc.Graph(figure=pnl_drawdown_chart(history), config={"displayModeBar": False}), dcc.Graph(figure=daily_pnl_chart(history), config={"displayModeBar": False})], className="chart-stack")], className="main-grid"),
            html.P("Spot convention: each Buy USD trade is long USD and short a fixed local-currency amount at its entry spot. The dashboard fully reprices that local leg into USD. Position and portfolio VaR use 252 historical return scenarios applied to today’s open positions.", className="method-note"),
        ]
    )


def ledger_metrics(frame: pd.DataFrame) -> dict[str, float]:
    """Summarise a trade subset for either a hierarchy node or a leaf trade."""
    return {
        "trade_count": float(len(frame)),
        "gross_notional_usd": float(frame["notional_usd"].sum()),
        "net_notional_usd": float(frame["signed_notional_usd"].sum()),
        "start_npv_usd": float(frame["start_npv_usd"].sum()),
        "current_npv_usd": float(frame["current_npv_usd"].sum()),
        "daily_pnl_usd": float(frame["daily_pnl_usd"].sum()),
        "mtd_pnl_usd": float(frame["mtd_pnl_usd"].sum()),
        "inception_pnl_usd": float(frame["inception_pnl_usd"].sum()),
    }


def ledger_cells(values: dict[str, float], spot: str = "—") -> list[html.Div]:
    """Render the shared numeric cells in a ledger row."""
    change_class = "positive" if values["daily_pnl_usd"] >= 0 else "negative"
    return [
        html.Div(money(values["inception_pnl_usd"]), className="ledger-cell"),
        html.Div(money(values["current_npv_usd"]), className="ledger-cell"),
        html.Div(money(values["net_notional_usd"]), className="ledger-cell"),
        html.Div(money(values["daily_pnl_usd"]), className=f"ledger-cell {change_class}"),
        html.Div(money(values["mtd_pnl_usd"]), className="ledger-cell"),
        html.Div(money(values["start_npv_usd"]), className="ledger-cell"),
        html.Div(spot, className="ledger-cell"),
        html.Div(money(values["gross_notional_usd"]), className="ledger-cell"),
        html.Div(str(int(values["trade_count"])), className="ledger-cell"),
    ]


def group_path(parent_path: tuple[tuple[str, str], ...], field: str, value: object) -> tuple[tuple[str, str], ...]:
    return (*parent_path, (field, str(value)))


def group_key(path: tuple[tuple[str, str], ...]) -> str:
    return json.dumps(path)


def all_group_keys(frame: pd.DataFrame, fields: list[str], depth: int = 0, parent_path: tuple[tuple[str, str], ...] = ()) -> set[str]:
    """Return every expandable hierarchy key for the selected grouping order."""
    if depth >= len(fields):
        return set()
    keys: set[str] = set()
    field = fields[depth]
    for value, subset in frame.groupby(field, dropna=False, sort=True):
        path = group_path(parent_path, field, value)
        keys.add(group_key(path))
        keys.update(all_group_keys(subset, fields, depth + 1, path))
    return keys


def leaf_row(trade: pd.Series, depth: int) -> html.Div:
    values = ledger_metrics(pd.DataFrame([trade]))
    label = f"{trade['instrument_name']} · {trade['trade_id']}"
    return html.Div(
        [html.Div(label, className="ledger-label ledger-leaf", style={"paddingLeft": f"{18 + depth * 22}px"}), *ledger_cells(values, number(float(trade["as_of_spot"])))],
        className="ledger-row ledger-trade",
    )


def ledger_nodes(frame: pd.DataFrame, fields: list[str], expanded: set[str], depth: int = 0, parent_path: tuple[tuple[str, str], ...] = ()) -> list[html.Div]:
    """Create only the currently visible rows of the expandable book hierarchy."""
    if depth >= len(fields):
        return [leaf_row(trade, depth) for _, trade in frame.sort_values("trade_id").iterrows()]

    field = fields[depth]
    rows: list[html.Div] = []
    for value, subset in frame.groupby(field, dropna=False, sort=True):
        path = group_path(parent_path, field, value)
        key = group_key(path)
        is_open = key in expanded
        label = f"{GROUPING_OPTIONS[field]}: {value}"
        rows.append(
            html.Div(
                [
                    html.Div(
                        [html.Button("⌄" if is_open else "›", id={"type": "ledger-toggle", "index": key}, className="tree-toggle", n_clicks=0), html.Span(label)],
                        className="ledger-label ledger-group-label",
                        style={"paddingLeft": f"{12 + depth * 22}px"},
                    ),
                    *ledger_cells(ledger_metrics(subset)),
                ],
                className="ledger-row ledger-group",
            )
        )
        if is_open:
            rows.extend(ledger_nodes(subset, fields, expanded, depth + 1, path))
    return rows


def trade_ledger(metrics: pd.DataFrame, grouping: list[str], expanded: list[str]) -> html.Div:
    """Build the configurable, expandable trade-breakdown ledger."""
    columns = ["Book view", "P&L", "Position value", "Position value (base CCY)", "Change", "MTD P&L", "Start NPV", "Current spot", "Gross USD", "Trades"]
    header = html.Div([html.Div(column, className="ledger-cell") for column in columns], className="ledger-row ledger-header")
    expanded_set = set(expanded or [])
    rows = ledger_nodes(metrics, grouping, expanded_set) if grouping else [leaf_row(trade, 0) for _, trade in metrics.sort_values("trade_id").iterrows()]
    return html.Div([grouping_shelf("ledger", grouping), grouping_headers("ledger"), header, *rows], className="ledger-scroll")


def trade_layout(state: dict[str, object]) -> html.Div:
    """Build Tab 2, a PM-controlled hierarchy with trade-level metrics."""
    return html.Div(
        [
            html.Div([html.Span("02", className="section-number"), html.Div([html.Div("TRADE LEDGER", className="eyebrow"), html.H2("Book Breakdown")]), html.Div("Select grouping order, then expand any level", className="section-context")], className="section-header"),
            html.Div(
                [
                    html.Div([html.Button("Expand all", id="expand-all", n_clicks=0, className="control-button"), html.Button("Collapse all", id="collapse-all", n_clicks=0, className="control-button secondary")], className="ledger-actions"),
                ],
                className="ledger-controls panel",
            ),
            html.Div(id="trade-ledger", className="panel ledger-panel"),
            html.P("Drag a live table column into Group by to build the hierarchy, then drag grouped fields to reorder them. P&L is since trade inception. Position value is the USD mark-to-market; base-currency position value is the signed USD notional for this USD/local-currency spot book. Start NPV is the prior-close mark, and Change is the one-day USD P&L.", className="method-note"),
        ]
    )


def risk_metrics_for_subset(
    subset: pd.DataFrame,
    trade_scenarios: pd.DataFrame,
    portfolio_scenarios: pd.Series,
    portfolio_var: float,
    portfolio_tail_dates: pd.Index,
    portfolio_cvar_dates: pd.Index,
) -> dict[str, float]:
    """Calculate standalone, component, marginal, and tail risk for a tree node."""
    subset_scenarios = trade_scenarios[subset["trade_id"].tolist()].sum(axis=1)
    position_var, _ = var_from_pnl(subset_scenarios)
    position_cvar, _ = cvar_from_pnl(subset_scenarios, position_var)
    bumped_var, _ = var_from_pnl(portfolio_scenarios + 0.01 * subset_scenarios)
    bump_in_millions = 0.01 * subset["notional_usd"].sum() / 1_000_000
    worst_losses = -subset_scenarios.nsmallest(3).to_numpy()
    return {
        "trade_count": float(len(subset)),
        "gross_notional_usd": float(subset["notional_usd"].sum()),
        "position_var_usd": position_var,
        "component_var_usd": float(-subset_scenarios.loc[portfolio_tail_dates].mean()),
        "position_cvar_usd": position_cvar,
        "component_cvar_usd": float(-subset_scenarios.loc[portfolio_cvar_dates].mean()),
        "marginal_var_per_usd_m": (bumped_var - portfolio_var) / bump_in_millions,
        "bumped_portfolio_var_usd": bumped_var,
        "worst_loss_usd": float(worst_losses[0]),
        "second_worst_loss_usd": float(worst_losses[1]),
        "third_worst_loss_usd": float(worst_losses[2]),
    }


def risk_ledger_cells(values: dict[str, float], risk_type: str) -> list[html.Div]:
    if risk_type == "cvar":
        return [
            html.Div(money(values["component_cvar_usd"]), className="ledger-cell"),
            html.Div(money(values["position_cvar_usd"]), className="ledger-cell"),
            html.Div(money(values["position_var_usd"]), className="ledger-cell"),
            html.Div(money(values["worst_loss_usd"]), className="ledger-cell negative"),
            html.Div(money(values["second_worst_loss_usd"]), className="ledger-cell"),
            html.Div(money(values["third_worst_loss_usd"]), className="ledger-cell"),
            html.Div(money(values["gross_notional_usd"]), className="ledger-cell"),
            html.Div(str(int(values["trade_count"])), className="ledger-cell"),
        ]
    if risk_type == "marginal":
        return [
            html.Div(money(values["marginal_var_per_usd_m"]), className="ledger-cell"),
            html.Div(money(values["position_var_usd"]), className="ledger-cell"),
            html.Div(money(values["bumped_portfolio_var_usd"]), className="ledger-cell"),
            html.Div(money(values["component_var_usd"]), className="ledger-cell"),
            html.Div(money(values["worst_loss_usd"]), className="ledger-cell negative"),
            html.Div(money(values["second_worst_loss_usd"]), className="ledger-cell"),
            html.Div(money(values["gross_notional_usd"]), className="ledger-cell"),
            html.Div(str(int(values["trade_count"])), className="ledger-cell"),
        ]
    return [
        html.Div(money(values["component_var_usd"]), className="ledger-cell"),
        html.Div(money(values["position_var_usd"]), className="ledger-cell"),
        html.Div(money(values["marginal_var_per_usd_m"]), className="ledger-cell"),
        html.Div(money(values["worst_loss_usd"]), className="ledger-cell negative"),
        html.Div(money(values["second_worst_loss_usd"]), className="ledger-cell"),
        html.Div(money(values["third_worst_loss_usd"]), className="ledger-cell"),
        html.Div(money(values["gross_notional_usd"]), className="ledger-cell"),
        html.Div(str(int(values["trade_count"])), className="ledger-cell"),
    ]


def risk_leaf_row(
    trade: pd.Series,
    depth: int,
    trade_scenarios: pd.DataFrame,
    portfolio_scenarios: pd.Series,
    portfolio_var: float,
    portfolio_tail_dates: pd.Index,
    portfolio_cvar_dates: pd.Index,
    risk_type: str,
) -> html.Div:
    values = risk_metrics_for_subset(pd.DataFrame([trade]), trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates)
    label = f"{trade['instrument_name']} · {trade['trade_id']}"
    return html.Div(
        [html.Div(label, className="ledger-label ledger-leaf", style={"paddingLeft": f"{18 + depth * 22}px"}), *risk_ledger_cells(values, risk_type)],
        className="risk-row ledger-trade",
    )


def risk_ledger_nodes(
    frame: pd.DataFrame,
    fields: list[str],
    expanded: set[str],
    trade_scenarios: pd.DataFrame,
    portfolio_scenarios: pd.Series,
    portfolio_var: float,
    portfolio_tail_dates: pd.Index,
    portfolio_cvar_dates: pd.Index,
    risk_type: str,
    depth: int = 0,
    parent_path: tuple[tuple[str, str], ...] = (),
) -> list[html.Div]:
    if depth >= len(fields):
        return [risk_leaf_row(trade, depth, trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates, risk_type) for _, trade in frame.sort_values("trade_id").iterrows()]

    field = fields[depth]
    rows: list[html.Div] = []
    for value, subset in frame.groupby(field, dropna=False, sort=True):
        path = group_path(parent_path, field, value)
        key = group_key(path)
        is_open = key in expanded
        rows.append(
            html.Div(
                [
                    html.Div(
                        [html.Button("⌄" if is_open else "›", id={"type": "var-toggle", "index": key}, className="tree-toggle", n_clicks=0), html.Span(f"{GROUPING_OPTIONS[field]}: {value}")],
                        className="ledger-label ledger-group-label",
                        style={"paddingLeft": f"{12 + depth * 22}px"},
                    ),
                    *risk_ledger_cells(risk_metrics_for_subset(subset, trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates), risk_type),
                ],
                className="risk-row ledger-group",
            )
        )
        if is_open:
            rows.extend(risk_ledger_nodes(subset, fields, expanded, trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates, risk_type, depth + 1, path))
    return rows


def risk_ledger(
    metrics: pd.DataFrame,
    grouping: list[str],
    expanded: list[str],
    trade_scenarios: pd.DataFrame,
    portfolio_scenarios: pd.Series,
    portfolio_var: float,
    portfolio_tail_dates: pd.Index,
    portfolio_cvar_dates: pd.Index,
    risk_type: str,
) -> html.Div:
    columns_by_type = {
        "var": ["Risk view", "VaR contribution", "Position VaR", "Marginal VaR / $1m", "Worst loss", "2nd-worst loss", "3rd-worst loss", "Gross USD", "Trades"],
        "cvar": ["Risk view", "Expected-Shortfall contribution", "Position Expected Shortfall", "Position VaR", "Worst loss", "2nd-worst loss", "3rd-worst loss", "Gross USD", "Trades"],
        "marginal": ["Risk view", "Marginal VaR / $1m", "Position VaR", "Bumped portfolio VaR", "Component VaR", "Worst loss", "2nd-worst loss", "Gross USD", "Trades"],
    }
    columns = columns_by_type[risk_type]
    header = html.Div([html.Div(column, className="ledger-cell") for column in columns], className="risk-row ledger-header")
    expanded_set = set(expanded or [])
    if grouping:
        rows = risk_ledger_nodes(metrics, grouping, expanded_set, trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates, risk_type)
    else:
        rows = [risk_leaf_row(trade, 0, trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates, risk_type) for _, trade in metrics.sort_values("trade_id").iterrows()]
    return html.Div([grouping_shelf("var", grouping), grouping_headers("var"), header, *rows], className="risk-scroll")


def risk_figures(
    metrics: pd.DataFrame,
    grouping: list[str],
    trade_scenarios: pd.DataFrame,
    portfolio_scenarios: pd.Series,
    portfolio_var: float,
    portfolio_tail_dates: pd.Index,
    portfolio_cvar_dates: pd.Index,
    risk_type: str,
) -> tuple[go.Figure, go.Figure]:
    """Create the contribution and loss-distribution views beneath the VaR ledger."""
    field = grouping[0] if grouping else "currency_pair"
    measure_by_type = {
        "var": ("component_var_usd", "Component VaR"),
        "cvar": ("component_cvar_usd", "Expected Shortfall contribution"),
        "marginal": ("marginal_var_per_usd_m", "Marginal VaR per $1m"),
    }
    measure, title = measure_by_type[risk_type]
    contributions = []
    for value, subset in metrics.groupby(field, dropna=False, sort=True):
        summary = risk_metrics_for_subset(subset, trade_scenarios, portfolio_scenarios, portfolio_var, portfolio_tail_dates, portfolio_cvar_dates)
        contributions.append({"label": str(value), "measure": summary[measure]})
    contribution_frame = pd.DataFrame(contributions).sort_values("measure")
    contribution = go.Figure(
        go.Bar(
            x=contribution_frame["measure"] / 1_000_000,
            y=contribution_frame["label"],
            orientation="h",
            marker_color=["#cf5b48" if value < 0 else "#0b8673" for value in contribution_frame["measure"]],
            hovertemplate=f"%{{y}}<br>{title}: $%{{x:.2f}}m<extra></extra>",
        )
    )
    contribution_layout = base_layout(f"{title} by {GROUPING_OPTIONS[field]}")
    contribution_layout["height"] = 300
    contribution.update_layout(**contribution_layout)
    contribution.update_xaxes(title="USD m", gridcolor="#e9eef3")
    contribution.update_yaxes(title=None)

    distribution = go.Figure(
        go.Histogram(
            x=portfolio_scenarios / 1_000_000,
            nbinsx=32,
            marker_color="#2c6faa",
            hovertemplate="Portfolio P&L: $%{x:.2f}m<br>Count: %{y}<extra></extra>",
        )
    )
    distribution.add_vline(x=-portfolio_var / 1_000_000, line_color="#cf5b48", line_width=2, line_dash="dash", annotation_text="99% VaR", annotation_position="top left")
    distribution_layout = base_layout("Historical simulation loss distribution")
    distribution_layout["height"] = 300
    distribution_layout["showlegend"] = False
    distribution.update_layout(**distribution_layout)
    distribution.update_xaxes(title="Simulated one-day P&L (USD m)", gridcolor="#e9eef3")
    distribution.update_yaxes(title="Observations", gridcolor="#e9eef3")
    return contribution, distribution


def risk_layout() -> html.Div:
    return html.Div(
        [
            html.Div([html.Span("03", className="section-number"), html.Div([html.Div("HISTORICAL VAR", className="eyebrow"), html.H2("VaR attribution")]), html.Div("Fixed EOD positions · historical spot shocks", className="section-context")], className="section-header"),
            html.Div(
                [
                    html.Div([html.Label("Lookback", className="input-label"), dcc.RadioItems(id="var-lookback", options=[{"label": "252 days", "value": 252}, {"label": "500 days", "value": 500}], value=252, inline=True, className="lookback-control")]),
                    html.Div([html.Label("Simulation vintage", className="input-label"), dcc.Dropdown(id="var-vintage", options=[{"label": label, "value": key} for key, label in VINTAGE_LABELS.items()], value="eod", clearable=False)], className="vintage-picker"),
                    html.Div([html.Button("Expand all", id="var-expand-all", n_clicks=0, className="control-button"), html.Button("Collapse all", id="var-collapse-all", n_clicks=0, className="control-button secondary")], className="ledger-actions"),
                ],
                className="risk-controls panel",
            ),
            dcc.Tabs(id="var-type", value="var", children=[dcc.Tab(label="VaR", value="var"), dcc.Tab(label="Expected Shortfall", value="cvar"), dcc.Tab(label="Marginal VaR", value="marginal")], className="var-subtabs"),
            html.Div(id="var-summary", className="var-summary"),
            html.Div(id="var-ledger", className="panel risk-panel"),
            html.Div([dcc.Graph(id="var-contribution-chart", config={"displayModeBar": False}), dcc.Graph(id="var-distribution-chart", config={"displayModeBar": False})], className="var-chart-grid"),
            html.P("Drag a live table column into Group by to build the risk hierarchy, then drag grouped fields to reorder them. For every historical day, today’s EOD spot positions are held fixed and repriced using that day’s return. 99% VaR equals the average loss on the second- and third-worst simulations. Year −1 and Year −2 only shift the historical return sample; they do not substitute historical positions.", className="method-note"),
        ]
    )


def load_limits(path: Path) -> dict[str, float]:
    """Load the explicitly configurable sample risk limits."""
    limits = pd.read_csv(path)
    required = {"limit_key", "limit_name", "limit_usd", "description"}
    missing = required.difference(limits.columns)
    if missing:
        raise ValueError(f"Limits file is missing: {', '.join(sorted(missing))}")
    if limits["limit_key"].duplicated().any() or (limits["limit_usd"] <= 0).any():
        raise ValueError("Each limit must have a unique key and positive value")
    return limits.set_index("limit_key")["limit_usd"].astype(float).to_dict()


def limit_status(value: float, limit: float) -> tuple[str, float, str]:
    """Classify a non-negative risk usage against a hard limit."""
    utilisation = 100.0 * value / limit if limit else 0.0
    if utilisation > 100.0:
        return "Breach", utilisation, "negative"
    if utilisation >= 80.0:
        return "Watch", utilisation, "warning"
    return "Within limit", utilisation, "positive"


def limit_table(rows: list[dict[str, float | str]], include_pretrade: bool = False) -> dash_table.DataTable:
    """Render a compact, consistent limit-utilisation table."""
    display = pd.DataFrame(rows)
    if include_pretrade:
        display = display[["Metric", "Current", "Proposed", "Limit", "Utilisation", "Status"]]
    else:
        display = display[["Metric", "Current", "Limit", "Utilisation", "Status"]]
    return dash_table.DataTable(
        data=display.to_dict("records"),
        columns=[{"name": column, "id": column} for column in display.columns],
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#eef3f7", "border": "none", "color": "#40556d", "fontWeight": 700, "fontSize": 10, "letterSpacing": ".04em", "padding": "11px", "textTransform": "uppercase"},
        style_cell={"backgroundColor": "white", "border": "none", "borderBottom": "1px solid #edf1f5", "color": "#334b63", "fontFamily": "Inter, Arial, sans-serif", "fontSize": 12, "padding": "11px", "textAlign": "right"},
        style_cell_conditional=[{"if": {"column_id": "Metric"}, "textAlign": "left"}],
        style_data_conditional=[
            {"if": {"filter_query": '{Status} = "Within limit"', "column_id": "Status"}, "color": "#0b8673", "fontWeight": 700},
            {"if": {"filter_query": '{Status} = "Watch"', "column_id": "Status"}, "color": "#b7791f", "fontWeight": 700},
            {"if": {"filter_query": '{Status} = "Breach"', "column_id": "Status"}, "color": "#cf5b48", "fontWeight": 700},
        ],
    )


def limit_monitor_rows(state: dict[str, object]) -> list[dict[str, str]]:
    """Evaluate VaR and realised-P&L risk measures against their limits."""
    limits = state["limits"]
    history = state["history"]
    var_by_vintage = state["limit_var_by_vintage"]
    current_drawdown = max(0.0, -float(history.iloc[-1].drawdown_usd))
    maximum_drawdown = max(0.0, -float(history.drawdown_usd.min()))
    daily_loss = max(0.0, -float(history.iloc[-1].daily_pnl_usd))
    measures = [
        ("99% VaR · EOD", float(var_by_vintage["eod"]), float(limits["var_99_500"])),
        ("99% VaR · Year −1", float(var_by_vintage["year_1"]), float(limits["var_99_500"])),
        ("99% VaR · Year −2", float(var_by_vintage["year_2"]), float(limits["var_99_500"])),
        ("Current peak-to-trough", current_drawdown, float(limits["current_ptt"])),
        ("Maximum peak-to-trough", maximum_drawdown, float(limits["maximum_ptt"])),
        ("One-day loss", daily_loss, float(limits["daily_loss"])),
    ]
    rows = []
    for label, value, limit in measures:
        status, utilisation, _ = limit_status(value, limit)
        rows.append({"Metric": label, "Current": money(value), "Limit": money(limit), "Utilisation": f"{utilisation:.0f}%", "Status": status})
    return rows


def var_limit_chart(state: dict[str, object]) -> go.Figure:
    values = state["limit_var_by_vintage"]
    limit = float(state["limits"]["var_99_500"])
    labels = ["EOD", "Year −1", "Year −2"]
    amounts = [float(values["eod"]), float(values["year_1"]), float(values["year_2"])]
    chart = go.Figure(
        go.Bar(
            x=labels,
            y=[amount / 1_000_000 for amount in amounts],
            marker_color=["#cf5b48" if amount > limit else "#0b8673" for amount in amounts],
            hovertemplate="%{x}<br>99% VaR: $%{y:.2f}m<extra></extra>",
        )
    )
    chart.add_hline(y=limit / 1_000_000, line_color="#cf5b48", line_dash="dash", annotation_text="Limit", annotation_position="top left")
    layout = base_layout("99% VaR by return-history vintage")
    layout["height"] = 310
    layout["showlegend"] = False
    chart.update_layout(**layout)
    chart.update_yaxes(title="USD m", gridcolor="#e9eef3")
    return chart


def scenario_results(metrics: pd.DataFrame) -> pd.DataFrame:
    """Revalue the fixed spot book under transparent deterministic spot shocks."""
    rows = []
    for scenario, shocks in SCENARIO_SHOCKS.items():
        trade_changes = []
        for trade in metrics.itertuples(index=False):
            shock = shocks.get(trade.currency_pair, 0.0)
            shocked_spot = trade.as_of_spot * (1.0 + shock)
            change = spot_pnl(trade.signed_notional_usd, trade.entry_price, shocked_spot) - trade.current_npv_usd
            trade_changes.append((trade.currency_pair, change))
        by_pair = pd.DataFrame(trade_changes, columns=["currency_pair", "pnl_usd"]).groupby("currency_pair")["pnl_usd"].sum()
        driver_pair = str(by_pair.idxmin())
        rows.append({"scenario": scenario, "pnl_usd": float(by_pair.sum()), "driver": driver_pair, "driver_pnl_usd": float(by_pair.loc[driver_pair])})
    return pd.DataFrame(rows)


def scenario_chart(results: pd.DataFrame) -> go.Figure:
    chart = go.Figure(
        go.Bar(
            x=results["scenario"],
            y=results["pnl_usd"] / 1_000_000,
            marker_color=["#0b8673" if value >= 0 else "#cf5b48" for value in results["pnl_usd"]],
            hovertemplate="%{x}<br>Portfolio P&L: $%{y:.2f}m<extra></extra>",
        )
    )
    layout = base_layout("Scenario P&L · fixed EOD positions")
    layout["height"] = 310
    layout["showlegend"] = False
    chart.update_layout(**layout)
    chart.update_yaxes(title="USD m", gridcolor="#e9eef3")
    return chart


def scenario_table(results: pd.DataFrame) -> dash_table.DataTable:
    display = results.copy()
    display["pnl_usd"] = display["pnl_usd"].map(money)
    display["driver_pnl_usd"] = display["driver_pnl_usd"].map(money)
    display = display.rename(columns={"scenario": "Scenario", "pnl_usd": "Portfolio P&L", "driver": "Largest loss driver", "driver_pnl_usd": "Driver P&L"})
    return dash_table.DataTable(
        data=display.to_dict("records"),
        columns=[{"name": column, "id": column} for column in display.columns],
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#eef3f7", "border": "none", "color": "#40556d", "fontWeight": 700, "fontSize": 10, "letterSpacing": ".04em", "padding": "11px", "textTransform": "uppercase"},
        style_cell={"backgroundColor": "white", "border": "none", "borderBottom": "1px solid #edf1f5", "color": "#334b63", "fontFamily": "Inter, Arial, sans-serif", "fontSize": 12, "padding": "11px", "textAlign": "right"},
        style_cell_conditional=[{"if": {"column_id": column}, "textAlign": "left"} for column in ["Scenario", "Largest loss driver"]],
    )


def limits_layout(state: dict[str, object]) -> html.Div:
    """Build the PM-facing limit utilisation and scenario view."""
    history = state["history"]
    limits = state["limits"]
    var_eod = float(state["limit_var_by_vintage"]["eod"])
    current_drawdown = max(0.0, -float(history.iloc[-1].drawdown_usd))
    maximum_drawdown = max(0.0, -float(history.drawdown_usd.min()))
    return html.Div(
        [
            html.Div([html.Span("04", className="section-number"), html.Div([html.Div("LIMITS & SCENARIOS", className="eyebrow"), html.H2("Risk appetite and stress monitoring")]), html.Div("500-day VaR · fixed EOD spot book", className="section-context")], className="section-header"),
            html.Div(
                [
                    metric_card("EOD 99% VaR", money(var_eod), f"{var_eod / float(limits['var_99_500']) * 100:.0f}% of limit", "negative" if var_eod > float(limits["var_99_500"]) else "positive"),
                    metric_card("Current PTT drawdown", money(current_drawdown), f"{current_drawdown / float(limits['current_ptt']) * 100:.0f}% of limit", "negative"),
                    metric_card("Maximum PTT drawdown", money(maximum_drawdown), f"{maximum_drawdown / float(limits['maximum_ptt']) * 100:.0f}% of limit", "negative"),
                    metric_card("One-day loss", money(max(0.0, -float(history.iloc[-1].daily_pnl_usd))), "Versus prior close", "neutral"),
                ],
                className="limits-kpi-grid",
            ),
            html.Div([html.Div([html.Div("Limit utilisation", className="panel-heading"), limit_table(limit_monitor_rows(state))], className="panel table-panel"), dcc.Graph(figure=var_limit_chart(state), config={"displayModeBar": False})], className="limits-grid"),
            html.Div([html.Div("SCENARIO ANALYSIS", className="eyebrow"), html.H3("Transparent spot shocks")], className="subsection-header"),
            html.Div([html.Div([html.Div("Fixed-position scenario results", className="panel-heading"), scenario_table(state["scenario_results"])], className="panel table-panel"), dcc.Graph(figure=scenario_chart(state["scenario_results"]), config={"displayModeBar": False})], className="limits-grid"),
            html.P("The limit comparison uses 500 daily scenarios. At 99%, the 500-day VaR is the fifth-worst simulated loss; the 252-day VaR elsewhere in the dashboard retains the agreed second-/third-worst-loss convention. Peak-to-trough (PTT) is measured as the fall from a portfolio high-water mark. Scenario shocks are deterministic spot moves from the current EOD marks, not forecasts.", className="method-note"),
        ]
    )


def build_dashboard() -> Dash:
    trades = load_portfolio(PORTFOLIO_PATH)
    register_grouping_fields(trades)
    pairs = trades["currency_pair"].drop_duplicates().tolist()
    prices, price_source = load_prices(pairs, DATA_DIR)
    metrics = trade_metrics(trades, prices)
    _, history = portfolio_history(trades, prices, SAMPLE_NAV_USD)
    risk, portfolio_var, _ = historical_var(trades, prices)
    positions = summary_by_pair(metrics, risk)
    limits = load_limits(LIMITS_PATH)
    limit_scenarios = {vintage: simulated_trade_pnl(trades, prices, lookback_days=500, vintage=vintage).sum(axis=1) for vintage in VINTAGE_LABELS}
    limit_var_by_vintage = {vintage: limit_var_from_pnl(scenarios, lookback_days=500)[0] for vintage, scenarios in limit_scenarios.items()}

    state: dict[str, object] = {
        "trades": trades,
        "prices": prices,
        "metrics": metrics,
        "history": history,
        "positions": positions,
        "portfolio_var": portfolio_var,
        "limits": limits,
        "limit_var_by_vintage": limit_var_by_vintage,
        "scenario_results": scenario_results(metrics),
    }
    as_of = prices.index[-1]
    app = Dash(__name__, suppress_callback_exceptions=True)
    app.title = "EM FX Portfolio Monitor"
    app.layout = html.Div(
        [
            html.Header(
                [
                    html.Div([html.Div("FX PORTFOLIO MANAGEMENT", className="brand"), html.H1("EM FX Portfolio Monitor"), html.P("Spot book performance, contribution and risk overview", className="subtitle")]),
                    html.Div([html.Div("AS OF", className="asof-label"), html.Div(as_of.strftime("%d %b %Y"), className="asof-date"), html.Div(price_source, className="source-badge")], className="asof-block"),
                ],
                className="topbar",
            ),
            dcc.Store(id="ledger-expanded", data=[]),
            dcc.Store(id="ledger-grouping", data=["trader", "strategy", "currency_pair", "product_type"]),
            dcc.Store(id="var-expanded", data=[]),
            dcc.Store(id="var-grouping", data=["trader", "strategy", "currency_pair"]),
            dcc.Tabs(id="main-tabs", value="performance", children=[dcc.Tab(label="Performance Monitor", value="performance"), dcc.Tab(label="Trade Ledger", value="trades"), dcc.Tab(label="Historical VaR", value="var"), dcc.Tab(label="Limits & Scenarios", value="limits")]),
            html.Main(id="tab-content", className="content"),
        ],
        className="app-shell",
    )

    @app.callback(Output("tab-content", "children"), Input("main-tabs", "value"))
    def render_tab(tab: str) -> html.Div:
        if tab == "trades":
            return trade_layout(state)
        if tab == "var":
            return risk_layout()
        if tab == "limits":
            return limits_layout(state)
        return performance_layout(state)

    @app.callback(Output("trade-ledger", "children"), Input("ledger-grouping", "data"), Input("ledger-expanded", "data"))
    def render_ledger(grouping: list[str] | None, expanded: list[str] | None) -> html.Div:
        selected = [field for field in (grouping or []) if field in GROUPING_OPTIONS]
        return trade_ledger(state["metrics"], selected, expanded or [])

    @app.callback(
        Output("ledger-expanded", "data"),
        Input({"type": "ledger-toggle", "index": ALL}, "n_clicks"),
        Input("expand-all", "n_clicks"),
        Input("collapse-all", "n_clicks"),
        State("ledger-expanded", "data"),
        State("ledger-grouping", "data"),
        prevent_initial_call=True,
    )
    def update_expansion(_node_clicks: list[int], _expand_clicks: int, _collapse_clicks: int, expanded: list[str] | None, grouping: list[str] | None) -> list[str]:
        selected = [field for field in (grouping or []) if field in GROUPING_OPTIONS]
        triggered = ctx.triggered_id
        if triggered == "expand-all":
            return sorted(all_group_keys(state["metrics"], selected))
        if triggered == "collapse-all":
            return []
        if isinstance(triggered, dict):
            key = triggered["index"]
            open_keys = set(expanded or [])
            open_keys.remove(key) if key in open_keys else open_keys.add(key)
            return sorted(open_keys)
        return expanded or []

    @app.callback(
        Output("var-ledger", "children"),
        Output("var-contribution-chart", "figure"),
        Output("var-distribution-chart", "figure"),
        Output("var-summary", "children"),
        Input("var-lookback", "value"),
        Input("var-vintage", "value"),
        Input("var-type", "value"),
        Input("var-grouping", "data"),
        Input("var-expanded", "data"),
    )
    def render_var(
        lookback_days: int,
        vintage: str,
        risk_type: str,
        grouping: list[str] | None,
        expanded: list[str] | None,
    ) -> tuple[html.Div, go.Figure, go.Figure, html.Div]:
        selected = [field for field in (grouping or []) if field in GROUPING_OPTIONS]
        scenarios = simulated_trade_pnl(state["trades"], state["prices"], int(lookback_days), vintage)
        portfolio_scenarios = scenarios.sum(axis=1)
        portfolio_var, tail_dates = var_from_pnl(portfolio_scenarios)
        portfolio_cvar, cvar_dates = cvar_from_pnl(portfolio_scenarios, portfolio_var)
        ledger = risk_ledger(state["metrics"], selected, expanded or [], scenarios, portfolio_scenarios, portfolio_var, tail_dates, cvar_dates, risk_type)
        contribution, distribution = risk_figures(state["metrics"], selected, scenarios, portfolio_scenarios, portfolio_var, tail_dates, cvar_dates, risk_type)
        tail_losses = -portfolio_scenarios.loc[tail_dates]
        if risk_type == "cvar":
            summary_cards = [
                metric_card("Portfolio 99% Expected Shortfall", money(portfolio_cvar), f"{lookback_days}-day {VINTAGE_LABELS[vintage].lower()}", "negative"),
                metric_card("Tail observations", str(len(cvar_dates)), "Losses beyond the VaR cutoff", "neutral"),
                metric_card("99% VaR cutoff", money(portfolio_var), "Expected Shortfall is the mean beyond this loss", "negative"),
            ]
        elif risk_type == "marginal":
            summary_cards = [
                metric_card("Portfolio 99% VaR", money(portfolio_var), f"{lookback_days}-day {VINTAGE_LABELS[vintage].lower()}", "negative"),
                metric_card("Bump definition", "1%", "Increase each selected position in its current direction", "neutral"),
                metric_card("Reported unit", "USD / $1m", "Incremental VaR per additional gross USD 1m", "neutral"),
            ]
        else:
            summary_cards = [
                metric_card("Portfolio 99% VaR", money(portfolio_var), f"{lookback_days}-day {VINTAGE_LABELS[vintage].lower()}", "negative"),
                metric_card("2nd-worst loss", money(tail_losses.iloc[0]), tail_losses.index[0].strftime("%d %b %Y"), "negative"),
                metric_card("3rd-worst loss", money(tail_losses.iloc[1]), tail_losses.index[1].strftime("%d %b %Y"), "negative"),
            ]
        summary = html.Div(
            summary_cards,
            className="var-summary-grid",
        )
        return ledger, contribution, distribution, summary

    @app.callback(
        Output("var-expanded", "data"),
        Input({"type": "var-toggle", "index": ALL}, "n_clicks"),
        Input("var-expand-all", "n_clicks"),
        Input("var-collapse-all", "n_clicks"),
        State("var-expanded", "data"),
        State("var-grouping", "data"),
        prevent_initial_call=True,
    )
    def update_var_expansion(
        _node_clicks: list[int],
        _expand_clicks: int,
        _collapse_clicks: int,
        expanded: list[str] | None,
        grouping: list[str] | None,
    ) -> list[str]:
        selected = [field for field in (grouping or []) if field in GROUPING_OPTIONS]
        triggered = ctx.triggered_id
        if triggered == "var-expand-all":
            return sorted(all_group_keys(state["metrics"], selected))
        if triggered == "var-collapse-all":
            return []
        if isinstance(triggered, dict):
            key = triggered["index"]
            open_keys = set(expanded or [])
            open_keys.remove(key) if key in open_keys else open_keys.add(key)
            return sorted(open_keys)
        return expanded or []

    return app


app = build_dashboard()
server = app.server


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")), debug=False)
