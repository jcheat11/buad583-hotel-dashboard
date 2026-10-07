"""
Hotel Asset Dashboard: BUAD 583 capstone prototype (v0).

Start it by double-clicking run_app.bat, or with:
    python -m streamlit run app.py --server.address localhost

Two modes:
  - Local company app (app.py, run_app.bat): runs on this computer, keeps real data in local_data/,
    needs no internet connection and never calls an outside AI model.
  - Hosted fictional demo (demo_app.py, Streamlit Community Cloud): dummy uploads are processed on
    Streamlit's servers for the visitor's session only; "Draft with AI" sends a small fictional
    evidence packet to Google Gemini when clicked (see hotel_dashboard/ai_briefing.py).

This file only draws the screens. The logic lives in hotel_dashboard/:
    definitions.py  metric definitions, thresholds and tolerances (change the rules here)
    importer.py     reads and checks the monthly Excel files
    store.py        saves imported data as CSV files in local_data/
    metrics.py      every calculation shown on screen
    briefing.py     the rule-based written briefing
    charts.py       the dashboard charts
    sources.py      the separate imports: channel mix, forward pace, STR and debt service
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image          # Pillow comes with Streamlit; used only to trim the logo's transparent margins

from hotel_dashboard import ai_briefing, ai_eval, briefing, charts, coverage, metrics, sources
from hotel_dashboard import trace as trace_mod
from hotel_dashboard import definitions as d
from hotel_dashboard.demo import DEMO_SAMPLES, SAMPLE_DIR, TEMPLATE_FILE, build_demo_store, monthly_template_bytes
from hotel_dashboard.formatting import count, money, money_short, month_label, pct, points, signed_money, signed_pct
from hotel_dashboard.importer import read_monthly_workbook
from hotel_dashboard.store import DataStore

APP_DIR = Path(__file__).resolve().parent
LOCAL_DATA_DIR = APP_DIR / "local_data"
LOGO_FILE = APP_DIR / "assets" / "torchfolio-portfolio-flame-black-orange.png"   # black wordmark: light background only
ICON_FILE = APP_DIR / "assets" / "torchfolio-icon.png"     # browser-tab icon (scripts/make_icon.py builds it)

MODE = {"hosted_demo": False}   # set by main(); True only when started from demo_app.py
PAGES: dict = {}                 # the app's pages, filled in by main() (detail views are linked from previews)


# ------------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------------

def md(text: str) -> str:
    """Streamlit treats $...$ as a maths formula, so dollar signs must be escaped in markdown."""
    return str(text).replace("$", r"\$")


def when(iso_timestamp: str) -> str:
    """'2026-09-28T13:17:05' -> '28 Sep 2026, 13:17'."""
    return datetime.fromisoformat(str(iso_timestamp)).strftime("%d %b %Y, %H:%M")


@st.cache_resource
def logo_image():
    """The Torchfolio logo with its transparent margins trimmed, so it sits close to the page title."""
    image = Image.open(LOGO_FILE)
    box = image.getchannel("A").getbbox() if image.mode == "RGBA" else None
    return image.crop(box) if box else image


def uploads_allowed() -> bool:
    """Local app: always. Hosted test copy: only after the visitor confirms the files are fictional."""
    return not MODE["hosted_demo"] or bool(st.session_state.get("dummy_confirmed"))


def upload_help() -> str:
    if MODE["hosted_demo"]:
        return "Fictional or dummy data only. Read in memory for this browser session; nothing is saved."
    return "The file is read on this computer only."


def sample_files_zip() -> bytes:
    """The fictional sample files in one zip, so testers can edit a copy and upload it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SAMPLE_DIR.iterdir()):
            if path.suffix in (".xlsx", ".csv"):
                archive.write(path, path.name)
    return buffer.getvalue()


def link(page: str, label: str, icon: str) -> None:
    """A link to another page of the app (skipped when a screen is tested on its own)."""
    if page in PAGES:
        st.page_link(PAGES[page], label=label, icon=icon)


def data_needed(text: str, detail: str) -> None:
    """The honest empty state for a source that hasn't been imported."""
    st.markdown(f":orange-badge[:material/database: {text}]")
    st.caption(detail)


def date_label(iso_date: str) -> str:
    """'2026-09-01' -> '1 Sep 2026'."""
    parsed = datetime.fromisoformat(str(iso_date)[:10])
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def scope_key(scope: metrics.Scope) -> str:
    if scope.kind == "property":
        return f"property:{scope.property_id}"
    if scope.kind == "group":
        return f"group:{scope.classification}={scope.group}"
    return "all"


def workspace() -> str:
    """'demo' = fictional sample data kept in memory; 'local' = your files in the local_data folder."""
    if MODE["hosted_demo"]:
        return "demo"
    if "workspace" not in st.session_state:
        st.session_state.workspace = "local" if (LOCAL_DATA_DIR / "properties.csv").exists() else "demo"
    return st.session_state.workspace


def current_store() -> DataStore | None:
    if workspace() == "demo":
        if "demo_store" not in st.session_state:
            st.session_state.demo_store = build_demo_store()
        return st.session_state.demo_store
    store, problems = DataStore.open_folder(LOCAL_DATA_DIR)
    st.session_state.property_problems = problems
    return store


# ------------------------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------------------------

def sidebar() -> None:
    with st.sidebar:
        st.divider()
        if MODE["hosted_demo"]:
            st.markdown("**Hosted test copy**")
            st.caption("Fictional or dummy data only. Uploads are processed on Streamlit's servers for your "
                       "browser session and never saved. **Draft with AI** sends a small fictional evidence "
                       "packet to Google Gemini when clicked. The company app runs locally and never calls an "
                       "outside model.")
            return
        workspace()
        st.radio("Workspace", ["demo", "local"], key="workspace",
                 format_func=lambda w: "Fictional demo data" if w == "demo" else "My local data")
        if st.session_state.workspace == "demo":
            st.caption("Sample hotels, loaded into memory. Nothing you do here is saved.")
        else:
            st.caption(f"Data is kept on this computer in `{LOCAL_DATA_DIR.name}/` and is never uploaded.")
        st.caption(":material/wifi_off: Works offline · no AI service")


# ------------------------------------------------------------------------------------
# Page 1: Portfolio Overview
# ------------------------------------------------------------------------------------

def overview_page() -> None:
    store = current_store()
    # Short notices sit in the top-right corner; the full sourcing details are on Review Log.
    # Less empty space above the logo than Streamlit's default page padding.
    st.html("<style>[data-testid='stMainBlockContainer']{padding-top:5.5rem}</style>")
    brand, notice_area = st.columns([1, 1], vertical_alignment="center")
    if LOGO_FILE.exists():
        brand.image(logo_image(), width=404)        # same visible size as before; on the very light background
    notices = notice_area.container(horizontal=True, horizontal_alignment="right", gap="small")
    if workspace() == "demo":
        notices.badge("Demo · fictional data", icon=":material/science:", color="gray")
    title_area, filter_area = st.columns([5, 7], vertical_alignment="top")   # title directly under the logo
    title_area.title("Portfolio Overview")

    if store is None:
        st.info("Your local workspace has no property list yet. Open **Data Imports & Checks** to set it up.")
        return
    months = metrics.months_with_actuals(store.facts)
    if not months:
        st.info("No monthly actuals have been imported yet. Open **Data Imports & Checks** to import a file.")
        return

    facts, properties = store.facts, store.properties
    with filter_area:
        scope, month = filters(properties, months)
    ids = metrics.property_ids_in_scope(properties, scope)
    label = scope.label(properties)
    summary = metrics.month_summary(facts, ids, month)
    warnings = store.active_warnings()
    scoped_warnings = warnings[warnings["property_id"].isin(ids) & (warnings["month"] == month)]
    status = data_status(store, ids, month, months, summary, scoped_warnings)

    # The reporting period and comparison basis, stated once under the title.
    title_area.markdown(f":material/calendar_month: **{month_label(month)}**  \n"
                        f"**{label}** · {status['reporting']} of {status['hotels']} "
                        f"hotel{'s' if status['hotels'] != 1 else ''} reporting")
    for text, icon, color, detail in status["notices"]:
        notices.badge(text, icon=icon, color=color, help=md(detail))
    if summary["actual"] is None:
        st.info(f"No actual figures for {label} in {month_label(month)}. Import them on **Data Imports & Checks**.")
        source_indicator(status)
        return

    kpi_cards(summary)
    trends_panel(facts, ids, month)
    profit_section(store, summary, ids, month)
    # The map lists the hotels in the selected group, or the whole portfolio when one hotel is selected.
    attention_panel(store, map_ids_for(scope, ids, properties), month, warnings, scope)
    forward_panels(store, scope, ids, month)
    briefing_panel(store, ids, scope, label, month, warnings)         # one panel; evidence and review on Review Log
    source_indicator(status)   # brief source / data-quality line; full sourcing on Review Log


VIEWS = ["All properties", "One property", "Property group"]


def filters(properties: pd.DataFrame, months: list[str]) -> tuple[metrics.Scope, str]:
    """The 'Show' choice on top, with the hotel or group pickers and the month underneath, all on the right."""
    names = properties.set_index("property_id")["property_name"]
    classifications = d.classification_columns(properties.columns)
    # A click in the exception map (or its 'Show all properties' button) asks for a new view.
    if "pending_property" in st.session_state:
        st.session_state.view = "One property"
        st.session_state.property_pick = st.session_state.pop("pending_property")
    if "pending_view" in st.session_state:
        st.session_state.view = st.session_state.pop("pending_view")
    if "view" not in st.session_state:
        st.session_state.view = "All properties"
    if st.session_state.get("property_pick") not in names.index:
        st.session_state.pop("property_pick", None)
    # Keyed pickers keep the same selection on the detail pages; drop a remembered value that no longer fits.
    if st.session_state.get("group_by") not in classifications:
        st.session_state.pop("group_by", None)
    if st.session_state.get("month_pick") not in months:
        st.session_state.pop("month_pick", None)

    with st.container(horizontal_alignment="right"):
        view = st.segmented_control("Show", VIEWS, required=True, key="view")
        scope = metrics.Scope()
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="bottom", gap="small"):
            if view == "One property":
                pid = st.selectbox("Property", list(names.index), format_func=lambda p: names[p], width=240,
                                   key="property_pick")
                scope = metrics.Scope("property", property_id=pid)
            elif view == "Property group":
                if not classifications:
                    st.warning("properties.csv has no grouping columns.")
                else:
                    classification = st.selectbox("Grouping", classifications, format_func=d.label, width=170,
                                                  key="group_by")
                    sizes = properties[classification].value_counts()
                    if st.session_state.get("group_pick") not in sizes.index:
                        st.session_state.pop("group_pick", None)
                    group = st.selectbox("Group", sorted(sizes.index), width=235,
                                         format_func=lambda g: f"{g} ({sizes[g]})", key="group_pick")
                    scope = metrics.Scope("group", classification=classification, group=group)
            month = st.selectbox("Month", months[::-1], format_func=month_label, width=150, key="month_pick")
    return scope, month


def data_status(store: DataStore, ids, month: str, months: list[str], summary: dict,
                scoped_warnings: pd.DataFrame) -> dict:
    """Which period the figures cover, when they were imported, and anything that affects them."""
    rows = store.facts[(store.facts["month"] == month) & store.facts["property_id"].isin(ids)]
    imports = {}
    for scenario in d.SCENARIO_SHEETS:
        import_ids = rows.loc[rows["scenario"] == scenario, "import_id"].unique()
        log = store.imports[store.imports["import_id"].isin(import_ids)].sort_values("imported_at")
        if not log.empty:
            imports[scenario] = (when(log["imported_at"].iloc[-1]), list(log["file_name"]))
    reporting = len(summary["actual"]["property_ids"]) if summary["actual"] else 0
    names = store.properties.set_index("property_id")["property_name"]

    notices = []   # (short text for the top-right corner, icon, colour, full explanation)
    expected = metrics.latest_closed_month(date.today())
    if months[-1] < expected:
        notices.append((f"{month_label(expected, short=True)} not imported", ":material/schedule:", "orange",
                        f"{month_label(expected)} has closed, but its figures haven't been imported yet. "
                        f"The latest imported month is {month_label(months[-1])}."))
    if summary["missing_actual"]:
        notices.append((f"{reporting} of {len(ids)} hotels reported", ":material/report:", "orange",
                        "No actual figures for " + ", ".join(names.get(p, p) for p in summary["missing_actual"])
                        + " this month. Totals exclude them."))
    if summary["budget_missing"]:
        notices.append(("Budget incomplete", ":material/info:", "gray",
                        "No budget for " + ", ".join(names.get(p, p) for p in summary["budget_missing"])
                        + " this month, so budget comparisons are hidden."))
    if len(scoped_warnings):
        notices.append((f"{len(scoped_warnings)} data warning(s)", ":material/warning:", "red",
                        f"{len(scoped_warnings)} data-check warning(s) affect these figures. "
                        "See Data Imports & Checks for details."))

    note = "; ".join([f"{month_label(month)} figures"] + [
        f"{scenario.lower()} imported {stamp} ({', '.join(files)})" for scenario, (stamp, files) in imports.items()])
    return {"month": month, "imports": imports, "reporting": reporting, "hotels": len(ids),
            "warnings": len(scoped_warnings), "notices": notices, "note": note}


