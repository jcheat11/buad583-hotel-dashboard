"""
Optional AI draft of the management briefing: HOSTED FICTIONAL DEMO ONLY.

    verified figures  ->  compact evidence packet  ->  Gemini (only when the button is clicked)
                      ->  JSON draft  ->  validation  ->  shown as "AI draft", pending human approval

What is sent: the evidence packet only (the rule-based briefing's verified statements, the
exception flags, and the data-check and reconciliation results), never an uploaded workbook.

The local company-data app never calls a model: ai_allowed() is False unless the app runs as the
hosted demo AND in the fictional demo workspace, and call_gemini() re-checks that before any request.

Validation (nothing the model writes is trusted as-is):
  - every item must cite evidence IDs from the packet (unknown or missing IDs: item removed);
  - every number in an item must appear in the evidence it cites or in the packet heading
    (otherwise the item is removed as an unsupported number);
  - an item that cites a possible cause, or says it is one, is shown as "Possible cause (unverified)".
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
import requests

from . import briefing, metrics
from .formatting import month_label

DEFAULT_MODEL = "gemini-3.5-flash-lite"      # stable, free-tier eligible, no announced shutdown (checked Sep 2026)
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 45
MAX_ITEMS_PER_SECTION = 3
MAX_ITEM_CHARACTERS = 400
SECTION_FIELDS = {"changed": "what_changed", "matters": "why_it_matters", "decision": "what_needs_a_decision"}
FACT, POSSIBLE_CAUSE = briefing.FACT, briefing.POSSIBLE_CAUSE


class AIError(Exception):
    """A problem that stops the AI draft. `message` is shown to the user; the dashboard carries on."""

    MESSAGES = {
        "disabled": "AI drafting is switched off here: the local company-data app never sends data to an outside model.",
        "missing_key": "AI drafting isn't set up yet: the app has no GEMINI_API_KEY in its Streamlit secrets. "
                       "The briefing below is unchanged.",
        "bad_key": "The Gemini API key was refused (invalid, or not allowed to use this model). Check the key in "
                   "the app's Streamlit secrets. The briefing below is unchanged.",
        "quota": "The free Gemini quota is used up for now (rate or daily limit). Try again later; the briefing "
                 "below is unchanged.",
        "failure": "The AI model didn't respond successfully. Try again later; the briefing below is unchanged.",
        "invalid_response": "The AI model's reply couldn't be read as a briefing, so nothing was shown. Try again; "
                            "the briefing below is unchanged.",
    }

    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(self.MESSAGES[code])

    @property
    def message(self) -> str:
        return self.MESSAGES[self.code]


def ai_allowed(hosted_demo: bool, workspace: str) -> bool:
    """The only switch for outside AI calls: hosted fictional demo, demo workspace. Never local company data."""
    return bool(hosted_demo) and workspace == "demo"


# ------------------------------------------------------------------------------ evidence packet
def evidence_packet(facts: pd.DataFrame, properties: pd.DataFrame, ids, scope_label: str, month: str,
                    warnings: pd.DataFrame, map_ids) -> dict:
    """A small, self-contained summary of verified results. Every item has an ID the draft must cite."""
    statements = briefing.build_briefing(facts, properties, ids, scope_label, month, warnings)
    items = []

    def add(kind, text, source="", section=""):
        items.append({"id": f"E{len(items) + 1}", "kind": kind, "section_hint": section, "text": text,
                      "source": source})

    for s in statements:
        add(s.kind, s.text, s.source, s.section)
    table = metrics.exception_map(facts, properties, map_ids, month, warnings)
    for _, row in table[table["overall"] != "good"].iterrows():
        add("exception_flag", f"{row['property']}: status {metrics.STATUS_WORDS[row['overall']]}. "
                              + "; ".join(row["details"]) + ".", "Properties Needing Attention", "decision")
    summary = metrics.month_summary(facts, ids, month)
    if summary["actual"]:
        waterfall = metrics.ebitda_waterfall(summary["actual"])
        if waterfall is not None:
            if waterfall["ebitda_supplied"] is None:
                add("reconciliation", "EBITDA was not supplied for every hotel, so the waterfall end is calculated "
                                      "and cannot be reconciled.", "EBITDA waterfall", "decision")
            elif waterfall["reconciles"]:
                add("reconciliation", "The EBITDA waterfall reconciles to the reported EBITDA.", "EBITDA waterfall",
                    "matters")
            else:
                gaps = "; ".join(f"reported {name} differs from the lines by {_money(gap)}"
                                 for name, _, _, gap in waterfall["checks"] if gap)
                add("reconciliation", f"The EBITDA waterfall does not reconcile: {gaps}.", "EBITDA waterfall",
                    "decision")
    scoped = warnings[warnings["property_id"].isin(ids) & (warnings["month"] == month)] if not warnings.empty else warnings
    if len(scoped):
        add("data_check", f"{len(scoped)} data-check warning(s) affect these figures: "
                          + "; ".join(sorted(set(scoped["check"]))) + ".", "Data Imports & Checks", "decision")
    reporting = len(summary["actual"]["property_ids"]) if summary["actual"] else 0
    return {"scope": scope_label, "month": month_label(month),
            "hotels_reporting": f"{reporting} of {len(set(ids))} hotels reporting",
            "data": "FICTIONAL demo data", "evidence": items}


def _money(value: float) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.0f}"


def packet_fingerprint(packet: dict, context: str) -> str:
    """Changes whenever the evidence, the selection, or the imports change."""
    return hashlib.sha256((json.dumps(packet, sort_keys=True) + context).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------ the model call
INSTRUCTIONS = """You are an experienced hotel asset manager writing this month's briefing for a colleague.
Use ONLY the evidence items provided.

