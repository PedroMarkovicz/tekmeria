"""ClinicalTrials.gov v2 study JSON as trial records and passages (spec sections 4.3 and 8)."""

import re
from datetime import datetime
from typing import Any

from pydantic import JsonValue

from tekmerion.contract.models import Link, Provenance, PublishedDate, Record, RecordIds
from tekmerion.contract.normalize import normalize_text
from tekmerion.contract.tool_models import TrialHit
from tekmerion.sources.base import PassageDraft, RecordBundle

SOURCE = "clinicaltrials"
VENUE = "ClinicalTrials.gov"

# The registry stores Markdown, so punctuation arrives escaped: "\>18" means ">18".
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|~=])")
_HEADING = re.compile(
    r"^\s*(?:key\s+)?(inclusion|exclusion)\s+criteria\b\s*:?\s*(.*)$", re.IGNORECASE
)
_BULLET = re.compile(r"^(?:\d{1,3}[.)]|[*•-])\s+")  # "1.", "1)", "*", "•", "-"


def clean(text: str | None) -> str:
    return normalize_text(_MD_ESCAPE.sub(r"\1", text or ""))


def _sentences(parts: list[str]) -> str:
    """Join non-empty parts as sentences, adding a full stop where one is missing."""
    return " ".join(p if p[-1] in ".!?:" else p + "." for p in parts if p)


def _date(value: str | None) -> PublishedDate | None:
    if not value:
        return None
    precision = {10: "day", 7: "month", 4: "year"}.get(len(value))
    return (
        PublishedDate.model_validate({"date": value, "precision": precision}) if precision else None
    )


def _words(value: str | None) -> str:
    """EXPERIMENTAL -> experimental, PLACEBO_COMPARATOR -> placebo comparator."""
    return (value or "").replace("_", " ").lower()


def eligibility_items(criteria: str | None) -> dict[str, list[str]]:
    """Top-level criteria of the inclusion and exclusion lists, plus any other statements.

    A bulleted or numbered line starts an item, as does an unindented line after a blank
    line. Indented sub-bullets join their item, separated by "; ".
    """
    lists: dict[str, list[str]] = {"inclusion": [], "exclusion": [], "other": []}
    current = "other"
    pieces: list[tuple[bool, str]] = []  # (is a sub-bullet, text) of the open item
    blank = True

    def close() -> None:
        text = ""
        for is_bullet, piece in pieces:
            if not text:
                text = piece
            elif is_bullet and not text.endswith(":"):
                text += "; " + piece
            else:
                text += " " + piece
        if cleaned := clean(text):
            lists[current].append(cleaned)
        pieces.clear()

    for line in (criteria or "").splitlines():
        if not line.strip():
            blank = True
            continue
        heading = _HEADING.match(line)
        if heading:
            close()
            current = heading.group(1).lower()
            if heading.group(2).strip():
                pieces.append((False, heading.group(2).strip()))
            blank = False
            continue
        indented = line[:1] in (" ", "\t")
        bullet = _BULLET.match(line.strip())
        text = _BULLET.sub("", line.strip(), count=1)
        if not pieces or (bullet and not indented) or (blank and not indented):
            close()
            pieces.append((False, text))
        else:
            pieces.append((bool(bullet), text))
        blank = False
    close()
    return lists


def _outcome_text(outcome: dict[str, Any]) -> str:
    time_frame = clean(outcome.get("timeFrame"))
    return _sentences(
        [
            clean(outcome.get("measure")),
            clean(outcome.get("description")),
            f"Time frame: {time_frame}" if time_frame else "",
        ]
    )


def _arm_text(arm: dict[str, Any]) -> str:
    label = clean(arm.get("label"))
    kind = _words(arm.get("type"))
    names = [clean(n) for n in arm.get("interventionNames") or [] if clean(n)]
    return _sentences(
        [
            f"{label} ({kind})" if kind else label,
            clean(arm.get("description")),
            "Interventions: " + "; ".join(names) if names else "",
        ]
    )


def _value(measurement: dict[str, Any]) -> str:
    value = clean(measurement.get("value"))
    low, high = clean(measurement.get("lowerLimit")), clean(measurement.get("upperLimit"))
    spread = clean(measurement.get("spread"))
    if low or high:
        return f"{value} ({low} to {high})"
    return f"{value} ({spread})" if spread else value


def _result_passages(measure: dict[str, Any]) -> list[tuple[str, str]]:
    """One posted outcome measure as (path suffix, text) pairs.

    "" holds what was measured and how many were analysed; "values.M" holds the Mth row
    of results per group; "analysis.M" holds the Mth statistical comparison. Splitting
    keeps each passage small enough to cite precisely.
    """
    groups = {str(g.get("id")): clean(g.get("title")) for g in measure.get("groups") or []}
    time_frame = clean(measure.get("timeFrame"))
    unit = clean(measure.get("unitOfMeasure"))
    kind = _words(measure.get("paramType"))
    dispersion = clean(measure.get("dispersionType"))
    head = [
        clean(measure.get("title")),
        clean(measure.get("description")),
        f"Time frame: {time_frame}" if time_frame else "",
        f"Unit: {unit}" if unit else "",
        f"Measure: {kind} ({dispersion})"
        if kind and dispersion
        else (f"Measure: {kind}" if kind else ""),
    ]
    for denom in (measure.get("denoms") or [])[:1]:
        counts = [
            f"{groups.get(c.get('groupId'), c.get('groupId'))}: {clean(c.get('value'))}"
            for c in denom.get("counts") or []
        ]
        if counts:
            head.append(f"Analysed ({clean(denom.get('units')).lower()}): " + "; ".join(counts))
    passages = [("", _sentences(head))]
    rows: list[str] = []
    for row in measure.get("classes") or []:
        for category in row.get("categories") or []:
            label = " - ".join(
                t for t in (clean(row.get("title")), clean(category.get("title"))) if t
            )
            values = [
                f"{groups.get(m.get('groupId'), m.get('groupId'))}: {_value(m)}"
                for m in category.get("measurements") or []
            ]
            if values:
                rows.append((f"{label}: " if label else "") + "; ".join(values))
    passages += [(f"values.{i}", _sentences([r])) for i, r in enumerate(rows, start=1)]
    analyses: list[str] = []
    for analysis in measure.get("analyses") or []:
        compared = " vs ".join(groups.get(str(g), str(g)) for g in analysis.get("groupIds") or [])
        estimate = " ".join(
            t for t in (clean(analysis.get("paramType")), clean(analysis.get("paramValue"))) if t
        )
        low, high = clean(analysis.get("ciLowerLimit")), clean(analysis.get("ciUpperLimit"))
        pct = clean(analysis.get("ciPctValue"))
        text = f"Analysis, {compared}: {estimate}" if compared else f"Analysis: {estimate}"
        if low or high:
            text += f" ({pct}% CI {low} to {high})" if pct else f" (CI {low} to {high})"
        if p_value := clean(analysis.get("pValue")):
            text += f", p={p_value}"
        analyses.append(text)
    passages += [(f"analysis.{i}", _sentences([a])) for i, a in enumerate(analyses, start=1)]
    return passages