def kpi_cards(summary: dict) -> None:
    a, b = summary["actual"], summary["budget"]
    occ, adr, revpar = (metrics.variance(a, b, f) for f in ("occupancy", "adr", "revpar"))
    profit_field = d.HEADLINE_PROFIT_FIELD
    profit = metrics.variance(a, b, profit_field)

    def versus(text):   # the comparison basis, with the budget figure itself
        return f"vs budget {text}" if b else "no budget to compare"

    cards = st.columns(4)
    cards[0].metric(
        "Occupancy", pct(a["occupancy"]), delta=points(occ["difference"]) if occ else None,
        delta_description=versus(pct(b["occupancy"]) if b else ""), border=True,
        help=md(f"Rooms sold / rooms available = {count(a['rooms_sold'])} / {count(a['rooms_available'])}."))
    cards[1].metric(
        "ADR", money(a["adr"]), delta=f"{'+' if adr['difference'] >= 0 else '-'}${abs(adr['difference']):,.2f}"
        if adr else None, delta_description=versus(money(b["adr"]) if b else ""), border=True,
        help=md(f"Rooms revenue / rooms sold = {money(a['rooms_revenue'], 0)} / {count(a['rooms_sold'])}."))
    cards[2].metric(
        "RevPAR", money(a["revpar"]),
        delta=f"{'+' if revpar['difference'] >= 0 else '-'}${abs(revpar['difference']):,.2f}" if revpar else None,
        delta_description=versus(money(b["revpar"]) if b else ""), border=True,
        help=md(f"Rooms revenue / rooms available = {money(a['rooms_revenue'], 0)} / "
                f"{count(a['rooms_available'])}."))
    cards[3].metric(
        d.PROFIT_CARD_LABELS[profit_field], money_short(a.get(profit_field)),
        delta=signed_pct(profit["percent"]) if profit and profit["percent"] is not None else None,
        delta_description=versus(money_short(b.get(profit_field)) if b else ""), border=True,
        help=md(briefing.profit_source(a, profit_field) + " Added up across the selected hotels."))


def trends_panel(facts: pd.DataFrame, ids, month: str) -> None:
    data = metrics.trend(facts, ids, month, n_months=6)
    first = metrics.shift_month(month, -5)
    with st.container(border=True):
        title, legend = st.columns([2, 1], vertical_alignment="center")
        title.subheader("Performance Trends", anchor=False,
                        help="Each point is recalculated from the combined rooms and revenue of the selected "
                             "hotels. Hover over a chart for exact figures.")
        title.caption(f"{month_label(first, short=True)} – {month_label(month, short=True)} · actual vs budget")
        swatch = "display:inline-block;width:26px;vertical-align:middle;margin-right:6px;border-top:2px"
        legend.html(
            f"""<div style="display:flex;gap:18px;justify-content:flex-end;font-size:1rem;color:{charts.INK}">
              <span><span style="{swatch} solid {charts.ACTUAL_COLOR}"></span>Actual</span>
              <span><span style="{swatch} dashed {charts.BUDGET_LINE};border-top-width:3px"></span>Budget</span></div>""")
        columns = st.columns(3, gap="medium")
        for column, (field, name, is_percent) in zip(
                columns, [("occupancy", "Occupancy (%)", True), ("adr", "ADR (US$)", False),
                          ("revpar", "RevPAR (US$)", False)]):
            column.altair_chart(charts.trend_chart(data, field, name, is_percent), width="stretch", theme=None)


def profit_section(store: DataStore, summary: dict, ids, month: str) -> None:
    period = month_label(month, short=True)
    a, b = summary["actual"], summary["budget"]
    left, right = st.columns([7, 5], gap="medium")
    with left, st.container(border=True):
        st.subheader("EBITDA Waterfall", anchor=False,
                     help="Total Revenue, less each expense group in source P&L order, gives GOP and then EBITDA. "
                          "The GOP and EBITDA bars are the figures supplied in the imported files. If the lines "
                          "don't arrive at them, the gap is shown as its own 'Difference' step; no bar is adjusted. "
                          "Added up across the selected hotels.")
        hotels = len(a["property_ids"])
        st.caption(f"{period} · US$ · actual · {hotels} hotel{'s' if hotels != 1 else ''}")
        waterfall = metrics.ebitda_waterfall(a)
        if waterfall is None:
            st.info("The imported figures don't include every expense group needed for the waterfall.",
                    icon=":material/info:")
        else:
            st.altair_chart(charts.waterfall_chart(waterfall["steps"]), width="stretch", theme=None)
            revenue = money(a["total_revenue"], 0)
            if waterfall["ebitda_supplied"] is None:
                st.warning(md(f"EBITDA is not in the imported files for every selected hotel, so the last bar is "
                              f"calculated from the lines ({money(waterfall['ebitda_calculated'], 0)}) and can't be "
                              "reconciled."), icon=":material/warning:")
            elif waterfall["reconciles"]:
                st.caption(md(f"✓ Reconciles: Total Revenue {revenue} − expense groups = reported EBITDA "
                              f"{money(waterfall['ebitda_supplied'], 0)}"))
            else:
                gaps = "; ".join(f"reported {name} {money(supplied, 0)} vs {money(calculated, 0)} from the lines "
                                 f"(difference {money(gap, 0)})"
                                 for name, calculated, supplied, gap in waterfall["checks"] if gap)
                st.warning(md(f"✗ Does not reconcile: {gaps}. Shown as a separate 'Difference' step. "
                              "See Data Imports & Checks."), icon=":material/warning:")
            if not waterfall["detailed_below_gop"]:
                st.caption("Below-GOP expenses are one step: the imported files don't split them into the source "
                           "P&L groups (Insurance, Real Estate Tax, Personal Property Tax, Other Taxes, Miscellaneous).")
            ebitda = metrics.variance(a, b, "ebitda")
            if ebitda and ebitda["percent"] is not None:
                st.caption(md(f"Budget EBITDA {money(ebitda['budget'], 0)} · actual "
                              f"{signed_pct(ebitda['percent'])} vs budget"))
        with st.expander("Detailed variance figures (all P&L lines, through to NOI)"):
            table = metrics.financial_variances(summary)
            table["Variance %"] = table["Variance %"].astype(float) * 100
            table["Result"] = table["Result"].map({"Favorable": "▲ Favorable", "Unfavorable": "▼ Unfavorable",
                                                   "": ""})
            st.dataframe(table, hide_index=True, width="stretch", column_config={
                "Actual": st.column_config.NumberColumn(format="$%,.0f"),
                "Budget": st.column_config.NumberColumn(format="$%,.0f"),
                "Variance": st.column_config.NumberColumn(format="$%,.0f"),
                "Variance %": st.column_config.NumberColumn(format="%+.1f%%")})
            st.caption("Variance = actual − budget; for costs, positive is unfavorable. "
                       "NOI = GOP − other expenses − capital reserve.")

    with right:
        revenue_mix_panel(store, ids, month)
        with st.container(border=True):
            st.subheader("Largest Expense Variances", anchor=False,
                         help="The five operating expense lines furthest from budget, in either direction. "
                              "+ = over budget; − = under budget.")
            st.caption(f"{period} · US$ · actual − budget")
            expenses = metrics.expense_variances(summary, limit=5)
            if expenses.empty:
                st.info("No complete budget for this view.")
            else:
                st.altair_chart(charts.expense_chart(expenses), width="stretch", theme=None)


def revenue_mix_panel(store: DataStore, ids, month: str) -> None:
    """Room revenue by booking channel, from the separate channel import (never inferred from the P&L)."""
    with st.container(border=True):
        st.subheader("Revenue Mix", anchor=False,
                     help="Room revenue by booking channel: SellAway, Direct Booking, OTA and Other. It comes from a "
                          "separate channel import; the monthly P&L does not split revenue by channel, so nothing "
                          "is inferred from it.")
        mix = sources.channel_mix(store.sources["channel_mix"], store.facts, ids, month)
        names = store.properties.set_index("property_id")["property_name"]
        if not mix["reporting"]:
            st.caption(f"{month_label(month, short=True)} · room revenue by booking channel")
            data_needed("Channel data needed",
                        "The monthly financial files have no revenue by booking channel. Import a channel mix file "
                        "(SellAway, Direct Booking, OTA, Other) on Data Imports & Checks.")
            link("imports", "Add channel data", ":material/upload_file:")
            return
        st.caption(f"{month_label(month, short=True)} · US$ room revenue by channel · "
                   f"{len(mix['reporting'])} of {len(ids)} hotels")
        if mix["table"]["revenue"].notna().any():
            st.altair_chart(charts.revenue_mix_chart(mix["table"]), width="stretch", theme=None)
        blank = mix["table"].loc[mix["table"]["revenue"].isna(), "channel"]
        if len(blank):
            st.caption("Not reported by every hotel, so not totalled: " + ", ".join(blank) + ".")
        if mix["missing"]:
            st.caption("No channel data for " + ", ".join(names.get(p, p) for p in mix["missing"]) + ".")
        if not mix["shares"]:
            st.caption(":orange[Definition pending:] shares of total are not shown, because SellAway may overlap "
                       "with Direct Booking or OTA (see Definitions).")
        elif mix["difference"] is not None and abs(mix["difference"]) > d.TOTAL_TOLERANCE:
            st.caption(md(f"Channels total differs from rooms revenue by {money(mix['difference'], 0)}."))


# Property statuses: symbol + word + fill colour, so colour is never the only signal.
STATUS_STYLE = {                      # status: (symbol, cell fill, text colour) - see .streamlit/config.toml
    "review": ("✕", "#F9D6D2", "#8E1D16"),
    "watch": ("!", "#FDE8C4", "#7A4300"),
    "good": ("✓", "#DCF1E3", "#14532D"),
    "missing": ("–", "#EBEBEB", "#4D4D4D"),
}
STATUS_BADGE = {"review": "red", "watch": "yellow", "good": "green", "missing": "gray"}   # same fills as above
STATUS_HELP = ("On Plan = at or near budget; Watch = off plan, but inside the review threshold; Review = needs a "
               "decision now; No data = nothing to compare. The colour matches the status tiles above.")
STATUS_MEANING = {
    "review": "needs a decision now",
    "watch": "off plan, but inside the review threshold",
    "good": "at or near budget",
}
THRESHOLDS = (
    f"**Review:** RevPAR more than {abs(d.REVIEW_REVPAR_VS_BUDGET):.0%} or GOP more than "
    f"{abs(d.REVIEW_GOP_VS_BUDGET):.0%} below budget; labor or any expense line more than "
    f"{d.ATTENTION_EXPENSE_OVER_BUDGET:.0%} and ${d.ATTENTION_EXPENSE_MIN_DOLLARS:,} over budget; or no actuals.  \n"
    f"**Watch:** RevPAR or GOP more than {abs(d.WATCH_BELOW_BUDGET):.0%} below budget, labor more than "
    f"{d.WATCH_LABOR_OVER_BUDGET:.0%} over, a data-check warning, or no budget.  \n"
    f"**On Plan:** everything else. A property's status is its worst indicator."
)