Evidence rules (strict):
- Three sections: what_changed, why_it_matters, what_needs_a_decision. At most 3 items per section.
- Every item cites the evidence IDs it relies on (e.g. ["E2","E7"]) in its "evidence" field. Do not rely on
  anything not in the evidence.
- Copy every figure EXACTLY as written in the cited evidence (same digits, $, %, commas). Do not calculate,
  round, convert or combine figures. If a figure isn't in the evidence, don't write one.
- Items with kind "possible_cause" in the evidence are unverified. If you mention a possible cause, set the
  item's kind to "possible_cause" and phrase it as something to check, never as established fact.

Style:
- Each item is one short sentence (about 12 to 25 words) that a busy asset manager would actually say.
- Lead with the point: the business meaning or the action, then at most one or two supporting figures.
  Good: "[Hotel] is giving back margin on labor, which ran [figure] over budget." Not: "Labor expense for
  [Hotel] was [figure] compared with a budget of [figure]." ([Hotel] and [figure] are placeholders: use the
  names and figures from the evidence.)
- what_changed: the few movements that matter most, not a list of every metric.
- why_it_matters: the consequence for profit, cash flow, covenants or the plan.
- what_needs_a_decision: start with the action (review, approve, follow up, hold) and name the property or
  line; cite the flag or threshold behind it.
- Plain business language. Vary how items begin; don't repeat the same phrase across items. No jargon
  beyond standard hotel terms (RevPAR, ADR, GOP, NOI, EBITDA, DSCR).
