"""
The dashboard's charts (Altair). Each function takes a small table from metrics.py and returns a chart.
No calculations happen here; every number shown is computed in metrics.py.

Colour is never the only signal: every bar carries a text label with its sign and value, and
budget lines are orange AND dashed, with a legend.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from .formatting import money, money_short

# Torchfolio scheme: black with the logo's flame orange as the accent, on neutral greys.
# Marks clear 3:1 contrast on white except light orange and grey steps, which always carry value labels.
NAVY = "#111111"              # (name kept for the code) black: totals and headings in charts
ACTUAL_COLOR = "#111111"      # actual results: black, the main chart series
BUDGET_COLOR = "#797979"      # budget: grey, dashed in line charts
SECOND_SERIES = "#4D4D4D"     # dark grey
FAVORABLE = "#1F8A70"         # raises profit (functional status colour)
UNFAVORABLE = "#C8413A"       # lowers profit (functional)
UNEXPLAINED = "#FF6F00"       # orange accent: gap in the imported lines
ORANGE = "#FF6F00"            # Torchfolio flame orange (accent)
BUDGET_LINE = ORANGE          # budget in the trend charts: orange AND dashed (colour is not the only cue)
OPERATING_COST = "#FDBA8C"    # light orange: operating expense (above-GOP) steps in the EBITDA waterfall
BELOW_GOP_COST = "#8C877C"    # grey: below-GOP expense steps
INK = "#111111"
MUTED = "#666666"             # grey text
GRID = "#E8E8E8"
AXIS = "#C8C8C8"
FONT = "Inter, sans-serif"      # bundled with the app (static/fonts), same as the theme's body font


def _finish(chart, height: int, title: str | None = None):
    # A little padding so axis labels in the (wider) Inter font are never clipped at the edges.
    chart = chart.properties(width="container", height=height, padding={"left": 8, "right": 6, "top": 4, "bottom": 4})
    if title:
        chart = chart.properties(title=alt.TitleParams(title, anchor="start", fontSize=15, fontWeight=700,
                                                       color=NAVY, offset=10))
    return (chart.configure_view(strokeWidth=0)
            .configure_axis(labelFontSize=13, labelColor=MUTED)
            .configure_legend(labelFontSize=13, labelColor=INK, symbolStrokeWidth=0)
            .configure(background="transparent", font=FONT))


def signed_short(value: float) -> str:
    return ("+" if value >= 0 else "-") + money_short(abs(value))


# --- Monthly trends ------------------------------------------------------------------------

def trend_chart(data: pd.DataFrame, field: str, title: str, is_percent: bool, height: int = 230):
    """Actual (solid Prussian Blue) and budget (dashed Mustard Yellow) by month, with a hover crosshair and tooltip."""
    df = data[["month", "series", field]].rename(columns={field: "value"}).dropna()
    df["date"] = pd.to_datetime(df["month"] + "-01")
    if is_percent:
        df["value"] = df["value"] * 100
    value_format = ".1f" if is_percent else "$,.2f"
    df["label"] = [f"{v:.1f}%" if is_percent else f"${v:,.2f}" for v in df["value"]]
    wide = df.pivot(index="date", columns="series", values="value").reset_index()
    for series in ("Actual", "Budget"):
        if series not in wide:
            wide[series] = None

    x = alt.X("date:T", title=None,
              axis=alt.Axis(format="%b %y", tickCount={"interval": "month", "step": 1}, grid=False,
                            labelColor=MUTED, domainColor=AXIS, tickColor=AXIS))
    y_axis = alt.Axis(tickCount=4, gridColor=GRID, domain=False, ticks=False, labelColor=MUTED,
                      **({"labelExpr": "datum.value + '%'"} if is_percent else {"format": "$,.0f"}))
    y = alt.Y("value:Q", title=None, scale=alt.Scale(zero=False, nice=True), axis=y_axis)
    color = alt.Color("series:N", legend=None,
                      scale=alt.Scale(domain=["Actual", "Budget"], range=[ACTUAL_COLOR, BUDGET_LINE]))
    dash = alt.StrokeDash("series:N", legend=None,
                          scale=alt.Scale(domain=["Actual", "Budget"], range=[[1, 0], [5, 4]]))
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False, clear="pointerout")

    width = alt.StrokeWidth("series:N", legend=None, scale=alt.Scale(domain=["Actual", "Budget"], range=[2, 2.5]))
    lines = alt.Chart(df).mark_line(strokeCap="round", strokeJoin="round").encode(x, y, color, dash, width)
    hover_dots = alt.Chart(df).mark_circle(size=70, stroke="white", strokeWidth=2).encode(
        x, y, color, opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    rule = alt.Chart(wide).mark_rule(color=AXIS).encode(
        x="date:T", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("date:T", title="Month", format="%B %Y"),
                 alt.Tooltip("Actual:Q", format=value_format), alt.Tooltip("Budget:Q", format=value_format)],
    ).add_params(hover)
    latest = df[(df["series"] == "Actual") & (df["date"] == df["date"].max())]
    end_dot = alt.Chart(latest).mark_circle(size=70, color=ACTUAL_COLOR, stroke="white", strokeWidth=2,
                                            opacity=1).encode(x, y)
    label_style = {"align": "right", "dx": -8, "dy": -12, "fontSize": 13, "fontWeight": 600}
    # A white outline drawn first keeps the label readable where it crosses a line.
    end_halo = alt.Chart(latest).mark_text(**label_style, stroke="white", strokeWidth=4,
                                           strokeJoin="round").encode(x, y, text="label:N")
    end_label = alt.Chart(latest).mark_text(**label_style, color=INK).encode(x, y, text="label:N")
    return _finish(alt.layer(lines, hover_dots, rule, end_dot, end_halo, end_label), height, title)


# --- Operating profit bridge -------------------------------------------------------------------

BRIDGE_KINDS = {
    "budget": ("Budget GOP", "#8A8A8A"),
    "actual": ("Actual GOP", ACTUAL_COLOR),
    "favorable": ("Raises GOP", FAVORABLE),
    "unfavorable": ("Lowers GOP", UNFAVORABLE),
    "unexplained": ("Not explained by the lines", UNEXPLAINED),
}


def bridge_chart(steps: pd.DataFrame):
    """
    Horizontal waterfall from budget GOP (top) to actual GOP (bottom). The value axis is zoomed to the
    range the steps move through, so the budget and actual bars are cut off at the left edge (their
    full values are printed on them).
    """
    df = steps.reset_index(drop=True).copy()
    moving = df[~df["kind"].isin(["budget", "actual"])]
    points = list(moving["start"]) + list(moving["end"]) + list(df.loc[df["kind"].isin(["budget", "actual"]), "end"])
    low, high = min(points), max(points)
    span = max(high - low, abs(high) * 0.02, 1.0)
    axis_low, axis_high = low - span * 0.5, high + span * 0.8

    totals = df["kind"].isin(["budget", "actual"])
    df["x0"] = df["start"].where(~totals, axis_low)
    df["x1"] = df["end"]
    df["label_x"] = df[["x0", "x1"]].max(axis=1)
    df["label"] = [money_short(a) if t else signed_short(a) for a, t in zip(df["amount"], totals)]
    df["what"] = df["kind"].map(lambda k: BRIDGE_KINDS[k][0])
    df["amount_text"] = [money(a, 0) for a in df["amount"]]
    df["running_text"] = [money(e, 0) for e in df["end"]]
    order = list(df["step"])
    kinds = [k for k in BRIDGE_KINDS if k in set(df["kind"])]

    y = alt.Y("step:N", sort=order, title=None,
              axis=alt.Axis(labelLimit=150, labelColor=INK, labelFontSize=13, ticks=False, domain=False))
    x = alt.X("x0:Q", title=None, scale=alt.Scale(domain=[axis_low, axis_high], nice=False, zero=False),
              axis=alt.Axis(format="$.3s", gridColor=GRID, tickCount=5, domain=False, ticks=False))
    bars = alt.Chart(df).mark_bar(size=20, clip=True, cornerRadius=2).encode(
        y=y, x=x, x2="x1:Q",
        color=alt.Color("what:N", title=None,
                        scale=alt.Scale(domain=[BRIDGE_KINDS[k][0] for k in kinds],
                                        range=[BRIDGE_KINDS[k][1] for k in kinds]),
                        legend=alt.Legend(orient="top", columns=2, symbolType="square")),
        tooltip=[alt.Tooltip("step:N", title="Line"), alt.Tooltip("amount_text:N", title="Amount"),
                 alt.Tooltip("running_text:N", title="Running GOP")])
    labels = alt.Chart(df).mark_text(align="left", dx=6, fontSize=13, fontWeight=600, color=INK).encode(
        y=y, x="label_x:Q", text="label:N")
    return _finish(alt.layer(bars, labels), height=34 * len(df) + 40)


# --- EBITDA waterfall ----------------------------------------------------------------------------

WATERFALL_KINDS = {
    "total": ("Totals", NAVY),
    "operating": ("Operating expenses", OPERATING_COST),
    "below_gop": ("Below-GOP expenses", BELOW_GOP_COST),
    "difference": ("Difference to reported figure", ORANGE),
}


def waterfall_chart(steps: pd.DataFrame):
    """
    Horizontal waterfall from Total Revenue (top) down to EBITDA (bottom), on a full axis from zero.
    Expense groups step down from the running total. A 'difference' step (mustard, outlined) appears
    only when the lines don't arrive at the GOP or EBITDA supplied in the file.
    """
    df = steps.reset_index(drop=True).copy()
    df["x0"], df["x1"] = df[["start", "end"]].min(axis=1), df[["start", "end"]].max(axis=1)
    top = max(df["x1"].max(), 1.0)
    df["label"] = [money_short(a) if k == "total" else signed_short(a) for a, k in zip(df["amount"], df["kind"])]
    df["what"] = df["kind"].map(lambda k: WATERFALL_KINDS[k][0])
    df["amount_text"] = [money(a, 0) for a in df["amount"]]
    df["running_text"] = [money(e, 0) for e in df["end"]]
    kinds = [k for k in WATERFALL_KINDS if k in set(df["kind"])]
    y = alt.Y("step:N", sort=list(df["step"]), title=None,
              axis=alt.Axis(labelLimit=210, labelColor=INK, labelFontSize=13, ticks=False, domain=False))
    x = alt.X("x0:Q", title=None, scale=alt.Scale(domain=[min(0.0, df["x0"].min()), top * 1.45], nice=False),
              axis=alt.Axis(format="$.2s", gridColor=GRID, tickCount=5, domain=False, ticks=False))
    bars = alt.Chart(df).mark_bar(size=18, cornerRadius=2).encode(
        y=y, x=x, x2="x1:Q",
        color=alt.Color("what:N", title=None,
                        scale=alt.Scale(domain=[WATERFALL_KINDS[k][0] for k in kinds],
                                        range=[WATERFALL_KINDS[k][1] for k in kinds]),
                        legend=alt.Legend(orient="top", columns=1, symbolType="square")),
        stroke=alt.condition(alt.datum.kind == "difference", alt.value(INK), alt.value(None)),
        tooltip=[alt.Tooltip("step:N", title="Line"), alt.Tooltip("amount_text:N", title="Amount"),
                 alt.Tooltip("running_text:N", title="Running total")])
    labels = alt.Chart(df).mark_text(align="left", dx=6, fontSize=13, fontWeight=600, color=INK).encode(
        y=y, x="x1:Q", text="label:N")
    return _finish(alt.layer(bars, labels), height=27 * len(df) + 60)


# --- Revenue Mix ---------------------------------------------------------------------------------

def revenue_mix_chart(table: pd.DataFrame):
    """Room revenue per booking channel (one bar each). Shares are printed only when they are valid."""
    df = table.dropna(subset=["revenue"]).copy()
    shares = "share" in df.columns
    df["label"] = [money_short(v) + (f" · {s * 100:.0f}%" if shares and pd.notna(s) else "")
                   for v, s in zip(df["revenue"], df["share"] if shares else [None] * len(df))]
    y = alt.Y("channel:N", title=None, sort=list(df["channel"]),
              axis=alt.Axis(labelColor=INK, labelFontSize=13, ticks=False, domain=False, labelLimit=170))
    bars = alt.Chart(df).mark_bar(size=18, color=ACTUAL_COLOR, cornerRadiusEnd=4).encode(
        y=y, x=alt.X("revenue:Q", title=None, scale=alt.Scale(domain=[0, df["revenue"].max() * 1.45], nice=False),
                     axis=alt.Axis(format="$.2s", gridColor=GRID, tickCount=3, domain=False, ticks=False)),
        tooltip=[alt.Tooltip("channel:N", title="Channel"), alt.Tooltip("revenue:Q", title="Room revenue", format="$,.0f")])
    labels = alt.Chart(df).mark_text(align="left", dx=6, fontSize=13, color=INK).encode(
        y=y, x="revenue:Q", text="label:N")
    return _finish(alt.layer(bars, labels), height=38 * len(df) + 20)


# --- Revenue by source ---------------------------------------------------------------------

def revenue_chart(rows: pd.DataFrame):
    """Actual revenue per source as a bar, with the budget as a black tick on the same scale."""
    df = rows.copy()
    top = max(df["actual"].max(), df["budget"].fillna(0).max())
    df["label_x"] = df[["actual", "budget"]].max(axis=1)
    df["label"] = [f"{money_short(a)} vs {money_short(b)} budget ({p * 100:+.1f}%)" if pd.notna(b) else money_short(a)
                   for a, b, p in zip(df["actual"], df["budget"], df["percent"].fillna(0))]
    y = alt.Y("source:N", title=None, sort=list(df["source"]),
              axis=alt.Axis(labelColor=INK, labelFontSize=13, ticks=False, domain=False, labelLimit=160))
    scale = alt.Scale(domain=[0, top * 2.1], nice=False)
    bars = alt.Chart(df).mark_bar(size=20, color=ACTUAL_COLOR, cornerRadiusEnd=4).encode(
        y=y, x=alt.X("actual:Q", title=None, scale=scale,
                     axis=alt.Axis(format="$.2s", gridColor=GRID, tickCount=4, domain=False, ticks=False)),
        tooltip=[alt.Tooltip("source:N", title="Source"), alt.Tooltip("actual:Q", title="Actual", format="$,.0f"),
                 alt.Tooltip("budget:Q", title="Budget", format="$,.0f")])
    ticks = alt.Chart(df.dropna(subset=["budget"])).mark_tick(color=INK, thickness=2, size=30).encode(
        y=y, x=alt.X("budget:Q", scale=scale))
    labels = alt.Chart(df).mark_text(align="left", dx=8, fontSize=13, color=INK).encode(
        y=y, x=alt.X("label_x:Q", scale=scale), text="label:N")
    return _finish(alt.layer(bars, ticks, labels), height=48 * len(df) + 20)


# --- Largest expense variances --------------------------------------------------------------------

def expense_chart(rows: pd.DataFrame):
    """
    Actual minus budget per expense line: right (red) = over budget, left (green) = under budget.
    Every value label sits to the right of the zero line, where there is always room, so labels never
    collide with the line names on narrow screens.
    """
    df = rows.copy()
    largest = max(df["difference"].abs().max(), 1.0)
    df["label_x"] = df["difference"].clip(lower=0)
    df["label"] = [f"{signed_short(v)} ({p * 100:+.1f}%)" for v, p in zip(df["difference"], df["percent"])]
    df["what"] = ["Over budget" if over else "Under budget" for over in df["over_budget"]]
    y = alt.Y("line:N", title=None, sort=list(df["line"]),
              axis=alt.Axis(labelColor=INK, labelFontSize=13, ticks=False, domain=False, labelLimit=170))
    x = alt.X("difference:Q", title=None, scale=alt.Scale(domain=[-largest * 1.15, largest * 3.6], nice=False),
              axis=alt.Axis(format="$.2s", gridColor=GRID, tickCount=5, domain=False, ticks=False))
    bars = alt.Chart(df).mark_bar(size=18, cornerRadius=2).encode(
        y=y, x=x,
        color=alt.Color("what:N", title=None,
                        scale=alt.Scale(domain=["Over budget", "Under budget"],
                                        range=[UNFAVORABLE, FAVORABLE]),
                        legend=alt.Legend(orient="top", columns=1, symbolType="square")),
        tooltip=[alt.Tooltip("line:N", title="Line"), alt.Tooltip("actual:Q", title="Actual", format="$,.0f"),
                 alt.Tooltip("budget:Q", title="Budget", format="$,.0f"),
                 alt.Tooltip("difference:Q", title="Actual - budget", format="$,.0f")])
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=AXIS).encode(x="x:Q")
    labels = alt.Chart(df).mark_text(align="left", dx=5, fontSize=13, color=INK).encode(
        y=y, x="label_x:Q", text="label:N")
    return _finish(alt.layer(bars, zero, labels), height=36 * len(df) + 50)