def attention_panel(store: DataStore, map_ids, month: str, warnings: pd.DataFrame, scope: metrics.Scope) -> None:
    table = metrics.exception_map(store.facts, store.properties, map_ids, month, warnings)
    with st.container(border=True):
        head, action = st.columns([4, 1], vertical_alignment="center")
        head.subheader("Properties Needing Attention", anchor=False, help=THRESHOLDS.replace("$", r"\$"))
        if scope.kind == "property" and action.button("Show all properties", icon=":material/apps:"):
            st.session_state.pending_view = "All properties"
            st.rerun()

        counts = table["overall"].value_counts()
        chips = []
        for status in ("review", "watch", "good", "missing"):
            if status == "missing" and not counts.get(status):
                continue
            symbol, fill, ink = STATUS_STYLE[status]
            chips.append(
                f'<div style="background:{fill};color:{ink};border-radius:10px;padding:6px 14px;min-width:120px">'
                f'<div style="font-size:1.7rem;font-weight:800;line-height:1.1;font-variant-numeric:tabular-nums">'
                f'{int(counts.get(status, 0))}</div>'
                f'<div style="font-weight:700">{symbol} {metrics.STATUS_WORDS[status]}</div>'
                f'<div style="font-size:0.8rem">{STATUS_MEANING.get(status, "no figures to compare")}</div></div>')
        st.html(f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:2px 0 6px">{"".join(chips)}</div>')
        st.caption(f"{month_label(month)} · Click a row to open property details")

        def cell(status, text):
            return f"{STATUS_STYLE[status][0]} {text}"

        def pct_text(value):
            return f"{value * 100:+.1f}%" if pd.notna(value) else "No data"

        def labor_text(value, share):
            if pd.isna(value):
                return "No data"
            return f"{money_short(abs(value))} {'over' if value > 0 else 'under'} budget ({share * 100:+.1f}%)"

        selected = scope.property_id if scope.kind == "property" else None
        display = pd.DataFrame({
            "Property": [("▶ " if pid == selected else "") + name
                         for pid, name in zip(table["property_id"], table["property"])],
            "Status": [metrics.STATUS_WORDS[s] for s in table["overall"]],
            "RevPAR vs Budget": [cell(s, pct_text(p) + (f" · {money(r)}" if pd.notna(r) else ""))
                                 for s, p, r in zip(table["revpar_status"], table["revpar_pct"], table["revpar"])],
            "GOP vs Budget": [cell(s, pct_text(p)) for s, p in zip(table["gop_status"], table["gop_pct"])],
            "Labor": [cell(s, labor_text(v, p))
                      for s, v, p in zip(table["labor_status"], table["labor_diff"], table["labor_pct"])],
            "Data Quality": [cell(s, note) for s, note in zip(table["data_status"], table["data_note"])],
        })
        status_columns = {"Status": "overall", "RevPAR vs Budget": "revpar_status", "GOP vs Budget": "gop_status",
                          "Labor": "labor_status", "Data Quality": "data_status"}
        css = pd.DataFrame("", index=display.index, columns=display.columns)
        for column, source in status_columns.items():
            css[column] = [f"background-color: {STATUS_STYLE[s][1]}; color: {STATUS_STYLE[s][2]}; font-weight: 600"
                           for s in table[source]]
        event = st.dataframe(
            display.style.apply(lambda _: css, axis=None), hide_index=True, width="stretch",
            on_select="rerun", selection_mode="single-row", key=f"exception_map_{scope_key(scope)}_{month}",
            column_config={"Status": st.column_config.TextColumn("Status", help=STATUS_HELP)})
        if event.selection.rows:
            pid = table["property_id"].iloc[event.selection.rows[0]]
            if pid != selected:
                st.session_state.pending_property = pid
                st.rerun()

        flagged = table[table["overall"] != "good"]
        if not flagged.empty:
            st.markdown("**Details**")
            for _, row in flagged.iterrows():
                status = row["overall"]
                with st.expander(f"{row['property']} · :{STATUS_BADGE[status]}-badge[{metrics.STATUS_WORDS[status]}]"):
                    for reason in row["details"]:
                        st.markdown(f"- {md(reason)}")


PREVIEW_HEIGHT = 300   # every preview box is the same fixed size; imported data fills it, it never grows
PREVIEW_BODY = 212     # the preview text sits in a fixed area, so the "Open ..." links line up across boxes


def forward_panels(store: DataStore, scope: metrics.Scope, ids, month: str) -> None:
    st.subheader("Forward-Looking and Market Context", anchor=False,
                 help="Compact previews. Each opens a detail view. The data comes from separate imports (pace, debt "
                      "service, STR), kept apart from the closed monthly results; nothing is estimated.")
    pace_box, debt_box, market_box = st.columns(3, gap="medium")
    for column, preview, page, label in ((pace_box, lambda: pace_preview(store, ids), "pace", "Open Forward Pace"),
                                         (debt_box, lambda: dscr_preview(store, scope, ids, month), "debt",
                                          "Open Debt Coverage"),
                                         (market_box, lambda: market_preview(store, scope, ids, month), "market",
                                          "Open Market Context")):
        with column, st.container(border=True, height=PREVIEW_HEIGHT):
            with st.container(height=PREVIEW_BODY, border=False):
                preview()
            link(page, label, ":material/open_in_new:")


def pace_preview(store: DataStore, ids) -> None:
    st.markdown("#### :material/trending_up: Forward Pace")
    view = sources.pace_view(store.sources["pace"], store.facts, ids)
    if view is None:
        data_needed("Pace data needed", "Import a pace export with its as-of date and future stay months.")
    else:
        st.markdown(f":blue-badge[As of {date_label(view['as_of'])}]")
        first = view["table"].iloc[0]
        budget = f" · {pct(first['pct_of_budget_nights'], 0)} of budget" if pd.notna(
            first["pct_of_budget_nights"]) else ""
        st.markdown(f"**{month_label(first['stay_month'])}:** {count(first['room_nights'])} room nights on the "
                    f"books{budget}")
        st.caption(f"{len(view['covered'])} of {len(ids)} hotel{'s' if len(ids) != 1 else ''} · "
                   f"{len(view['table'])} stay months · not closed results")


def dscr_preview(store: DataStore, scope: metrics.Scope, ids, month: str) -> None:
    st.markdown("#### :material/account_balance: DSCR")
    table = sources.dscr_view(store.sources["debt"], ids, month)
    known = table[table["dscr"].notna()]
    if known.empty:
        reason = table["note"].iloc[0] if scope.kind == "property" and len(table) else \
            "Import debt service, the agreed cash-flow figure and each loan's minimum DSCR."
        data_needed("Debt data needed", reason)
    elif scope.kind == "property":
        row = known.iloc[0]
        colour = {"review": "red", "good": "green"}.get(row["status"], "gray")
        st.markdown(f"### {row['dscr']:.2f}x &nbsp; :{colour}-badge[{row['note']}]")
        st.caption(f"{row['period']} · minimum "
                   + (f"{row['minimum']:.2f}x (from the debt file)" if pd.notna(row["minimum"]) else "not supplied"))
    else:
        review = int((table["status"] == "review").sum())
        st.markdown(f"### {review} of {len(table)}")
        st.markdown(f"need review: DSCR below minimum · {month_label(month, short=True)}")
        st.caption(f"{len(known)} with DSCR · {int((table['status'] == 'no_threshold').sum())} no minimum · "
                   f"{int(table['dscr'].isna().sum())} no debt data")


def market_preview(store: DataStore, scope: metrics.Scope, ids, month: str) -> None:
    st.markdown("#### :material/query_stats: Market Context")
    view = sources.str_view(store.sources["str"], ids, month)
    hist = view["historical"]
    if view["latest_month"] is None and view["forward"].empty:
        data_needed("STR data needed", "Import STR competitive-set results (historical and, if available, forward).")
    elif hist.empty:
        st.markdown(f":gray-badge[No STR results for {month_label(month, short=True)}]")
        if view["latest_month"]:
            st.caption(f"Latest historical STR month: {month_label(view['latest_month'])}.")
    elif scope.kind == "property":
        row = hist.iloc[0]
        st.markdown(f"### RGI {row['rgi']:.1f}")
        st.markdown(f"MPI {row['mpi']:.1f} · ARI {row['ari']:.1f}")
        st.caption(f"STR historical · {month_label(month)} · report date {date_label(row['report_date'])}")
    else:
        below = int((hist["rgi"] < 100).sum())
        st.markdown(f"### {below} of {len(hist)}")
        st.markdown(f"below fair share (RGI under 100) · {month_label(month, short=True)}")
        st.caption("Historical STR · per hotel, never averaged" +
                   (f" · {len(view['missing'])} without STR" if view["missing"] else ""))


# ------------------------------------------------------------------------------------
# Detail views (opened from the previews; the Show / Month selection carries over)
# ------------------------------------------------------------------------------------

def detail_context(title: str, icon: str):
    """Title, the same Show / Month pickers as the overview, and the selected hotels. None if no data yet."""
    store = current_store()
    head, picker = st.columns([5, 7], vertical_alignment="center")
    head.title(f"{icon} {title}")
    link("overview", "Back to Portfolio Overview", ":material/arrow_back:")
    if store is None:
        st.info("Your local workspace has no property list yet. Open **Data Imports & Checks** to set it up.")
        return None
    months = metrics.months_with_actuals(store.facts) or [metrics.latest_closed_month(date.today())]
    with picker:
        scope, month = filters(store.properties, months)
    ids = metrics.property_ids_in_scope(store.properties, scope)
    head.markdown(f"**{scope.label(store.properties)}** · {len(ids)} hotel{'s' if len(ids) != 1 else ''}")
    if workspace() == "demo":
        st.badge("Demo · fictional data", icon=":material/science:", color="gray")
    return store, scope, ids, month


def names_of(store: DataStore) -> pd.Series:
    return store.properties.set_index("property_id")["property_name"]


def pace_page() -> None:
    context = detail_context("Forward Pace", ":material/trending_up:")
    if context is None:
        return
    store, scope, ids, _ = context
    view = sources.pace_view(store.sources["pace"], store.facts, ids)
    if view is None:
        data_needed("Pace data needed", "No pace export has been imported for these hotels. A supported export has "
                    "one row per hotel, as-of date and future stay month (see Data Imports & Checks).")
        return
    names = names_of(store)
    st.markdown(f"**As of {date_label(view['as_of'])}** · business on the books for future stay months. "
                "Forward-looking: kept apart from closed monthly results, and not tied to the Month picker.")
    if view["missing"]:
        st.caption("No pace report on this date for " + ", ".join(names.get(p, p) for p in view["missing"])
                   + "; they are not included or estimated.")
    table = view["table"].assign(stay_month=lambda t: t["stay_month"].map(month_label),
                                 pct_of_budget_nights=lambda t: t["pct_of_budget_nights"] * 100,
                                 pct_of_budget_revenue=lambda t: t["pct_of_budget_revenue"] * 100)
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "stay_month": "Stay month", "hotels": "Hotels",
        "room_nights": st.column_config.NumberColumn("Room nights on books", format="%,.0f"),
        "room_revenue": st.column_config.NumberColumn("Room revenue on books", format="$%,.0f"),
        "adr": st.column_config.NumberColumn("ADR on books", format="$%.2f"),
        "budget_room_nights": st.column_config.NumberColumn("Budget room nights", format="%,.0f"),
        "pct_of_budget_nights": st.column_config.NumberColumn("% of budget nights", format="%.1f%%"),
        "pct_of_budget_revenue": st.column_config.NumberColumn("% of budget revenue", format="%.1f%%"),
        "pickup_nights": st.column_config.NumberColumn(
            f"Pickup since {date_label(view['previous_as_of'])}" if view["previous_as_of"] else "Pickup",
            format="%+,.0f")})
    st.caption("Budget = the imported monthly budget for the same hotels. % of budget is how much of the month's "
               "budgeted business is already booked; pickup is the change since the previous pace report.")
    by_hotel = store.sources["pace"]
    by_hotel = by_hotel[(by_hotel["as_of_date"] == view["as_of"]) & by_hotel["property_id"].isin(ids)]
    with st.expander("Room nights on the books by hotel"):
        pivot = by_hotel.pivot_table(index="property_id", columns="stay_month", values="room_nights_otb",
                                     aggfunc="sum")
        pivot.index = [names.get(p, p) for p in pivot.index]
        pivot.columns = [month_label(m, short=True) for m in pivot.columns]
        st.dataframe(pivot, width="stretch")


def debt_page() -> None:
    context = detail_context("Debt Coverage", ":material/account_balance:")
    if context is None:
        return
    store, scope, ids, month = context
    table = sources.dscr_view(store.sources["debt"], ids, month)
    if table["debt_service"].isna().all():
        data_needed("Debt data needed", "No debt service has been imported for these hotels. DSCR needs debt service, "
                    "the agreed cash-flow figure and each loan's minimum DSCR (see Data Imports & Checks).")
        return
    st.markdown("**DSCR = cash flow available for debt service ÷ debt service.** Trailing 12 months when the debt "
                "file supplies all 12; otherwise the selected month alone, labelled. Both figures and the minimum "
                "come from the debt file; no numerator or threshold is assumed.")
    names = names_of(store)
    shown = table.assign(property=table["property_id"].map(lambda p: names.get(p, p)),
                         status=table["status"].map({"review": "Review", "good": "At or above minimum",
                                                     "no_threshold": "No minimum supplied", "missing": "No data"}))
    st.dataframe(shown[["property", "status", "dscr", "minimum", "period", "cash_flow", "debt_service", "note"]],
                 hide_index=True, width="stretch", column_config={
                     "property": "Property", "status": "Status",
                     "dscr": st.column_config.NumberColumn("DSCR", format="%.2fx"),
                     "minimum": st.column_config.NumberColumn("Minimum", format="%.2fx"),
                     "period": "Calculation period",
                     "cash_flow": st.column_config.NumberColumn("Cash flow for debt service", format="$%,.0f"),
                     "debt_service": st.column_config.NumberColumn("Debt service", format="$%,.0f"),
                     "note": st.column_config.TextColumn("Note", width="large")})
    st.caption("One row per property. Loans differ, so no portfolio DSCR is calculated.")