- Write plain sentences only: no bullet characters, numbering, headings, markdown or evidence IDs in the
  text; the app formats the list itself."""

ITEM_SCHEMA = {"type": "OBJECT", "properties": {
    "text": {"type": "STRING"},
    "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
    "kind": {"type": "STRING", "description": "fact or possible_cause"}},   # enforced by validate()
    "required": ["text", "evidence", "kind"]}
RESPONSE_SCHEMA = {"type": "OBJECT", "properties": {name: {"type": "ARRAY", "items": ITEM_SCHEMA}
                                                   for name in SECTION_FIELDS.values()},
                   "required": list(SECTION_FIELDS.values())}


def call_gemini(packet: dict, api_key: str | None, model: str = DEFAULT_MODEL, *, hosted_demo: bool,
                workspace: str) -> dict:
    """Sends the evidence packet (nothing else) and returns the parsed JSON draft, or raises AIError."""
    if not ai_allowed(hosted_demo, workspace):
        raise AIError("disabled")                        # checked again here, before any network request
    if not api_key:
        raise AIError("missing_key")
    body = {
        "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(packet, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096,
                             "responseMimeType": "application/json", "responseSchema": RESPONSE_SCHEMA},
    }
    try:
        response = requests.post(ENDPOINT.format(model=model), json=body, timeout=TIMEOUT_SECONDS,
                                 headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
    except requests.RequestException as exc:
        raise AIError("failure", type(exc).__name__) from None
    if response.status_code == 429:
        raise AIError("quota")
    if response.status_code in (401, 403) or (response.status_code == 400 and "API key" in response.text):
        raise AIError("bad_key", str(response.status_code))
    if response.status_code != 200:
        raise AIError("failure", f"HTTP {response.status_code}")
    try:
        data = response.json()
        candidate = data["candidates"][0]
        text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        draft = json.loads(text)
    except (ValueError, KeyError, IndexError, TypeError):
        raise AIError("invalid_response") from None
    if not isinstance(draft, dict):
        raise AIError("invalid_response")
    return draft


# ------------------------------------------------------------------------------ validation
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
EVIDENCE_ID = re.compile(r"\bE\d+\b")
# Hedged cause language in an item the model called a fact: shown as a possible cause instead.
CAUSE_WORDS = re.compile(r"\b(possibl[ey]|might|could|perhaps|likely|unverified|may have|may be)\b", re.I)
# A list marker the model added despite the instructions (the app draws the bullets itself).
LIST_MARKER = re.compile(r"^\s*(?:[-*•‣▪]|\d{1,2}[.)])\s+")


def plain_text(text) -> str:
    """One plain sentence for a bullet: no leading list marker, no line breaks, no markdown emphasis."""
    text = LIST_MARKER.sub("", " ".join(str(text).split()))
    return text.replace("**", "").replace("__", "").strip()


def _normalise(number: str) -> str:
    try:
        return format(Decimal(number.replace(",", "")).normalize(), "f")
    except InvalidOperation:
        return number


def numbers_in(text: str) -> set[str]:
    return {_normalise(n) for n in NUMBER.findall(EVIDENCE_ID.sub("", text or ""))}


@dataclass
class Draft:
    sections: dict                     # "changed" / "matters" / "decision" -> list of {text, evidence, kind}
    removed: list                      # (section, text, reason)
    model: str
    packet_fingerprint: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    numbers_total: int = 0
    numbers_supported: int = 0

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256((self.packet_fingerprint + json.dumps(self.sections, sort_keys=True)).encode()
                              ).hexdigest()[:16]


def validate(raw: dict, packet: dict, model: str, packet_fp: str) -> Draft:
    """Keeps only items whose evidence exists and whose every number appears in that evidence."""
    evidence = {item["id"]: item for item in packet["evidence"]}
    heading = numbers_in(" ".join(str(packet[k]) for k in ("scope", "month", "hotels_reporting")))
    sections, removed, total, supported = {}, [], 0, 0
    for key, name in SECTION_FIELDS.items():
        items = raw.get(name) if isinstance(raw.get(name), list) else []
        kept = []
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                removed.append((key, str(item)[:120], "not a briefing item"))
                continue
            text = plain_text(item.get("text", ""))[:MAX_ITEM_CHARACTERS]
            cited = [str(e).strip() for e in item.get("evidence") or [] if str(e).strip()]
            found = numbers_in(text)
            allowed = heading | set().union(*(numbers_in(evidence[e]["text"] + " " + evidence[e]["source"])
                                              for e in cited if e in evidence))
            total += len(found)
            supported += len(found & allowed)
            if position >= MAX_ITEMS_PER_SECTION:
                removed.append((key, text, "more than 3 items in the section"))
            elif not text:
                removed.append((key, text, "empty"))
            elif not cited:
                removed.append((key, text, "no evidence cited"))
            elif any(e not in evidence for e in cited):
                removed.append((key, text, "cites evidence that doesn't exist: "
                                + ", ".join(e for e in cited if e not in evidence)))
            elif found - allowed:
                removed.append((key, text, "number(s) not in the cited evidence: " + ", ".join(sorted(found - allowed))))
            else:
                cause = (item.get("kind") == POSSIBLE_CAUSE or bool(CAUSE_WORDS.search(text))
                         or any(evidence[e]["kind"] == POSSIBLE_CAUSE for e in cited))
                kept.append({"text": EVIDENCE_ID.sub("", text).replace("[]", "").strip(), "evidence": cited,
                             "kind": POSSIBLE_CAUSE if cause else FACT})
        sections[key] = kept
    return Draft(sections, removed, model, packet_fp, numbers_total=total, numbers_supported=supported)


def draft_markdown(draft: Draft, packet: dict, reviewer: str, reviewed_at: str, notes: str) -> str:
    lines = [f"# AI-drafted management briefing: {packet['scope']}, {packet['month']}", "",
             f"*{packet['data']}. Drafted by Gemini ({draft.model}) from verified figures; "
             f"reviewed and approved by {reviewer} on {reviewed_at}.*", ""]
    evidence = {item["id"]: item for item in packet["evidence"]}
    for key, title in briefing.SECTIONS.items():
        lines += [f"## {title}", ""]
        if not draft.sections[key]:
            lines.append("- No finding passed validation for this section.")
        for item in draft.sections[key]:
            label = "Possible cause (unverified): " if item["kind"] == POSSIBLE_CAUSE else ""
            lines.append(f"- {label}{plain_text(item['text'])}")
            for e in item["evidence"]:                         # the cited evidence, as indented sub-points
                source = f" (source: {evidence[e]['source']})" if evidence[e]["source"] else ""
                lines.append(f"    - Evidence {e}: {evidence[e]['text']}{source}")
        lines.append("")
    if notes:
        lines += ["## Reviewer notes", "", notes, ""]
    return "\n".join(lines)
