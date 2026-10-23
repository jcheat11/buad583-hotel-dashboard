"""
Keeps imported figures on this computer, or only in memory (for the demo and the tests).

Files in the data folder are plain CSV, so they open in Excel:
  properties.csv         the hotel list and its groupings (you maintain this one)
  monthly_figures.csv    one row per hotel + month + scenario (Actual or Budget)
  import_log.csv         one row per imported file, with the date and time it was imported
  data_check_issues.csv  warnings found when each file was imported
  briefing_reviews.csv   who approved which briefing, and when
  channel_mix.csv, pace.csv, str.csv, debt.csv
                         the separate sources (see sources.py), each kept apart from monthly_figures.csv
  source_import_log.csv  one row per imported source file

Nothing in this module sends data anywhere.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import definitions as d
from .importer import ImportResult
from .sources import SOURCES

KEY = ["scenario", "property_id", "month"]
FACT_COLUMNS = KEY + d.NUMERIC_FIELDS + ["import_id"]
LOG_COLUMNS = ["import_id", "file_name", "file_hash", "imported_at", "scenarios", "months",
               "rows", "warnings", "replaced_rows"]
ISSUE_COLUMNS = ["import_id", "severity", "check", "message", "sheet", "excel_row", "property_id", "month"]
REVIEW_COLUMNS = ["scope", "month", "fingerprint", "reviewer", "reviewed_at", "notes"]
SOURCE_LOG_COLUMNS = ["import_id", "source", "file_name", "file_hash", "imported_at", "periods", "rows",
                      "warnings", "replaced_rows"]
SOURCE_COLUMNS = {name: [c.field for c in spec.columns] + ["import_id"] for name, spec in SOURCES.items()}

FILES = {
    "facts": ("monthly_figures.csv", FACT_COLUMNS),
    "imports": ("import_log.csv", LOG_COLUMNS),
    "issues": ("data_check_issues.csv", ISSUE_COLUMNS),
    "reviews": ("briefing_reviews.csv", REVIEW_COLUMNS),
    "source_imports": ("source_import_log.csv", SOURCE_LOG_COLUMNS),
    **{name: (f"{name}.csv", columns) for name, columns in SOURCE_COLUMNS.items()},
}
TEXT_COLUMNS = {"scenario", "property_id", "month", "file_name", "file_hash", "imported_at", "scenarios",
                "months", "severity", "check", "message", "sheet", "scope", "fingerprint", "reviewer",
                "reviewed_at", "notes", "source", "periods", "as_of_date", "stay_month", "report_type",
                "period_type", "period", "report_date", "comp_set", "lender"}


class DuplicateMonthError(Exception):
    """The file contains hotel-months that were already imported, and replacing them wasn't confirmed."""

    def __init__(self, duplicates: pd.DataFrame):
        self.duplicates = duplicates
        super().__init__(f"{len(duplicates)} hotel-month(s) were already imported.")