def market_page() -> None:
    context = detail_context("Market Context (STR)", ":material/query_stats:")
    if context is None:
        return
    store, scope, ids, month = context
    view = sources.str_view(store.sources["str"], ids, month)
    names = names_of(store)
    if view["latest_month"] is None and view["forward"].empty:
        data_needed("STR data needed", "No STR report has been imported for these hotels (see Data Imports & Checks).")
    st.subheader("Historical Competitive-Set Results", anchor=False)
    st.caption(f"Closed month {month_label(month)} · each hotel against its own competitive set · indexes = hotel ÷ "
               "comp set × 100, never averaged across hotels")
    hist = view["historical"]
    if hist.empty:
        st.caption(f"No historical STR results for {month_label(month)}."
                   + (f" Latest available: {month_label(view['latest_month'])}." if view["latest_month"] else ""))
    else:
        shown = hist.assign(property=hist["property_id"].map(lambda p: names.get(p, p)),
                            report_date=hist["report_date"].map(date_label),
                            occupancy=hist["occupancy"] * 100, comp_occupancy=hist["comp_occupancy"] * 100)
        st.dataframe(shown[["property", "report_date", "occupancy", "comp_occupancy", "adr", "comp_adr", "revpar",
                            "comp_revpar", "mpi", "ari", "rgi"]], hide_index=True, width="stretch", column_config={
            "property": "Property", "report_date": "Report date",
            "occupancy": st.column_config.NumberColumn("Occ", format="%.1f%%"),
            "comp_occupancy": st.column_config.NumberColumn("Comp occ", format="%.1f%%"),
            "adr": st.column_config.NumberColumn("ADR", format="$%.2f"),
            "comp_adr": st.column_config.NumberColumn("Comp ADR", format="$%.2f"),
            "revpar": st.column_config.NumberColumn("RevPAR", format="$%.2f"),
            "comp_revpar": st.column_config.NumberColumn("Comp RevPAR", format="$%.2f"),
            "mpi": st.column_config.NumberColumn("MPI", format="%.1f"),
            "ari": st.column_config.NumberColumn("ARI", format="%.1f"),
            "rgi": st.column_config.NumberColumn("RGI", format="%.1f")})
    if view["missing"] and not hist.empty:
        st.caption("No STR results for " + ", ".join(names.get(p, p) for p in view["missing"]) + ".")

    st.subheader("Forward STR (On the Books)", anchor=False)
    st.caption("Forward-looking occupancy on the books for future stay months, from each hotel's latest forward "
               "report. Not closed results.")
    forward = view["forward"]
    if forward.empty:
        st.caption("No forward STR report imported.")
    else:
        shown = forward.assign(property=forward["property_id"].map(lambda p: names.get(p, p)),
                               report_date=forward["report_date"].map(date_label),
                               period=forward["period"].map(month_label),
                               occupancy=forward["occupancy"] * 100, comp_occupancy=forward["comp_occupancy"] * 100)
        st.dataframe(shown[["property", "report_date", "period", "occupancy", "comp_occupancy", "occupancy_index"]],
                     hide_index=True, width="stretch", column_config={
                         "property": "Property", "report_date": "Report date", "period": "Stay month",
                         "occupancy": st.column_config.NumberColumn("Occ on books", format="%.1f%%"),
                         "comp_occupancy": st.column_config.NumberColumn("Comp occ on books", format="%.1f%%"),
                         "occupancy_index": st.column_config.NumberColumn("Occupancy index", format="%.1f")})


CARD_EVIDENCE = {"changed": "Evidence and sources", "matters": "Evidence, sources and possible causes",
                 "decision": "Evidence and sources"}


def map_ids_for(scope: metrics.Scope, ids, properties: pd.DataFrame) -> list:
    """The exception map lists the selected group, or the whole portfolio otherwise."""
    return list(ids) if scope.kind == "group" else list(properties["property_id"])


def briefing_state(store: DataStore, ids, scope: metrics.Scope, label: str, month: str,
                   warnings: pd.DataFrame) -> dict:
    """The rule-based briefing for the selection and its approval (tied to the exact statements)."""
    statements = briefing.build_briefing(store.facts, store.properties, ids, label, month, warnings)
    fingerprint = briefing.fingerprint(statements)
    return {"statements": statements, "fingerprint": fingerprint,
            "summaries": briefing.card_summaries(store.facts, store.properties, ids, month, warnings),
            "review": store.find_review(scope_key(scope), month, fingerprint)}


def ai_state(store: DataStore, ids, scope: metrics.Scope, label: str, month: str, warnings: pd.DataFrame) -> dict:
    """
    The AI draft held in this browser session for the selection, if it still applies. A draft (and so
    its approval) is discarded when the imports or the selection change. Never calls the model.
    """
    packet = ai_briefing.evidence_packet(store.facts, store.properties, ids, label, month, warnings,
                                         map_ids_for(scope, ids, store.properties))
    context = "|".join([scope_key(scope), month, ",".join(map(str, store.imports["import_id"])),
                        ",".join(map(str, store.source_imports["import_id"]))])
    packet_fp = ai_briefing.packet_fingerprint(packet, context)
    held = st.session_state.get("ai_draft")
    stale = held is not None and held.packet_fingerprint != packet_fp
    if stale:
        st.session_state.pop("ai_draft")
        held = None
    review = store.find_review(f"ai:{scope_key(scope)}", month, held.fingerprint) if held else None
    return {"packet": packet, "packet_fp": packet_fp, "held": held, "stale": stale, "review": review}


AI_DRAFT_LABEL = "AI draft — asset manager review required"


def has_findings(draft) -> bool:
    """A usable AI draft: at least one finding survived validation."""
    return draft is not None and any(draft.sections[key] for key in briefing.SECTIONS)


def briefing_panel(store: DataStore, ids, scope: metrics.Scope, label: str, month: str,
                   warnings: pd.DataFrame) -> None:
    """
    Overview: one Briefing panel. It shows the rule-based summary unless a validated AI draft exists for
    this exact selection, month and imported data (hosted fictional demo only, made only on a click).
    The two have separate approvals; evidence, review and downloads for both are on Review Log.
    """
    rule = briefing_state(store, ids, scope, label, month, warnings)
    allowed = ai_briefing.ai_allowed(MODE["hosted_demo"], workspace())   # never in the local company-data app
    ai = ai_state(store, ids, scope, label, month, warnings) if allowed else None
    with st.container(border=True):
        head, badge = st.columns([1, 2], vertical_alignment="center")
        head.subheader("Briefing", anchor=False)
        notice = None
        if allowed:
            notice = ai_draft_controls(ai, label, month)          # the click is handled before anything is shown
        held = ai["held"] if ai else None
        show_ai = has_findings(held)
        with badge.container(horizontal=True, horizontal_alignment="right"):
            if show_ai:
                ai_status_badge(ai)
            elif rule["review"] is not None:
                st.badge(f"Approved · {rule['review']['reviewer']}", icon=":material/verified:", color="green")
            else:
                st.badge("Draft · asset manager review required", icon=":material/edit_note:", color="orange")
        if ai and ai["stale"]:
            st.info("The AI draft (and any approval of it) was discarded because the imported data or the selection "
                    "changed. Showing the rule-based summary.", icon=":material/restart_alt:")
        if notice:
            st.warning(notice, icon=":material/warning:")

        for column, (key, title) in zip(st.columns(3, gap="medium"), briefing.SECTIONS.items()):
            with column, st.container(border=True):
                st.caption(title)
                if show_ai:
                    items = held.sections[key]
                    if items:
                        st.markdown(ai_bullets(items))
                    else:
                        st.caption("No item passed validation for this section.")
                else:
                    headline, sentence = rule["summaries"][key]
                    st.markdown(f"#### {md(headline)}")
                    st.markdown(md(sentence))
        with st.container(horizontal=True, vertical_alignment="center"):
            if show_ai:
                st.caption(f"{label} · {month_label(month)} · AI draft by {held.model}, {when(held.created_at)}, from "
                           f"verified evidence · {len(held.removed)} claim(s) removed by validation. It needs its own "
                           "approval, separate from the rule-based summary's; evidence and approval are on Review Log.")
                link("review", "Review AI evidence and approve", ":material/fact_check:")
            else:
                st.caption(f"{label} · {month_label(month)} · rule-based summary written by fixed rules from the "
                           "verified figures; no AI. Evidence, sources, approval and download are on Review Log.")
                link("review", "Review evidence and approve", ":material/fact_check:")


def ai_draft_controls(ai: dict, label: str, month: str) -> str | None:
    """Hosted demo: the Draft with AI button. Returns a message when the call or its validation fails."""
    packet = ai["packet"]
    _, model = gemini_settings()
    with st.container(horizontal=True, vertical_alignment="center"):
        clicked = st.button("Draft with AI", icon=":material/auto_awesome:", key=f"ai_button_{ai['packet_fp']}",
                            help="Optional, hosted fictional demo only. The model is called only when you click.")
        st.caption(f"Sends {len(packet['evidence'])} verified evidence items for {label}, {month_label(month)} (not "
                   f"any workbook) to Google Gemini ({model}, free tier); fictional data only, and Google may use "
                   "free-tier requests to improve its products.")
    if not clicked:
        return None
    key, model = gemini_settings()
    try:
        with st.spinner("Asking Gemini for a draft..."):
            raw = ai_briefing.call_gemini(packet, key, model, hosted_demo=MODE["hosted_demo"], workspace=workspace())
    except ai_briefing.AIError as error:
        return error.message
    draft = ai_briefing.validate(raw, packet, model, ai["packet_fp"])
    if not has_findings(draft):
        return (f"The AI draft failed validation: all {len(draft.removed)} claim(s) were removed (missing or unknown "
                "evidence, or numbers not in the cited evidence). The briefing below is unchanged.")
    st.session_state.ai_draft = draft
    st.rerun()


def gemini_settings() -> tuple[str | None, str]:
    """Hosted demo only: the key (and optional model) from Streamlit's secrets, never from project files."""
    try:
        return st.secrets.get("GEMINI_API_KEY"), st.secrets.get("GEMINI_MODEL", ai_briefing.DEFAULT_MODEL)
    except Exception:                                   # no secrets configured at all
        return None, ai_briefing.DEFAULT_MODEL


def ai_status_badge(state: dict) -> None:
    if state["review"] is not None:
        st.badge(f"Approved AI draft · {state['review']['reviewer']}", icon=":material/verified:", color="green")
    elif state["held"] is not None:
        st.badge(AI_DRAFT_LABEL, icon=":material/smart_toy:", color="orange")
    else:
        st.badge("No AI draft yet", icon=":material/smart_toy:", color="gray")


def ai_bullets(items: list) -> str:
    """The validated findings of one section as a short bullet list (the model returns plain sentences)."""
    return "\n".join("- " + ("**Possible cause (unverified):** " if item["kind"] == ai_briefing.POSSIBLE_CAUSE
                             else "") + md(ai_briefing.plain_text(item["text"])) for item in items)


def source_indicator(status: dict) -> None:
    """Overview: one line on the source period and data quality; the full sourcing is on Review Log."""
    quality = (f":orange[:material/warning: {status['warnings']} data-check warning(s)]" if status["warnings"]
               else ":green[:material/check_circle: Data checks passed]")
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.markdown(f":material/fact_check: **Sources:** {month_label(status['month'])} (closed month) · "
                    f"{status['reporting']} of {status['hotels']} hotel{'s' if status['hotels'] != 1 else ''} "
                    f"reporting · {quality}")
        link("review", "Sources and checks in Review Log", ":material/arrow_forward:")


# ------------------------------------------------------------------------------------
# Page: Review Log (Calculation & Evidence Log)
# ------------------------------------------------------------------------------------