def _summary(study: dict[str, Any]) -> dict[str, Any]:
    """The fields shared by the record's extras and the search hit."""
    ps = study.get("protocolSection") or {}
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    references = (ps.get("referencesModule") or {}).get("references") or []
    linked = [str(r["pmid"]) for r in references if r.get("type") == "RESULT" and r.get("pmid")]
    return {
        "status": status.get("overallStatus"),
        "phases": list(design.get("phases") or []),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "conditions": [
            clean(c) for c in (ps.get("conditionsModule") or {}).get("conditions") or []
        ],
        "interventions": [
            clean(i.get("name"))
            for i in (ps.get("armsInterventionsModule") or {}).get("interventions") or []
            if i.get("name")
        ],
        "has_results": bool(study.get("hasResults")),
        "linked_pmids": list(dict.fromkeys(linked)),
    }


def _record(study: dict[str, Any], retrieved_at: datetime) -> Record:
    ps = study.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    nct = str(ident["nctId"])
    version = (status.get("lastUpdatePostDateStruct") or {}).get("date")
    summary = _summary(study)
    extras: dict[str, JsonValue] = {
        **summary,
        "study_type": (ps.get("designModule") or {}).get("studyType"),
        "official_title": clean(ident.get("officialTitle")) or None,
        "sponsor": ((ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get(
            "name"
        ),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "completion_date": (status.get("completionDateStruct") or {}).get("date"),
    }
    return Record(
        record_id=f"nct:{nct}@{version}" if version else f"nct:{nct}",
        kind="trial_registration",
        ids=RecordIds(nct=nct),
        title=clean(ident.get("briefTitle")) or clean(ident.get("officialTitle")) or nct,
        published=_date((status.get("studyFirstPostDateStruct") or {}).get("date")),
        venue=VENUE,
        provenance=Provenance(
            source=SOURCE, endpoint="studies", retrieved_at=retrieved_at, upstream_version=version
        ),
        links=[Link(rel="has_results_in", target=f"pmid:{p}") for p in summary["linked_pmids"]],
        extras=extras,
    )


def parse_study(study: dict[str, Any], retrieved_at: datetime) -> RecordBundle:
    """A study as a trial record with passages in registry order (spec 4.3)."""
    record = _record(study, retrieved_at)
    ps = study.get("protocolSection") or {}
    drafts: list[PassageDraft] = []

    def add(prefix: str, label: str, texts: list[str]) -> None:
        for index, text in enumerate((t for t in texts if t), start=1):
            drafts.append(PassageDraft(path=f"{prefix}.{index}", text=text, section_label=label))

    add(
        "summary", "Brief summary", [clean((ps.get("descriptionModule") or {}).get("briefSummary"))]
    )
    criteria = eligibility_items((ps.get("eligibilityModule") or {}).get("eligibilityCriteria"))
    add("eligibility.inclusion", "Inclusion criteria", criteria["inclusion"])
    add("eligibility.exclusion", "Exclusion criteria", criteria["exclusion"])
    add("eligibility.other", "Eligibility", criteria["other"])
    outcomes = ps.get("outcomesModule") or {}
    add(
        "outcome.primary",
        "Primary outcome",
        [_outcome_text(o) for o in outcomes.get("primaryOutcomes") or []],
    )
    add(
        "outcome.secondary",
        "Secondary outcome",
        [_outcome_text(o) for o in outcomes.get("secondaryOutcomes") or []],
    )
    arms = (ps.get("armsInterventionsModule") or {}).get("armGroups") or []
    add("arm", "Arm", [_arm_text(a) for a in arms])
    measures = ((study.get("resultsSection") or {}).get("outcomeMeasuresModule") or {}).get(
        "outcomeMeasures"
    ) or []
    for index, measure in enumerate(measures, start=1):
        label = clean(measure.get("title")) or "Outcome result"
        for suffix, text in _result_passages(measure):
            path = f"results.outcome.{index}" + (f".{suffix}" if suffix else "")
            drafts.append(PassageDraft(path=path, text=text, section_label=label))
    return RecordBundle(record=record, passages=drafts)


def trial_hit(study: dict[str, Any], retrieved_at: datetime) -> TrialHit:
    record = _record(study, retrieved_at)
    return TrialHit.model_validate(
        {"record_id": record.record_id, "title": record.title, **_summary(study)}
    )
