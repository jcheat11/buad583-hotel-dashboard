# Hotel Asset Dashboard: BUAD 583 capstone prototype (v0)

Turns monthly hotel Excel files into an executive dashboard for asset managers, the CEO and the
Chief of Staff. It imports a defined monthly template, checks the data, calculates occupancy,
ADR and RevPAR from the underlying rooms and revenue, compares against budget, and drafts a
short written briefing that an asset manager must approve before it can be shared.

**Two modes, with different privacy rules:**

- **Local company app** (`run_app.bat` / `app.py`): runs on your computer, needs no internet
  connection, keeps real data in the `local_data/` folder (never shared), and **never calls an
  outside AI model**. The briefing is written by fixed rules.
- **Hosted fictional demo** (`demo_app.py` on Streamlit Community Cloud, invited viewers only):
  fictional or dummy data only. Uploads are processed on Streamlit's servers, in memory, for each
  visitor's session. The optional **Draft AI Briefing** button sends a small evidence packet of
  verified, fictional figures (never a workbook) to Google Gemini on the free tier, where Google
  may use requests to improve its products.

**Privacy rule:** company financial data must never be sent to an outside AI (LLM) service. A
model running entirely on the user's computer may process it (not used in this version). Future
Power BI or CRM connections may read from approved company systems, but must not pass financial
data to an outside AI service. During development, only fictional or approved sanitized data is used.

---

## 1. Start the app

You need Python 3.11 or newer (this computer has 3.14) with a few packages. Install them once:

```
pip install -r requirements.txt
```

Then **double-click `run_app.bat`**. A browser tab opens at http://localhost:8501.
Keep the black window open while you use the app; close it to stop the app.

(Without the .bat file: `python -m streamlit run app.py`.)

## 2. Check it works in 5 minutes (fictional data)

The app opens in the **Fictional demo data** workspace: six made-up hotels with a 2026 budget and
January–July actuals already imported. Nothing you do here is saved.

The page menu is across the top. The workspace switch (fictional demo vs your local data) is in
the sidebar, which starts collapsed: open it with the **>>** button at the top left.

1. **Portfolio Overview** shows July 2026. A small orange notice at the top right says
   *Aug 2026 not imported* (hover over it for details). The reporting period and comparison basis
   are stated under the title.
2. Go to **Data Imports & Checks** and click **August 2026 close (clean file)**. It says
   *All checks passed*. Click **Import**.
3. Click the same button again. It is flagged as a **duplicate month**, and **Import** stays
   locked until you tick *Replace the previously imported figures*.
4. Click **August 2026 with deliberate errors**. It shows 3 errors (a missing field, a duplicate
   hotel-month, a "TOTAL" subtotal row) and 3 warnings (figures that do not reconcile), and it
   cannot be imported.
5. Back on **Portfolio Overview**, August is now shown. Under the trends:
   - the **EBITDA Waterfall** steps down from Total Revenue through each expense group (source P&L
     order) to GOP and EBITDA; the line under it confirms it reaches the reported EBITDA
     (*Reconciles: Total Revenue ... = reported EBITDA ...*);
   - **Revenue Mix** says *Channel data needed* until a channel file is imported (step 9);
   - **Largest Expense Variances** shows amounts, units and the budget basis;
   - **Properties Needing Attention** counts Review / Watch / On Plan and marks Canyon Gate Suites
     and Mesa Suites *Review*; open a property under **Details** to see why.
6. Click **Canyon Gate Suites** in that table. The whole page switches to that hotel.
   **Show all properties** switches back.
7. Choose **Property group → Asset Cohort → Legacy Assets**. Mesa Suites is also in
   *LivAway Suites* (brand) and *Stabilized Properties* (stage), but each view counts it once.
8. At the bottom, the **Management Briefing** has three short cards (*What Changed*, *Why It Matters*,
   *What Needs a Decision*), each a headline and one sentence; evidence, sources and possible causes
   (marked *not verified*) are in each card's expander. Enter a name, tick the box
   and click **Approve for sharing**; the download appears only then.
9. The three **Forward-Looking and Market Context** boxes say *Pace / Debt / STR data needed*. On
   **Data Imports & Checks → 2. Other Data Sources**, open each tab and click **Load the fictional
   demo file**, then **Import**. The boxes (and Revenue Mix) fill in without changing size; each box
   opens a detail view (**Forward Pace**, **Debt Coverage**, **Market Context**).