def review_page() -> None:
    store = current_store()
    head, picker = st.columns([7, 5], vertical_alignment="top")    # room for the title on one line
    head.title("Calculation & Evidence Log")
    if store is None:
        st.info("Your local workspace has no property list yet. Open **Data Imports & Checks** to set it up.")
        return
    months = metrics.months_with_actuals(store.facts)
    if not months:
        st.info("No monthly actuals have been imported yet. Open **Data Imports & Checks** to import a file.")
        return
    with picker:
        scope, month = filters(store.properties, months)          # same selection as Portfolio Overview
    ids = metrics.property_ids_in_scope(store.properties, scope)
    label = scope.label(store.properties)
    warnings = store.active_warnings()
    scoped = warnings[warnings["property_id"].isin(ids) & (warnings["month"] == month)]
    summary = metrics.month_summary(store.facts, ids, month)
    status = data_status(store, ids, month, months, summary, scoped)
    head.markdown(f":material/calendar_month: **{month_label(month)}**  \n**{label}** · current view")
    st.caption("A trace of the figures and evidence shown for this selection, rebuilt from the app's current data "
               "each time the page opens. It is not a permanent audit trail."
               + (" Hosted demo: everything here lasts for your browser session only." if MODE["hosted_demo"]
                  else ""))
    if workspace() == "demo":
        st.badge("Demo · fictional data", icon=":material/science:", color="gray")

    trace = trace_mod.calculation_trace(store.facts, store.properties, ids, month)
    gaps = sum(bool(r["Missing inputs"]) for r in trace["rows"])
    with st.expander(f"**1. How the numbers were calculated** · {len(trace['rows'])} key results"
                     + (f" · {gaps} with missing inputs" if gaps else " · no missing inputs")):
        calculation_section(store, ids, month, trace)
    prov = trace_mod.provenance(store, ids, month)
    with st.expander(f"**2. Sources and data checks** · {len(prov['files'])} file(s) · "
                     f"{len(prov['warnings'])} warning(s)"):
        sources_section(store, ids, month, status, prov, trace)
    rule = briefing_state(store, ids, scope, label, month, warnings)
    ai = ai_state(store, ids, scope, label, month, warnings) if ai_briefing.ai_allowed(
        MODE["hosted_demo"], workspace()) else None
    rule_word = "approved" if rule["review"] is not None else "draft"
    ai_word = ("" if ai is None else " · AI: " + ("approved" if ai["review"] is not None
                                                 else "draft" if ai["held"] is not None else "none yet"))
    with st.expander(f"**3. Briefing evidence and review** · rule-based: {rule_word}{ai_word}", expanded=True):
        evidence_section(store, scope, label, month, status, rule, ai)


def calculation_section(store: DataStore, ids, month: str, trace: dict) -> None:
    a, b = trace["summary"]["actual"], trace["summary"]["budget"]
    if a is None:
        st.info(f"No actual figures for this selection in {month_label(month)}, so nothing was calculated.")
        return
    st.caption("Results come from the dashboard's own calculations (hotels' figures added up first, then rates "
               "divided). GOP and EBITDA are the reported figures, checked against their lines in the EBITDA "
               "waterfall below. **Missing** means not supplied, never zero. " + trace["budget_note"])
    table = pd.DataFrame(trace["rows"]).drop(columns="key")
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Formula": st.column_config.TextColumn(width="medium"),
        "Actual (inputs → result)": st.column_config.TextColumn(width="medium"),
        "Budget (inputs → result)": st.column_config.TextColumn(width="medium")})

    bridge = trace["bridge"]
    st.markdown("**RevPAR variance split**")
    if bridge is None:
        st.caption("Not available: needs actual and budget occupancy and ADR.")
    else:
        st.caption(md(
            f"Occupancy effect = ({pct(a['occupancy'])} − {pct(b['occupancy'])}) × budget ADR {money(b['adr'])} = "
            f"{signed_money(bridge['occupancy_effect'])}. Rate effect = ({money(a['adr'])} − {money(b['adr'])}) × "
            f"actual occupancy {pct(a['occupancy'])} = {signed_money(bridge['rate_effect'])}. Together "
            f"{signed_money(bridge['total'])} = the RevPAR variance."))

    st.markdown("**EBITDA waterfall**")
    waterfall = trace["waterfall"]
    if waterfall is None:
        st.caption("Not available: the imported figures don't include every expense group.")
    else:
        steps = waterfall["steps"][["step", "amount", "end"]].rename(
            columns={"step": "Step", "amount": "Amount", "end": "Running total"})
        st.dataframe(steps, hide_index=True, width="stretch", column_config={
            "Amount": st.column_config.NumberColumn(format="$%,.0f"),
            "Running total": st.column_config.NumberColumn(format="$%,.0f")})
        for name, calculated, supplied, gap in waterfall["checks"]:
            if supplied is None:
                st.caption(md(f"{name}: not supplied by every hotel; the bar is calculated from the lines "
                              f"({money(calculated, 0)}) and can't be reconciled."))
            else:
                st.caption(md(f"{name}: the lines give {money(calculated, 0)}; reported {money(supplied, 0)} · "
                              + ("✓ reconciles" if not gap else f"✗ difference {money(gap, 0)}, shown as its own step")))

    st.markdown("**Inputs by hotel** (as imported)")
    st.dataframe(trace_mod.inputs_by_hotel(store.facts, store.properties, ids, month), hide_index=True,
                 width="stretch", column_config={
                     "scenario": "Scenario", "property": "Property", "import_id": st.column_config.NumberColumn(
                         "Import #", format="%d"),
                     **{c: st.column_config.NumberColumn(d.label(c), format="%,.0f")
                        for c in trace_mod.INPUT_COLUMNS}})
    st.caption("Blank = not supplied by that hotel's file (not zero). Totals above add these up per scenario.")


def sources_section(store: DataStore, ids, month: str, status: dict, prov: dict, trace: dict) -> None:
    st.markdown(f"**Reporting period:** {month_label(month)} (closed month) · **Hotels reporting:** "
                f"{status['reporting']} of {status['hotels']}")
    for *_, detail in status["notices"]:
        st.caption(md(detail))
    st.markdown("**Files supplying these figures**")
    if prov["files"].empty:
        st.caption("No imported figures for this selection and month.")
    else:
        files = prov["files"].assign(**{"Imported at": prov["files"]["Imported at"].map(when)})
        st.dataframe(files, hide_index=True, width="stretch")
    st.markdown("**Coverage by hotel**")
    st.dataframe(prov["coverage"], hide_index=True, width="stretch")
    st.markdown("**Data-check warnings**")
    if prov["warnings"].empty:
        st.caption("None for these figures.")
    else:
        names = store.imports.set_index("import_id")["file_name"]
        shown = prov["warnings"].assign(file=prov["warnings"]["import_id"].map(names))
        st.dataframe(shown[["check", "message", "property_id", "sheet", "excel_row", "file"]], hide_index=True,
                     width="stretch", column_config={
                         "check": "Check", "message": st.column_config.TextColumn("Details", width="large"),
                         "property_id": "Hotel", "sheet": "Sheet",
                         "excel_row": st.column_config.NumberColumn("Excel row", format="%d"), "file": "File"})
    st.markdown("**Reconciliation**")
    waterfall = trace["waterfall"]
    if waterfall is None:
        st.caption("Not checked: the waterfall needs every expense group.")
    else:
        st.caption("EBITDA waterfall " + ("reconciles to the reported GOP and EBITDA." if waterfall["reconciles"]
                                          else "does not fully reconcile; see section 1."))
    st.markdown("**Separate sources in this view**")
    extra = trace_mod.source_imports_in_view(store, ids, month)
    if extra.empty:
        st.caption("None (channel mix, pace, STR and debt files are imported separately).")
    else:
        st.dataframe(extra.assign(**{"Imported at": extra["Imported at"].map(when)}), hide_index=True,
                     width="stretch")
    st.markdown("**What can't be traced**")
    st.markdown("\n".join(f"- {line}" for line in trace_mod.UNTRACEABLE))


def evidence_section(store: DataStore, scope: metrics.Scope, label: str, month: str, status: dict, rule: dict,
                     ai: dict | None) -> None:
    review = rule["review"]
    head, badge = st.columns([3, 2], vertical_alignment="center")
    head.markdown("**Rule-based briefing**")
    with badge.container(horizontal=True, horizontal_alignment="right"):
        if review is not None:
            st.badge(f"Approved · {review['reviewer']}", icon=":material/verified:", color="green")
        else:
            st.badge("Draft · asset manager review required", icon=":material/edit_note:", color="orange")
    st.caption("Written by fixed rules from the calculations in section 1. Each statement shows its source figures.")
    for key, title in briefing.SECTIONS.items():
        st.markdown(f"*{title}*")
        for s in [s for s in rule["statements"] if s.section == key]:
            tag = "Possible cause (unverified)" if s.kind == briefing.POSSIBLE_CAUSE else "Fact"
            st.markdown(f"- **{tag}:** {md(s.text)}  \n  :gray[Source: {md(s.source)}]")
    if review is not None:
        notes = "" if pd.isna(review["notes"]) else str(review["notes"])
        st.success(f"Approved for sharing by **{review['reviewer']}** on {when(review['reviewed_at'])}.")
        text = briefing.to_markdown(rule["statements"], label, month, status["note"], review["reviewer"],
                                    when(review["reviewed_at"]), notes, rule["summaries"])
        st.download_button("Download approved briefing (.md)", text, mime="text/markdown", type="primary",
                           file_name=f"briefing_{re.sub(r'[^A-Za-z0-9]+', '_', label).strip('_')}_{month}.md",
                           icon=":material/download:")
    else:
        with st.form(f"review_{rule['fingerprint']}"):
            st.markdown("**Asset Manager Review**")
            name_col, notes_col = st.columns([1, 2])
            reviewer = name_col.text_input("Reviewer name", key="rule_reviewer")
            notes = notes_col.text_input("Notes to add (optional)", key="rule_notes")
            confirmed = st.checkbox("I have checked these statements against the source figures. "
                                    "Possible causes remain unverified.", key="rule_confirmed")
            submitted = st.form_submit_button("Approve for sharing", type="primary")
        if submitted:
            if not reviewer.strip() or not confirmed:
                st.error("Enter your name and tick the box to approve.")
            else:
                store.add_review(scope_key(scope), month, rule["fingerprint"], reviewer.strip(), notes.strip())
                st.rerun()
    st.caption("The approval is tied to these exact statements: if the figures change, the briefing returns to draft.")

    st.divider()
    head, badge = st.columns([3, 2], vertical_alignment="center")
    head.markdown("**AI draft**")
    if ai is None:
        st.caption("Not available here: the local company app never calls an outside AI model.")
        return
    with badge.container(horizontal=True, horizontal_alignment="right"):
        ai_status_badge(ai)
    if ai["stale"]:
        st.info("The previous AI draft (and any approval of it) was discarded because the imported data or the "
                "selection changed.", icon=":material/restart_alt:")
    held, packet = ai["held"], ai["packet"]
    if held is None:
        st.caption("No AI draft for this view. Use **Draft with AI** in the Briefing panel on Portfolio Overview to create one; "
                   "opening this page never calls the model.")
        return
    evidence = {e["id"]: e for e in packet["evidence"]}
    st.caption(f"Drafted {when(held.created_at)} by {held.model}. Citations (E1, E2 ...) point to the app's "
               "imported data and calculated evidence in the packet below, not to independent research by the "
               f"model. {held.numbers_supported} of {held.numbers_total} numbers in the reply matched the evidence "
               "they cite.")
    for key, title in briefing.SECTIONS.items():
        st.markdown(f"*{title}*")
        items = held.sections[key]
        if not items:
            st.caption("No finding passed validation for this section.")
        for item in items:
            tag = "Possible cause (unverified)" if item["kind"] == ai_briefing.POSSIBLE_CAUSE else "Finding"
            cited = "  \n".join(f"  :gray[{e}: {md(evidence[e]['text'])} · Source: "
                                f"{md(evidence[e]['source'] or 'calculated evidence')}]" for e in item["evidence"])
            st.markdown(f"- **{tag}:** {md(item['text'])}  \n{cited}")
    st.markdown("**Removed by validation**")
    if not held.removed:
        st.caption("None.")
    for section, text, reason in held.removed:
        st.markdown(md(f"- *{briefing.SECTIONS[section]}:* {text} :gray[({reason})]"))
    st.markdown("**Evidence packet sent to the model**")
    st.json(packet, expanded=False)

    review = ai["review"]
    if review is not None:
        notes = "" if pd.isna(review["notes"]) else str(review["notes"])
        st.success(f"AI draft approved by **{review['reviewer']}** on {when(review['reviewed_at'])}.")
        text = ai_briefing.draft_markdown(held, packet, review["reviewer"], when(review["reviewed_at"]), notes)
        st.download_button("Download approved AI draft (.md)", text, icon=":material/download:", mime="text/markdown",
                           file_name=f"ai_briefing_{re.sub(r'[^A-Za-z0-9]+', '_', label).strip('_')}_{month}.md")
    else:
        with st.form(f"ai_review_{held.fingerprint}"):
            st.markdown("**Asset Manager Review of the AI Draft**")
            name_col, notes_col = st.columns([1, 2])
            reviewer = name_col.text_input("Reviewer name", key="ai_reviewer")
            notes = notes_col.text_input("Notes to add (optional)", key="ai_notes")
            confirmed = st.checkbox("I have checked each item against its cited evidence. Possible causes "
                                    "remain unverified.", key="ai_confirmed")
            submitted = st.form_submit_button("Approve AI draft", type="primary")
        if submitted:
            if not reviewer.strip() or not confirmed:
                st.error("Enter your name and tick the box to approve.")
            else:
                store.add_review(f"ai:{scope_key(scope)}", month, held.fingerprint, reviewer.strip(), notes.strip())
                st.rerun()
    st.caption("The AI draft and its approval are discarded when the imports or the selection change.")