def load_properties(path) -> tuple[pd.DataFrame, list[str]]:
    """Reads properties.csv. Returns (table, problems), where problems are plain-English messages."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    problems = [f"properties.csv needs a '{c}' column." for c in ("property_id", "property_name")
                if c not in df.columns]
    if problems:
        return pd.DataFrame(columns=d.PROPERTY_FIXED_COLUMNS), problems

    df = df.apply(lambda column: column.str.strip())
    df = df[df["property_id"] != ""]
    repeated = df["property_id"][df["property_id"].duplicated()].unique()
    if len(repeated):
        problems.append("Property IDs listed more than once (only the first is used): " + ", ".join(repeated))
        df = df.drop_duplicates("property_id")
    df["keys"] = pd.to_numeric(df["keys"], errors="coerce") if "keys" in df.columns else float("nan")

    for column in d.classification_columns(df.columns):
        blank = df[column] == ""
        if blank.any():
            problems.append(f"{int(blank.sum())} hotel(s) have no '{column}' value; shown as 'Unassigned'.")
            df.loc[blank, column] = "Unassigned"
    return df.reset_index(drop=True), problems


def _concat(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    new = new.reindex(columns=existing.columns)
    if existing.empty:
        return new.reset_index(drop=True)
    if new.empty:
        return existing
    return pd.concat([existing, new], ignore_index=True)


class DataStore:
    """All imported data for one workspace. folder=None keeps everything in memory only."""

    def __init__(self, properties: pd.DataFrame, folder: str | Path | None = None):
        self.properties = properties
        self.folder = Path(folder) if folder else None
        self.preloaded: list[str] = []          # demo only: sample files loaded before the visitor did anything
        self.facts = self._load("facts")
        self.imports = self._load("imports")
        self.issues = self._load("issues")
        self.reviews = self._load("reviews")
        self.source_imports = self._load("source_imports")
        self.sources = {name: self._load(name) for name in SOURCE_COLUMNS}

    @classmethod
    def open_folder(cls, folder: str | Path) -> tuple["DataStore | None", list[str]]:
        """Opens a local data folder. Returns (store, problems); store is None if properties.csv is missing."""
        path = Path(folder) / "properties.csv"
        if not path.exists():
            return None, [f"No properties.csv found in {folder}."]
        properties, problems = load_properties(path)
        return cls(properties, folder), problems

    def _load(self, name: str) -> pd.DataFrame:
        file_name, columns = FILES[name]
        if self.folder and (self.folder / file_name).exists():
            dtypes = {c: str for c in columns if c in TEXT_COLUMNS}
            return pd.read_csv(self.folder / file_name, dtype=dtypes).reindex(columns=columns)
        return pd.DataFrame(columns=columns)

    def save(self) -> None:
        if not self.folder:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        for name, (file_name, _) in FILES.items():
            table = self.sources[name] if name in SOURCE_COLUMNS else getattr(self, name)
            table.to_csv(self.folder / file_name, index=False)

    # --- Importing ----------------------------------------------------------------

    def duplicates_of(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Hotel-months in `rows` that are already stored (same scenario, hotel and month)."""
        if rows.empty or self.facts.empty:
            return pd.DataFrame(columns=KEY + ["import_id"])
        return rows[KEY].merge(self.facts[KEY + ["import_id"]], on=KEY)

    def earlier_import_of_same_file(self, file_hash: str) -> pd.Series | None:
        match = self.imports[self.imports["file_hash"] == file_hash]
        return None if match.empty else match.iloc[-1]

    def add_import(self, result: ImportResult, replace: bool = False,
                   imported_at: datetime | None = None) -> int:
        """
        Saves a checked file. Refuses files with errors. If some hotel-months were imported
        before, refuses unless replace=True, in which case the old figures are removed first,
        so a hotel-month is never stored (or counted) twice.
        """
        if result.errors:
            raise ValueError("This file has errors, so nothing was imported.")
        if result.rows.empty:
            raise ValueError("This file has no rows to import.")
        duplicates = self.duplicates_of(result.rows)
        if not duplicates.empty and not replace:
            raise DuplicateMonthError(duplicates)

        import_id = int(self.imports["import_id"].max()) + 1 if not self.imports.empty else 1
        imported_at = imported_at or datetime.now()

        kept = self.facts
        if not duplicates.empty:
            marked = kept.merge(duplicates[KEY].drop_duplicates(), on=KEY, how="left", indicator=True)
            kept = kept[(marked["_merge"] == "left_only").to_numpy()]
        new = result.rows.reindex(columns=FACT_COLUMNS).assign(import_id=import_id)
        self.facts = _concat(kept, new).sort_values(KEY).reset_index(drop=True)

        months = sorted(new["month"].unique())
        log_row = {
            "import_id": import_id,
            "file_name": result.file_name,
            "file_hash": result.file_hash,
            "imported_at": imported_at.isoformat(timespec="seconds"),
            "scenarios": ", ".join(sorted(new["scenario"].unique())),
            "months": months[0] if len(months) == 1 else f"{months[0]} to {months[-1]}",
            "rows": len(new),
            "warnings": len(result.warnings),
            "replaced_rows": len(duplicates),
        }
        self.imports = _concat(self.imports, pd.DataFrame([log_row]))
        warnings = pd.DataFrame([{**asdict(w), "import_id": import_id} for w in result.warnings])
        self.issues = _concat(self.issues, warnings)
        self.save()
        return import_id

    def active_warnings(self) -> pd.DataFrame:
        """Warnings that still apply: the import they came from still supplies the figures they refer to."""
        warnings = self.issues[self.issues["severity"] == "warning"]
        warnings = warnings[warnings["import_id"].isin(set(self.facts["import_id"]))]
        if warnings.empty:
            return warnings
        row_level = warnings["property_id"].fillna("") != ""
        live = self.facts[["import_id", "scenario", "property_id", "month"]].rename(columns={"scenario": "sheet"})
        linked = warnings[row_level].merge(live, on=["import_id", "sheet", "property_id", "month"])
        return _concat(warnings[~row_level], linked)

    # --- Separate sources (channel mix, pace, STR, debt) -----------------------------

    def source_duplicates(self, source: str, rows: pd.DataFrame) -> pd.DataFrame:
        """Records in `rows` that were already imported for this source (same key)."""
        key = list(SOURCES[source].key)
        stored = self.sources[source]
        if rows.empty or stored.empty:
            return pd.DataFrame(columns=key)
        return rows[key].astype(str).merge(stored[key].astype(str).drop_duplicates(), on=key)

    def add_source_import(self, source: str, result: ImportResult, replace: bool = False,
                          imported_at: datetime | None = None) -> int:
        """Saves a checked source file. Same rules as monthly files: no errors, and repeats replaced only if confirmed."""
        if result.errors:
            raise ValueError("This file has errors, so nothing was imported.")
        if result.rows.empty:
            raise ValueError("This file has no rows to import.")
        duplicates = self.source_duplicates(source, result.rows)
        if not duplicates.empty and not replace:
            raise DuplicateMonthError(duplicates)
        key = list(SOURCES[source].key)
        kept = self.sources[source]
        if not duplicates.empty:
            marked = kept[key].astype(str).merge(duplicates.drop_duplicates(), on=key, how="left", indicator=True)
            kept = kept[(marked["_merge"] == "left_only").to_numpy()]
        import_id = int(self.source_imports["import_id"].max()) + 1 if not self.source_imports.empty else 1
        new = result.rows.reindex(columns=SOURCE_COLUMNS[source]).assign(import_id=import_id)
        self.sources[source] = _concat(kept, new).reset_index(drop=True)
        period_field = next(f for f in ("month", "stay_month", "period") if f in new.columns)
        periods = sorted(new[period_field].dropna().unique())
        log_row = {"import_id": import_id, "source": source, "file_name": result.file_name,
                   "file_hash": result.file_hash,
                   "imported_at": (imported_at or datetime.now()).isoformat(timespec="seconds"),
                   "periods": periods[0] if len(periods) == 1 else f"{periods[0]} to {periods[-1]}",
                   "rows": len(new), "warnings": len(result.warnings), "replaced_rows": len(duplicates)}
        self.source_imports = _concat(self.source_imports, pd.DataFrame([log_row]))
        self.save()
        return import_id

    # --- Briefing reviews -----------------------------------------------------------

    def find_review(self, scope: str, month: str, fingerprint: str) -> pd.Series | None:
        r = self.reviews
        match = r[(r["scope"] == scope) & (r["month"] == month) & (r["fingerprint"] == fingerprint)]
        return None if match.empty else match.iloc[-1]

    def add_review(self, scope: str, month: str, fingerprint: str, reviewer: str, notes: str = "",
                   reviewed_at: datetime | None = None) -> None:
        row = {"scope": scope, "month": month, "fingerprint": fingerprint, "reviewer": reviewer,
               "reviewed_at": (reviewed_at or datetime.now()).isoformat(timespec="seconds"), "notes": notes}
        self.reviews = _concat(self.reviews, pd.DataFrame([row]))
        self.save()