To check a number yourself, open `sample_data/monthly_2026-08.xlsx` in Excel and compute
`SUM(Rooms Revenue) / SUM(Rooms Sold)`. It matches the ADR card for *All properties* in August.

## 3. Run the tests

Double-click **`run_tests.bat`** (or run `python -m unittest discover -s tests -t . -v`).
There are 88 tests, all on fictional data, including checks that the EBITDA waterfall reconciles
(and shows any gap as its own step), that changing the view updates every widget, that each separate
source is checked and kept apart from the monthly figures, and that missing data is flagged. The small hand-worked examples in `tests/helpers.py`
show, for example, why a portfolio occupancy of 60% is right when averaging would give 70%.

## 4. Use your own data (stays on this computer)

1. In the app, open the sidebar (**>>**) and switch to **My local data** and click **Create a blank properties.csv**.
2. Open `local_data/properties.csv` in Excel. Add one row per hotel: `property_id`,
   `property_name`, `keys`, then one column per grouping (brand, asset_cohort,
   stabilization_stage…). **Any extra column becomes a new grouping option.**
3. Copy each month's figures into the template (`sample_data/TEMPLATE_monthly_hotel_report.xlsx`;
   its ReadMe sheet explains every column) and import it on **Data Imports & Checks**.

Rules that keep company data private:
- Real files and imported data live only in `local_data/`. `.gitignore` excludes that folder and
  every spreadsheet except the fictional samples, so they can't be committed by accident.
- Streamlit's usage statistics are turned off, and the app only listens on `localhost`
  (see `.streamlit/config.toml`).

## 5. How the numbers work

| Metric | Calculation |
|---|---|
| Occupancy | rooms sold / rooms available |
| ADR | rooms revenue / rooms sold |
| RevPAR | rooms revenue / rooms available (= occupancy x ADR) |
| EBITDA | GOP - other expenses (below GOP), as supplied; the waterfall checks it |
| Net Operating Income (NOI) | GOP - other expenses (below GOP) - capital reserve |
| DSCR | cash flow available for debt service / debt service, both from the debt file |
| MPI, ARI, RGI | hotel occupancy, ADR, RevPAR / its comp set's, x 100 (per hotel) |

- **Groups and the portfolio:** add up the rooms and revenue of the selected hotels first, then
  divide. Each hotel counts once, even if it belongs to several groups. Subtotal rows in an
  import are rejected.
- **Budget variances** compare like with like: the budget is added up for exactly the hotels that
  reported actuals, and is hidden if any of them has no budget.