def ai_evaluation_page() -> None:
    """Hosted fictional demo only: the AI draft vs the rule-based briefing on four fictional cases."""
    st.title("AI Briefing Evaluation")
    if not ai_briefing.ai_allowed(MODE["hosted_demo"], "demo"):
        st.info("Only available in the hosted fictional demo. The local company app never calls an outside model.")
        return
    key, model = gemini_settings()
    st.markdown(
        f"Runs the same four **fictional** cases through the AI draft ({model}) and the rule-based briefing: a "
        "normal month, an adverse month, a month with one hotel's actuals missing, and a month where one hotel's "
        "EBITDA doesn't reconcile. Uses 4 Gemini requests.")
    if st.button("Run evaluation (4 Gemini calls)", type="primary", icon=":material/science:"):
        with st.spinner("Running four fictional cases..."):
            st.session_state.ai_eval = ai_eval.run(
                lambda packet: ai_briefing.call_gemini(packet, key, model, hosted_demo=MODE["hosted_demo"],
                                                       workspace="demo"), model)
    results = st.session_state.get("ai_eval")
    if not results:
        return
    st.subheader("AI Draft", anchor=False)
    st.dataframe(pd.DataFrame([r["score"] for r in results]), hide_index=True, width="stretch")
    st.caption("Numbers correct = numbers in the raw reply found in the evidence they cite. Unsupported claims = "
               "items removed by validation. Decision coverage = decision evidence cited in 'What Needs a "
               "Decision'. Hedged facts = fact items using cause language (should be 0; they are relabelled).")
    st.subheader("Rule-Based Briefing (Reference)", anchor=False)
    st.dataframe(pd.DataFrame([r["baseline"] for r in results]), hide_index=True, width="stretch")
    st.caption("Rule-based statements are built from the calculations, so every number is exact and every "
               "decision item is covered by construction.")
    for r in results:
        with st.expander(f"{r['score']['case']}: {r['score']['result']}"):
            if r.get("error"):
                st.warning(r["error"])
                continue
            for key_, title in briefing.SECTIONS.items():
                st.markdown(f"**{title}**")
                for item in r["draft"].sections[key_]:
                    prefix = "Possible cause (unverified): " if item["kind"] == ai_briefing.POSSIBLE_CAUSE else ""
                    st.markdown(md(f"- {prefix}{item['text']} ({', '.join(item['evidence'])})"))
            for section, text, reason in r["draft"].removed:
                st.caption(md(f"Removed ({briefing.SECTIONS[section]}): {text} ({reason})"))


# ------------------------------------------------------------------------------------
# Page 2: Data Imports & Checks
# ------------------------------------------------------------------------------------

ROUTES = {   # monthly upload routes: which sheets each one reads (the importer ignores the others)
    "actuals": {"sheets": ["Actual"], "label": "Upload Monthly Actuals", "what": "Actuals"},
    "budget": {"sheets": ["Budget"], "label": "Upload Budget", "what": "Budget"},
    "combined": {"sheets": None, "label": "Upload combined Actual + Budget workbook", "what": "Actuals and budget"},
}
ACTUALS_STATUS = {"Complete": "good", "Partial": "watch", "Not imported": "review", "Not closed yet": "missing",
                  "Imported early": "watch"}
UPLOADED_MEANS = ("Choosing a file only **previews** it. Nothing is imported until it passes the checks and you "
                  "click **Import**.")
TILES = {   # the six imports, in page order: (title, icon)
    "actuals": ("Monthly Actuals", ":material/calendar_month:"),
    "budget": ("Budget", ":material/request_quote:"),
    "pace": ("Forward Pace", ":material/trending_up:"),
    "debt": ("Debt Service / DSCR", ":material/account_balance:"),
    "str": ("STR / Market Context", ":material/query_stats:"),
    "channel_mix": ("Revenue Mix", ":material/pie_chart:"),
}


def imports_page() -> None:
    store = current_store()
    head, year_area = st.columns([3, 1], vertical_alignment="bottom")
    head.title("Data Imports & Checks")
    if store is None:
        local_setup_help()
        return
    for problem in st.session_state.get("property_problems", []) if workspace() == "local" else []:
        st.warning(f"properties.csv: {problem}")
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"), icon=":material/check_circle:")
    years = coverage.years_available(store)
    if "next_import_year" in st.session_state:          # set by an import: show the year just imported
        st.session_state.import_year = st.session_state.pop("next_import_year")
    if st.session_state.get("import_year") not in years:
        st.session_state.import_year = coverage.default_year(store)
    year = year_area.selectbox("Reporting year", years, key="import_year")
    if workspace() == "demo":
        st.caption(":material/school: **Class Demo — Fictional Data Only.**")
        sample_tiles(store)
    else:
        st.caption("See what is loaded and what is missing, and upload each type of file in its own place. "
                   "Imported data is kept on this computer in local_data/.")

    actuals_section(store, year)
    budgets_section(store, year)
    supporting_section(store)
    detail_expanders(store)
    if st.session_state.get("import_dialog"):            # one dialog at a time, opened by a card or tile button
        import_dialog()


# --- The review dialog (sample files and chosen files alike) ---------------------------------

def open_dialog(kind: str, mode: str) -> None:
    """Button callback: open the review dialog for one import ('sample' file or 'upload' a file)."""
    st.session_state.import_dialog = {"kind": kind, "mode": mode}


def close_dialog() -> None:
    st.session_state.pop("import_dialog", None)


def sample_tiles(store: DataStore) -> None:
    """Demo only: one tile per import, each opening a preview of its fictional sample file."""
    with st.container(border=True):
        head, zip_area = st.columns([3, 1], vertical_alignment="center")
        head.markdown("**Sample data** · six fictional hotels (H01 to H06)")
        head.caption("**Load sample** opens a preview; nothing is imported until you click **Import**. Imports stay "
                     "in this browser session and are not saved.")
        zip_area.download_button("All sample files (.zip)", sample_files_zip(), key="sample_zip",
                                 file_name="hotel_dashboard_fictional_samples.zip", icon=":material/download:")
        kinds = list(TILES)
        for row in (kinds[:3], kinds[3:]):
            for column, kind in zip(st.columns(3, gap="small"), row):
                title, icon = TILES[kind]
                badge, detail = tile_status(store, kind)
                with column, st.container(border=True):
                    st.markdown(f"{icon} **{title}**")
                    st.markdown(badge)
                    st.button("Load sample", key=f"sample_{kind}", icon=":material/science:", width="stretch",
                              on_click=open_dialog, args=(kind, "sample"))
                    st.caption(detail)


def tile_status(store: DataStore, kind: str) -> tuple[str, str]:
    """A badge and one line for a sample tile: what is loaded now, honestly (preloaded or imported)."""
    first_file = DEMO_SAMPLES[kind][0][0]
    if kind == "actuals":
        sample_month = "2026-08"
        status = coverage.actuals_status(store, int(sample_month[:4])).set_index("month")
        complete = [m for m in status.index if status.loc[m, "Status"] == "Complete"]
        span = (f"{month_label(complete[0], short=True)[:3]} – {month_label(complete[-1], short=True)}"
                if complete else "none")
        if status.loc[sample_month, "Status"] == "Complete":
            return ":green-badge[:material/check_circle: August imported]", f"Actuals loaded: {span}."
        return (":gray-badge[Jan – Jul preloaded]",
                f"Actuals loaded: {span}. Sample: the August 2026 close, not imported yet.")
    if kind == "budget":
        grid_2026 = coverage.budget_coverage(store, 2026)
        grid_2027 = coverage.budget_coverage(store, 2027)
        full_2026 = int((grid_2026["Months"] == "12 of 12").sum())
        full_2027 = int((grid_2027["Months"] == "12 of 12").sum())
        badge = (":gray-badge[2026 budget preloaded]" if not full_2027 else
                 ":green-badge[:material/check_circle: 2027 budget imported]" if full_2027 == len(grid_2027) else
                 f":orange-badge[2027 budget: {full_2027} of {len(grid_2027)} hotels]")
        return badge, (f"2026 budget: {full_2026} of {len(grid_2026)} hotels, preloaded. 2027: {full_2027} of "
                       f"{len(grid_2027)} hotels. Re-importing 2026 asks before replacing.")
    state = coverage.source_status(store, kind)
    if not state["loaded"]:
        return ":gray-badge[Not loaded]", f"Sample: {first_file}."
    total = len(store.properties)
    return (":green-badge[:material/check_circle: Loaded]",
            f"{len(state['covered'])} of {total} hotels · latest file {state['latest_file']}.")


def confirm_dummy_data() -> None:
    """Hosted demo: the visitor confirms the file is fictional before an upload control appears."""
    if not MODE["hosted_demo"]:
        return
    st.caption("Runs on Streamlit's servers: the file is read in memory for this browser session only and "
               "nothing is saved. Only rows for the six fictional hotels (H01 to H06) are accepted, and .xlsm "
               "workbooks are refused.")
    if "hosted_dummy_ok" not in st.session_state:              # remembered while this browser session lasts
        st.session_state.hosted_dummy_ok = bool(st.session_state.get("dummy_confirmed"))
    st.checkbox("I confirm this file contains fictional or dummy data only.", key="hosted_dummy_ok")
    st.session_state.dummy_confirmed = st.session_state.hosted_dummy_ok


def choose_upload(route: str) -> tuple[str, bytes] | None:
    """The upload control inside the dialog. Returns the chosen file, or None."""
    confirm_dummy_data()
    if not uploads_allowed():
        return None
    if route in ROUTES:
        label, types = ROUTES[route]["label"], ["xlsx"] if MODE["hosted_demo"] else ["xlsx", "xlsm"]  # hosted: no .xlsm
    else:
        label, types = f"Upload {SUPPORTING[route][0]} (.xlsx or .csv)", ["xlsx", "csv"]
    uploaded = st.file_uploader(label, type=types, help=upload_help(),
                                key=f"uploader_{route}_{st.session_state.get('uploader_round', 0)}")
    if uploaded is None:
        st.caption(UPLOADED_MEANS)
        return None
    return uploaded.name, uploaded.getvalue()


@st.dialog("Review and import", width="large", on_dismiss=close_dialog)
def import_dialog() -> None:
    """File choice, preview, checks, any replacement warning, and the Import button, for every route."""
    request = st.session_state.get("import_dialog") or {}
    kind, mode = request.get("kind"), request.get("mode")
    store = current_store()
    if store is None or kind is None:
        return
    chosen, sample = None, False
    if mode == "sample":
        options = DEMO_SAMPLES[kind]
        st.markdown(f"{TILES[kind][1]} **{TILES[kind][0]}** · fictional sample file")
        index = 0
        if len(options) > 1:
            labels = [label for _, label, _ in options]
            pick = st.radio("Sample file", labels, key=f"dialog_sample_{kind}",
                            captions=[file_name for file_name, _, _ in options])
            index = labels.index(pick)
        file_name, _, route = options[index]
        chosen, sample = (file_name, (SAMPLE_DIR / file_name).read_bytes()), True
    else:
        route = kind
        st.markdown(f"**{ROUTES[route]['label'] if route in ROUTES else 'Upload ' + SUPPORTING[route][0]}**")
        chosen = choose_upload(route)
    if chosen is None:
        if st.button("Cancel", key="cancel_import", icon=":material/close:"):
            close_dialog()
            st.rerun()
        return
    review_file(store, route, *chosen, sample=sample)


def review_file(store: DataStore, route: str, file_name: str, raw: bytes, sample: bool = False) -> None:
    """Preview, checks, replacement warning and Import for one file: a monthly route or a supporting source."""
    monthly = route in ROUTES
    if monthly:
        result = read_monthly_workbook(raw, file_name, store.properties, scenarios=ROUTES[route]["sheets"])
        what = ROUTES[route]["what"]
    else:
        result = sources.read_source_file(route, raw, file_name, store.properties)
        what = sources.SOURCES[route].title
    st.markdown(f"**Preview (not imported yet): `{file_name}`** · {what}"
                + (" · :gray-badge[Fictional sample file]" if sample else ""))
    if monthly:
        if result.ignored_sheets:
            st.info(f"This file also has a **{' and '.join(result.ignored_sheets)}** sheet. It will **not** be "
                    f"imported here; use the {'Budget' if 'Budget' in result.ignored_sheets else 'Monthly Actuals'} "
                    "upload or the combined-workbook route for it.", icon=":material/info:")
        if not result.rows.empty:
            overview = (result.rows.groupby("scenario")
                        .agg(hotels=("property_id", "nunique"), rows=("month", "size"),
                             months=("month", lambda m: ", ".join(month_label(x, short=True)
                                                                  for x in sorted(m.unique()))))
                        .reset_index())
            if route == "combined":
                st.caption("Both sheets found in this file will be imported: "
                           + "; ".join(f"{r.scenario} ({r.rows} rows)" for r in overview.itertuples()) + ".")
            st.dataframe(overview, hide_index=True)
    elif not result.rows.empty:
        periods = result.rows[coverage.PERIOD_FIELD[route]].dropna().astype(str)
        st.caption(f"{len(result.rows)} row(s) for {result.rows['property_id'].nunique()} hotel(s); periods "
                   f"{periods.min()[:10]} to {periods.max()[:10]}.")

    errors, warnings = result.errors, result.warnings
    if errors:
        st.error(f"{len(errors)} error(s). Import is disabled until they are fixed in the file.",
                 icon=":material/block:")
    if warnings:
        st.warning(f"{len(warnings)} warning(s). The figures can be imported but will be flagged.",
                   icon=":material/warning:")
    if not errors and not warnings:
        st.success("All checks passed. Not imported yet: click Import.", icon=":material/check_circle:")
    if result.issues:
        columns = ["severity", "check", "message", "sheet", "excel_row", "property_id", "month"]
        issues = pd.DataFrame([vars(i) for i in result.issues])
        st.dataframe(issues[columns if monthly else columns[:3] + columns[4:6]], hide_index=True, width="stretch",
                     column_config={
                         "severity": "Severity", "check": "Check",
                         "message": st.column_config.TextColumn("Details", width="large"),
                         "sheet": "Sheet", "excel_row": st.column_config.NumberColumn("Excel row", format="%d"),
                         "property_id": "Hotel", "month": "Month"})

    replace = False
    if monthly:
        earlier = store.earlier_import_of_same_file(result.file_hash)
        if earlier is not None:
            st.info(f"This exact file was already imported on {when(earlier['imported_at'])}.")
        replacements = coverage.replacement_preview(store, result.rows)
        if not replacements.empty:
            st.warning(f"**{len(replacements)} property-month(s) in this file are already loaded.** Importing would "
                       "replace exactly these rows (nothing else changes):", icon=":material/content_copy:")
            st.dataframe(replacements.assign(Imported=replacements["Imported"].map(lambda t: when(t) if t else "")),
                         hide_index=True, width="stretch", column_config={
                             c: st.column_config.NumberColumn(format="$%,.0f") for c in
                             ("Total revenue now", "Total revenue in new file", "GOP now", "GOP in new file")})
            replace = st.checkbox("Replace the previously imported figures for these property-months",
                                  key=f"replace_{route}")
        overlap = not replacements.empty
    else:
        duplicates = store.source_duplicates(route, result.rows)
        if not duplicates.empty:
            st.warning(f"{len(duplicates)} record(s) in this file were already imported. Importing would replace "
                       "them.", icon=":material/content_copy:")
            replace = st.checkbox("Replace the previously imported records", key=f"replace_{route}")
        overlap = not duplicates.empty

    blocked = bool(errors) or result.rows.empty or (overlap and not replace)
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if st.button("Cancel", key="cancel_import", icon=":material/close:"):
            close_dialog()
            st.rerun()
        if st.button("Import", type="primary", disabled=blocked, icon=":material/done:", key=f"import_{route}"):
            if monthly:
                import_id = store.add_import(result, replace=replace)
                st.session_state.flash = (f"Imported {file_name} as import #{import_id} ({len(result.rows)} rows, "
                                          f"{len(warnings)} warning(s)). It passed validation and now shows in the "
                                          "status.")
                st.session_state.next_import_year = int(max(result.rows["month"])[:4])
                if ai_briefing.ai_allowed(MODE["hosted_demo"], workspace()):
                    st.session_state.flash += (" **Draft with AI** is available in the Briefing panel "
                                               "on Portfolio Overview.")
            else:
                import_id = store.add_source_import(route, result, replace=replace)
                st.session_state.flash = (f"Imported {file_name} ({what}) as source import #{import_id}. It passed "
                                          "validation and now shows on its card.")
            st.session_state.uploader_round = st.session_state.get("uploader_round", 0) + 1
            close_dialog()
            st.rerun()


# --- 1. Monthly Financials: Actuals ------------------------------------------------------------

def monthly_upload_card(route: str, template_sheets) -> None:
    """Template and the Upload / Import button for one monthly route."""
    spec = ROUTES[route]
    with st.container(border=True):
        st.markdown(f"**{spec['label']}**")
        sheet_text = ("Reads only the **Actual** sheet." if route == "actuals" else
                      "Reads only the **Budget** sheet." if route == "budget" else
                      "Reads **both** the Actual and the Budget sheet.")
        st.caption(f"Excel in the monthly template format. {sheet_text} {UPLOADED_MEANS}")
        with st.container(horizontal=True, gap="small"):
            st.button("Upload / Import", icon=":material/upload:", key=f"open_{route}", on_click=open_dialog,
                      args=(route, "upload"))
            st.download_button(f"{spec['what']} template (.xlsx)", monthly_template_bytes(template_sheets),
                               file_name=f"TEMPLATE_{route}.xlsx", icon=":material/download:",
                               key=f"template_{route}")


def actuals_section(store: DataStore, year: int) -> None:
    st.subheader("1. Monthly Financials — Actuals", anchor=False)
    status = coverage.actuals_status(store, year)
    closed = status[status["Status"] != "Not closed yet"]
    complete = int((closed["Status"] == "Complete").sum())
    left, right = st.columns([2, 3], gap="medium")
    with left:
        monthly_upload_card("actuals", ["Actual"])
    with right, st.container(border=True):
        st.markdown(f"**{year} at a glance**")
        reported = status["Properties reported"].str.split(" of ").str[0].astype(int)
        latest = status.loc[reported > 0, "Month"]
        warnings_open = int(status["Open warnings"].sum())
        st.markdown(f"- **Closed months with complete actuals:** {complete} of {len(closed)}  \n"
                    f"- **Latest month with actuals:** {latest.iloc[-1] if len(latest) else 'none yet'}  \n"
                    f"- **Partial or missing closed months:** "
                    + (", ".join(closed.loc[closed["Status"] != "Complete", "Month"]) or "none") + "  \n"
                    f"- **Open warnings on {year} actuals:** {warnings_open}")
        st.caption("Click a month in the table below for which file supplied each property, and any warnings.")

    shown = status.drop(columns="month").assign(Imported=status["Imported"].map(lambda t: when(t) if t else ""))
    fills = shown["Status"].map(lambda s: STATUS_STYLE[ACTUALS_STATUS[s]])
    css = pd.DataFrame("", index=shown.index, columns=shown.columns)
    css["Status"] = [f"background-color: {f[1]}; color: {f[2]}; font-weight: 600" for f in fills]
    event = st.dataframe(shown.style.apply(lambda _: css, axis=None), hide_index=True, width="stretch",
                         on_select="rerun", selection_mode="single-row", key=f"actuals_status_{year}",
                         column_config={"Missing": st.column_config.TextColumn(width="medium"),
                                        "Open warnings": st.column_config.NumberColumn(format="%d")})
    if event.selection.rows:
        month = status["month"].iloc[event.selection.rows[0]]
        details = coverage.month_details(store, month)
        with st.container(border=True):
            st.markdown(f"**{month_label(month)}: actuals by property**")
            st.dataframe(details["hotels"].assign(Imported=details["hotels"]["Imported"].map(
                lambda t: when(t) if t else "")), hide_index=True, width="stretch")
            if details["warnings"].empty:
                st.caption("No open data-check warnings for this month's actuals.")
            else:
                st.dataframe(details["warnings"][["check", "message", "property_id", "excel_row"]], hide_index=True,
                             width="stretch", column_config={
                                 "check": "Check", "message": st.column_config.TextColumn("Details", width="large"),
                                 "property_id": "Hotel",
                                 "excel_row": st.column_config.NumberColumn("Excel row", format="%d")})

    with st.expander("Have one workbook with both Actual and Budget sheets?"):
        st.caption("Use this route to import both sheets at once (the original combined template). The preview "
                   "lists both sheets before anything is imported; existing budget months still need the "
                   "replacement confirmation.")
        monthly_upload_card("combined", None)


# --- 2. Budgets -------------------------------------------------------------------------------

def budgets_section(store: DataStore, year: int) -> None:
    st.subheader("2. Budgets", anchor=False)
    left, right = st.columns([2, 3], gap="medium")
    with left:
        monthly_upload_card("budget", ["Budget"])
    with right, st.container(border=True):
        st.markdown("**How budgets are stored**")
        st.markdown(
            "- One portfolio workbook **or** separate workbooks (for example one per property group) both work: "
            "each budget row is stored by **scenario + property + month**.\n"
            "- A file that overlaps budget months already loaded shows **exactly which property-months it "
            "would replace** and needs your confirmation. Budgets are never combined or overwritten silently.\n"
            "- Only the **Budget** sheet is read here; Actuals are never imported through this control.\n"
            "- *Planned:* keeping several budget versions (such as a reforecast) for the same property-month "
            "needs a future data-model change; today the latest confirmed import replaces the earlier one.")

    properties = store.properties
    groups = ["All properties"] + [f"{d.label(c)}: {g}" for c in d.classification_columns(properties.columns)
                                   for g in sorted(properties[c].unique())]
    pick = st.selectbox("Budget coverage for", groups, key="budget_group")
    ids = list(properties["property_id"])
    if pick != "All properties":
        label, group = pick.split(": ", 1)
        column = next(c for c in d.classification_columns(properties.columns) if d.label(c) == label)
        ids = list(properties.loc[properties[column] == group, "property_id"])
    table = coverage.budget_coverage(store, year, ids)
    full = int((table["Months"] == "12 of 12").sum())
    st.caption(f"{year}: {full} of {len(table)} properties have a full-year budget. ✓ = budget loaded for that "
               "month; – = missing.")
    st.dataframe(table.assign(Imported=table["Imported"].map(lambda t: when(t) if t else "")), hide_index=True,
                 width="stretch", column_config={"Missing months": st.column_config.TextColumn(width="medium")})


# --- 3. Supporting Data ------------------------------------------------------------------------

SUPPORTING = {
    "pace": ("Forward Pace", "Business on the books for future stay months, as of a report date. Feeds the Forward "
                             "Pace preview and its detail view.", "pace", "Open Forward Pace"),
    "debt": ("Debt Service / DSCR", "Debt service, the agreed cash-flow figure and each loan's minimum DSCR. Feeds "
                                    "the DSCR preview and the Debt Coverage view.", "debt", "Open Debt Coverage"),
    "str": ("STR / Market Context", "STR competitive-set results, historical and forward. Feeds Market Context "
                                    "on Portfolio Overview and the Market Context view.", "market",
            "Open Market Context"),
    "channel_mix": ("Revenue Mix", "Room revenue by booking channel (SellAway, Direct Booking, OTA, Other). Feeds "
                                   "Revenue Mix on Portfolio Overview.", "overview", "Open Portfolio Overview"),
}


def supporting_card(store: DataStore, name: str) -> None:
    title, purpose, page, link_label = SUPPORTING[name]
    spec = sources.SOURCES[name]
    state = coverage.source_status(store, name)
    with st.container(border=True):
        head, badge = st.columns([3, 2], vertical_alignment="center")
        head.markdown(f"**{title}**")
        with badge.container(horizontal=True, horizontal_alignment="right"):
            if state["loaded"]:
                st.badge("Loaded", icon=":material/check_circle:", color="green")
            else:
                st.badge("No data yet", icon=":material/database:", color="gray")
        st.caption(purpose)
        if state["loaded"]:
            period = state["latest_period"]
            period_text = (f"as of {date_label(period)}" if name == "pace" else month_label(period[:7]))
            total = len(store.properties)
            st.markdown(f"**Latest period:** {period_text}  \n"
                        f"**Latest import:** {state['latest_file']} · {when(state['imported'])}  \n"
                        f"**Coverage:** {len(state['covered'])} of {total} properties"
                        + (f" (missing: {', '.join(state['missing'])})" if state["missing"] else "") + "  \n"
                        f"**Warnings at import:** {state['warnings']}")
        else:
            st.markdown("**Latest period:** none imported  \n**Coverage:** 0 properties")
        with st.container(horizontal=True, gap="small"):
            st.button("Upload / Import", icon=":material/upload:", key=f"open_{name}", on_click=open_dialog,
                      args=(name, "upload"))
            st.download_button("Template", sources.template_bytes(name), file_name=f"TEMPLATE_{name}.xlsx",
                               icon=":material/download:", key=f"template_{name}")
            with st.popover("File format", icon=":material/table_view:"):
                st.markdown(f"**{spec.file_hint}.** {spec.note}")
                st.dataframe(pd.DataFrame([{"Column": c.heading, "Required": "Yes" if c.required else "Optional",
                                            "Meaning": c.note + (f" Allowed: {', '.join(c.choices)}." if c.choices
                                                                 else "")}
                                           for c in spec.columns]), hide_index=True, width="stretch")
        link(page, link_label, ":material/open_in_new:")


def supporting_section(store: DataStore) -> None:
    st.subheader("3. Supporting Data", anchor=False)
    st.caption("Each source has its own file, checks and history, and is stored apart from the monthly financial "
               "figures; nothing here changes the monthly results. " + UPLOADED_MEANS)
    for row in (("pace", "debt"), ("str", "channel_mix")):
        for column, name in zip(st.columns(2, gap="medium"), row):
            with column:
                supporting_card(store, name)