- **EBITDA waterfall:** Total Revenue, minus the seven operating expense groups, = GOP; minus the
  below-GOP groups (Insurance, Real Estate Tax, Personal Property Tax, Other Taxes, Miscellaneous;
  or one combined step if a file doesn't split them) = EBITDA. GOP and EBITDA are the figures in the
  file; if the lines don't reach them, the gap is a separate *Difference* step, never hidden in a bar.
  The mapping from the source P&L groups is on the **Definitions** page.
- **Separate sources** (`hotel_dashboard/sources.py`), each with its own template, checks and history
  on **Data Imports & Checks**, and never mixed into the monthly results:
  - *Revenue Mix*: room revenue by channel (SellAway, Direct Booking, OTA, Other). Blank is not zero.
    No shares of total until it is confirmed that the channels don't overlap.
  - *Forward Pace*: on-the-books room nights and revenue per future stay month, labelled by as-of
    date; % of budget uses the imported monthly budget for the same hotels.
  - *STR*: historical (closed) and forward (on the books) rows are kept apart; indexes per hotel.
  - *Debt service*: DSCR over the trailing 12 months when all 12 are supplied, else the month alone
    (labelled). Numerator and minimum come from the file; nothing is assumed.
- **Properties Needing Attention:** RevPAR and GOP vs budget, labor vs budget and data quality per
  hotel, each with a symbol (✓ ! ✕ –) and words as well as a colour (Review / Watch / On Plan). Thresholds are in `definitions.py`.
- **Briefing:** fixed rules turn verified calculations into statements. Each statement shows its
  source figures. *Possible causes* are separate, written as questions, and marked unverified. Approval is tied to the exact statements: if the figures change, the briefing goes
  back to draft.

All definitions, tolerances and thresholds are in `hotel_dashboard/definitions.py` and are shown
on the app's **Definitions** page, including the questions still to be confirmed.

## 6. What's where

```
app.py                     the screens (no calculations here)
demo_app.py                hosted-demo entry point: fictional data only, uploads switched off
hotel_dashboard/
  definitions.py           metric definitions, template columns, tolerances, thresholds
  importer.py              reads the Excel template and runs the data checks
  store.py                 saves data as CSV files in local_data/ (or in memory for the demo)
  metrics.py               every calculation shown on screen
  briefing.py              the rule-based written briefing and its review fingerprint
  charts.py                the dashboard charts (no calculations)
  sources.py               the separate imports: channel mix, forward pace, STR, debt service
  demo.py                  loads the fictional demo workspace
sample_data/               fictional hotels, monthly files, TEMPLATE_* and DEMO_* source files (safe to share)
packaging/                 portable Windows demo build and its tests
scripts/make_sample_data.py  regenerates the fictional files
tests/                     automated tests (fictional data only)
local_data/                YOUR real data; created when you set it up; never shared
```

## 7. The hosted fictional demo (Streamlit Community Cloud)

The demo runs `demo_app.py` from the private GitHub repository on Streamlit Community Cloud;
invited viewers sign in to open it. It only ever uses the fictional demo workspace (never
`local_data/`).

**Dummy uploads.** A warning and a "fictional or dummy data only" confirmation come before any
upload control; only rows for the six fictional hotels are accepted (real company files are
rejected as unknown properties); .xlsm workbooks are refused; uploads are processed in memory on
Streamlit's servers for that visitor's session only (nothing is saved; visitors can't see each
other's data). The Data Imports page offers the fictional sample files as a zip to edit and re-upload.

**Draft AI Briefing (hosted demo only).** Under the rule-based Management Briefing, a button asks
Google Gemini (`gemini-3.5-flash-lite`, free tier) to draft the same three sections from an
*evidence packet*: the rule-based briefing's verified statements, the exception flags, and the
data-check and reconciliation results, each with an ID (E1, E2, ...). The workbook is never sent.
The reply is validated before it is shown: every item must cite existing evidence IDs, every
number must appear in the evidence it cites (otherwise the item is removed and listed), and
possible causes are labelled *unverified*. The draft is marked *AI draft—asset-manager review
required*, needs its own approval before it can be downloaded, and is discarded (with its
approval) when the imports or the Show / Month selection change. The model is only called when
the button is clicked. A missing key, the free-tier limit or a model failure shows a message; the
dashboard and the rule-based briefing keep working. The local company app has no AI button and
refuses outside AI calls in code (`hotel_dashboard/ai_briefing.py`, `ai_allowed`).
The hidden page `/ai-evaluation` runs four fictional cases (normal, adverse, missing data,
reconciliation error) through the AI and the rule-based briefing and scores them.

**The Gemini key lives only in Streamlit's secrets**, never in the project or on GitHub:
1. Create a free key at aistudio.google.com (Get API key). Do not enable billing.
2. On share.streamlit.io, open the app's menu (⋮) > **Settings** > **Secrets**, add the line
   `GEMINI_API_KEY = "your-key"` and click **Save**. Optionally add `GEMINI_MODEL = "..."`
   to switch model.
3. Reload the app. `.streamlit/secrets.toml` is git-ignored, so a local copy can't be committed.

## 8. Not built yet

- **Adapters for the real exports:** pace, STR, debt and channel files currently need re-keying into
  the simple templates (`sample_data/TEMPLATE_*.xlsx`). Once sample exports are available, a small
  adapter per export can fill the template automatically.
- **Guest reviews** (TrustYou) are not connected; the Market Context view says so.
- **Brand typefaces:** the West77 Partners style guide (2022, Rev 2) sets the colours, now applied in
  `.streamlit/config.toml` and `hotel_dashboard/charts.py`, but doesn't name a typeface; the app keeps
  Streamlit's default sans-serif until one is confirmed.

- **Today's signals** (daily Power BI export): designed but not built. Daily files must carry an
  as-of date, will go in a separate table, and will never be added to monthly results. The
  monthly importer already rejects files with an as-of date column.
- An adapter that converts the company's existing monthly management package into the template
  (today, figures are copied into the template).
- Groupings that change over time (v0 applies each hotel's current stabilization stage to every month).
- Salesforce, TrustYou and an automated Power BI connection are out of scope for the capstone.