def detail_expanders(store: DataStore) -> None:
    st.subheader("Details", anchor=False)
    with st.expander(f"Import history: monthly financials ({len(store.imports)})"):
        if store.imports.empty:
            st.caption("Nothing imported yet.")
        else:
            log = store.imports.sort_values("import_id", ascending=False).copy()
            log["imported_at"] = log["imported_at"].map(when)
            st.dataframe(log.drop(columns=["file_hash"]), hide_index=True, width="stretch", column_config={
                "import_id": "#", "file_name": "File", "imported_at": "Imported at", "scenarios": "Scenarios",
                "months": "Months", "rows": "Rows", "warnings": "Warnings", "replaced_rows": "Rows replaced"})
    with st.expander(f"Import history: supporting data ({len(store.source_imports)})"):
        if store.source_imports.empty:
            st.caption("No supporting file imported yet.")
        else:
            log = store.source_imports.sort_values("import_id", ascending=False).copy()
            log["imported_at"] = log["imported_at"].map(when)
            log["source"] = log["source"].map(lambda s: SUPPORTING[s][0])
            st.dataframe(log.drop(columns=["file_hash"]), hide_index=True, width="stretch", column_config={
                "import_id": "#", "source": "Source", "file_name": "File", "imported_at": "Imported at",
                "periods": "Periods", "rows": "Rows", "warnings": "Warnings", "replaced_rows": "Rows replaced"})
    warnings = store.active_warnings()
    with st.expander(f"Open data-check warnings ({len(warnings)})"):
        if warnings.empty:
            st.caption("No open warnings for the imported figures.")
        else:
            files = store.imports.set_index("import_id")["file_name"]
            shown = warnings.assign(file=warnings["import_id"].map(files))
            st.dataframe(shown[["check", "message", "property_id", "month", "sheet", "excel_row", "file"]],
                         hide_index=True, width="stretch", column_config={
                             "check": "Check", "message": st.column_config.TextColumn("Details", width="large"),
                             "property_id": "Hotel", "month": "Month", "sheet": "Sheet",
                             "excel_row": st.column_config.NumberColumn("Excel row", format="%d"), "file": "File"})
    with st.expander(f"Hotels and groupings ({len(store.properties)})"):
        st.caption("From properties.csv. Every column except property_id, property_name and keys is a grouping. "
                   "Each hotel has one value per grouping, so no hotel is counted twice within a grouping.")
        st.dataframe(store.properties, hide_index=True, width="stretch")


def local_setup_help() -> None:
    st.info("Your local workspace is empty. Three steps to start:", icon=":material/info:")
    st.markdown(
        f"1. Create `properties.csv` in the `{LOCAL_DATA_DIR.name}` folder (button below).\n"
        "2. Open it in Excel and add one row per hotel. Keep the columns; add more grouping columns if useful.\n"
        "3. Come back here and import your monthly files (download the template below).\n\n"
        f"Real data stays in `{LOCAL_DATA_DIR}` on this computer. The folder is excluded from sharing "
        "(see `.gitignore`).")
    if st.button("Create a blank properties.csv", type="primary"):
        LOCAL_DATA_DIR.mkdir(exist_ok=True)
        (LOCAL_DATA_DIR / "properties.csv").write_text(
            "property_id,property_name,keys,brand,asset_cohort,stabilization_stage\n", encoding="utf-8")
        st.rerun()
    st.download_button("Download the monthly Excel template", TEMPLATE_FILE.read_bytes(),
                       file_name=TEMPLATE_FILE.name, icon=":material/download:")


# ------------------------------------------------------------------------------------
# Page 3: Definitions, and page 4: Today's signals (planned)
# ------------------------------------------------------------------------------------

def definitions_page() -> None:
    st.title("Definitions")
    st.caption("These are read directly from hotel_dashboard/definitions.py, so they always match the running code.")

    st.subheader("Metrics")
    st.markdown(
        "| Metric | Calculation |\n|---|---|\n"
        "| Occupancy | rooms sold / rooms available |\n"
        "| ADR | rooms revenue / rooms sold |\n"
        "| RevPAR | rooms revenue / rooms available (equals occupancy x ADR) |\n"
        "| EBITDA | GOP - other expenses (below GOP), as supplied in the file; the waterfall checks it |\n"
        "| Net Operating Income (NOI) | GOP - other expenses (below GOP) - capital reserve |\n"
        "| DSCR | cash flow available for debt service / debt service, both from the debt file |\n"
        "| MPI, ARI, RGI | hotel occupancy, ADR, RevPAR / its comp set's, x 100 (per hotel, never averaged) |\n\n"
        "**Groups and the portfolio:** the app adds up rooms available, rooms sold and revenue for the "
        "selected hotels first, then divides. It never averages hotel percentages. Each hotel is included "
        "once, even if it belongs to several groups. Budget comparisons use exactly the hotels that reported "
        "actuals, and are hidden if any of them has no budget.")

    st.subheader("EBITDA Waterfall Mapping")
    st.caption("How the source P&L groups map to template columns and waterfall steps. Totals are the figures "
               "supplied in the file; a gap between the steps and a supplied total is shown as a 'Difference' step.")
    mapping = [("Total Revenue", "Total Revenue", "Starting bar")]
    mapping += [(src, d.label(f), "Operating expense step") for src, f in [
        ("Labor and Benefits", "labor_expense"), ("Room Expense", "rooms_expense"),
        ("General & Administrative", "ga_expense"), ("Maintenance", "maintenance_expense"),
        ("Sales and Marketing", "sales_marketing_expense"), ("Utilities", "utilities_expense"),
        ("Management Fees + Royalties", "mgmt_fees_royalties")]]
    mapping += [("Gross Operating Profit", "GOP", "Subtotal bar (reported GOP)")]
    mapping += [(src, d.label(f), "Below-GOP expense step") for src, f in [
        ("Insurance", "insurance_expense"), ("Real Estate Tax", "real_estate_tax"),
        ("Personal Property Tax", "personal_property_tax"), ("Tax Other", "other_taxes"),
        ("Miscellaneous Expense", "misc_other_expense")]]
    mapping += [("Total Other Expense", "Other Expenses (below GOP)", "One step, if the five groups above are not supplied"),
                ("EBITDA", "EBITDA", "Ending bar (reported EBITDA)")]
    st.dataframe(pd.DataFrame(mapping, columns=["Source P&L group", "Template column", "Waterfall"]),
                 hide_index=True, width="stretch")

    st.subheader("Separate Data Sources")
    st.caption("Each has its own import on Data Imports & Checks and is never mixed into the monthly results.")
    for spec in sources.SOURCES.values():
        with st.expander(f"{spec.title} · {spec.file_hint}"):
            st.markdown(spec.note)
            st.dataframe(pd.DataFrame([{"Column": c.heading, "Required": "Yes" if c.required else "Optional",
                                        "Meaning": c.note} for c in spec.columns]), hide_index=True, width="stretch")

    st.subheader("Pending Confirmation")
    for number, question in enumerate(d.PENDING_CONFIRMATION, start=1):
        st.markdown(md(f"{number}. {question}"))

    st.subheader("Monthly Template Columns")
    fields = pd.DataFrame([{"Excel heading": d.ALL_FIELDS[f], "Required": "Yes" if f in d.REQUIRED_FIELDS else "",
                            "Meaning": d.FIELD_NOTES.get(f, "")} for f in d.ALL_FIELDS])
    st.dataframe(fields, hide_index=True, width="stretch",
                 column_config={"Meaning": st.column_config.TextColumn(width="large")})

    st.subheader("Data Checks")
    st.markdown(md(
        "| Check | Result | Rule |\n|---|---|---|\n"
        "| Missing field | error / warning | A required column or value is blank (error); an optional one is blank (warning). |\n"
        "| Duplicate month | error / confirm | Same hotel and month twice in one file (error). Already imported before: shown, and only replaced if you confirm. |\n"
        "| Subtotal row | error | Rows such as 'Total' or 'Portfolio' are rejected so hotels aren't counted twice. |\n"
        "| Unknown property | error | Property ID is not in properties.csv. |\n"
        "| Does not reconcile | error | Rooms sold is more than rooms available. |\n"
        f"| Does not reconcile | warning | Totals don't add up (tolerance {money(d.TOTAL_TOLERANCE)}); reported occupancy differs by more than {d.OCCUPANCY_TOLERANCE_PP} points; reported ADR/RevPAR differ by more than {money(d.RATE_TOLERANCE)}; rooms available is more than keys x days. |\n"
        "| Month not closed | warning | Actual figures for the current or a future month. |\n"
        "| Wrong file type | error | Daily files (with an as-of date) are kept out of monthly results. |"))

    st.subheader("Privacy")
    if MODE["hosted_demo"]:
        st.markdown("**You are using the hosted fictional demo.**")
    st.markdown(
        "**Local company app** (run on your own computer)\n"
        "- Company financial data must never be sent to an outside AI (LLM) service. A model running entirely "
        "on this computer may process it; this version uses none, and the briefing is written by fixed rules.\n"
        "- Processing happens on this computer; the app makes no internet calls and never calls an outside model.\n"
        f"- Real data belongs only in `{LOCAL_DATA_DIR.name}/`, which is excluded from sharing.\n\n"
        "**Hosted fictional demo** (Streamlit Community Cloud, invited viewers)\n"
        "- Fictional or dummy data only. Uploads are processed on Streamlit's servers, in memory, for each "
        "visitor's session; nothing is saved and visitors can't see each other's data.\n"
        "- Only the six fictional hotels are accepted, and .xlsm workbooks are refused.\n"
        "- **Draft with AI** (button only) sends a small evidence packet of verified figures, never a "
        "workbook, to Google Gemini on the free tier, where Google may use requests to improve its products.\n\n"
        "Streamlit's usage statistics are switched off in `.streamlit/config.toml` in both modes.")


def signals_page() -> None:
    st.title("Today's Signals")
    st.badge("Planned · not built in v0", icon=":material/construction:", color="gray")
    st.markdown(
        "A later version will import a **daily** Power BI export (Excel or CSV) for a quick view of what "
        "is happening right now. The design keeps it separate from the closed monthly results:\n\n"
        "- Every daily file must have an **as-of date** column. Each figure is shown with that date, "
        "for example *'Occupancy on the books for October, as of 27 Sep 2026'*.\n"
        "- Daily rows go into their own table (`daily_signals.csv`), never into `monthly_figures.csv`, "
        "and are never added to monthly totals or budget variances.\n"
        "- The monthly importer already **rejects** files with an as-of date column, so daily figures "
        "can't get mixed into monthly results by accident.\n"
        "- The same property list and groupings will apply, with the same rule: add up first, then divide.\n\n"
        "Salesforce, TrustYou and an automated Power BI connection are out of scope for the capstone.")


# ------------------------------------------------------------------------------------

def main(hosted_demo: bool = False) -> None:
    MODE["hosted_demo"] = hosted_demo
    st.set_page_config(page_title="Torchfolio", page_icon=str(ICON_FILE), layout="wide",
                       initial_sidebar_state="collapsed")
    PAGES.update(
        overview=st.Page(overview_page, title="Portfolio Overview", icon=":material/space_dashboard:", default=True),
        imports=st.Page(imports_page, title="Data Imports & Checks", icon=":material/upload_file:", url_path="imports"),
        review=st.Page(review_page, title="Review Log", icon=":material/fact_check:", url_path="review-log"),
        definitions=st.Page(definitions_page, title="Definitions", icon=":material/menu_book:", url_path="definitions"),
        signals=st.Page(signals_page, title="Today's Signals (Planned)", icon=":material/schedule:", url_path="signals"),
        # Detail views: not in the top menu; opened from the preview boxes on the overview.
        pace=st.Page(pace_page, title="Forward Pace", url_path="pace", visibility="hidden"),
        debt=st.Page(debt_page, title="Debt Coverage", url_path="debt-coverage", visibility="hidden"),
        market=st.Page(market_page, title="Market Context", url_path="market-context", visibility="hidden"),
    )
    if hosted_demo:                   # fictional cases only; never registered in the local company-data app
        PAGES["ai_eval"] = st.Page(ai_evaluation_page, title="AI Evaluation", url_path="ai-evaluation",
                                   visibility="hidden")
    navigation = st.navigation(list(PAGES.values()), position="top")
    # Fonts come from the theme (.streamlit/config.toml). The theme has no setting for these two, so a small
    # style rule adds them: headings break into balanced lines (no single word left alone), and the KPI
    # figures use Inter's tabular (equal-width) digits so they don't shift when the selection changes.
    st.html("<style>h1,h2,h3{text-wrap:balance}"
            "[data-testid='stMetricValue'],[data-testid='stMetricDelta']{font-variant-numeric:tabular-nums}</style>")
    sidebar()
    navigation.run()


if __name__ == "__main__":
    main()
