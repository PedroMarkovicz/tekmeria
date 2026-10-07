"""openFDA drug labels as label records and passages (spec sections 4.3 and 8)."""

import re
from datetime import datetime
from typing import Any

from pydantic import JsonValue

from medcite.contract.models import Provenance, PublishedDate, Record, RecordIds
from medcite.contract.normalize import normalize_text
from medcite.contract.tool_models import LabelHit
from medcite.sources.base import PassageDraft, RecordBundle

SOURCE = "openfda"
CHUNK_CHARS = 1000

# Fields that are metadata or packaging rather than label text; *_table fields hold HTML.
_NOT_TEXT = frozenset(
    {
        "set_id",
        "id",
        "version",
        "effective_time",
        "openfda",
        "spl_product_data_elements",
        "spl_indexing_data_elements",
        "package_label_principal_display_panel",
    }
)
# Prescribing-information order (21 CFR 201.57); other sections follow alphabetically.
SECTION_ORDER = (
    "boxed_warning",
    "indications_and_usage",
    "dosage_and_administration",
    "dosage_forms_and_strengths",
    "contraindications",
    "warnings_and_cautions",
    "warnings",
    "precautions",
    "adverse_reactions",
    "drug_interactions",
    "use_in_specific_populations",
    "pregnancy",
    "lactation",
    "pediatric_use",
    "geriatric_use",
    "drug_abuse_and_dependence",
    "overdosage",
    "description",
    "clinical_pharmacology",
    "mechanism_of_action",
    "pharmacodynamics",
    "pharmacokinetics",
    "nonclinical_toxicology",
    "clinical_studies",
    "how_supplied",
    "information_for_patients",
)
# A sentence ends at ., ! or ? followed by a space and an upper-case letter, digit or bracket.
_SENTENCE_END = re.compile(r"(?<=[.!?]) (?=[A-Z0-9(\[])")


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split normalized text at sentence ends into pieces of at most `limit` characters.

    A single sentence longer than `limit` stays whole. Joining the pieces with single
    spaces gives back the text, so no character is lost or changed.
    """
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return pieces


def section_keys(result: dict[str, Any]) -> list[str]:
    """Label text sections present in a result, in prescribing-information order."""
    present = [
        key
        for key, value in result.items()
        if key not in _NOT_TEXT
        and not key.endswith("_table")
        and isinstance(value, list)
        and any(isinstance(v, str) and v.strip() for v in value)
    ]
    known = [key for key in SECTION_ORDER if key in present]
    return known + sorted(key for key in present if key not in SECTION_ORDER)


def _names(openfda: dict[str, Any], field: str) -> list[str]:
    return list(dict.fromkeys(normalize_text(str(v)) for v in openfda.get(field) or [] if v))


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    openfda = result.get("openfda") or {}
    effective = str(result.get("effective_time") or "")
    brands, generics = _names(openfda, "brand_name"), _names(openfda, "generic_name")
    manufacturers = _names(openfda, "manufacturer_name")
    return {
        "brand_names": brands,
        "generic_names": generics,
        "manufacturer": manufacturers[0] if manufacturers else None,
        "effective_date": (
            f"{effective[:4]}-{effective[4:6]}-{effective[6:8]}" if len(effective) == 8 else None
        ),
        "routes": _names(openfda, "route"),
        "rxcui": _names(openfda, "rxcui"),
        "has_boxed_warning": "boxed_warning" in section_keys(result),
    }


def _title(set_id: str, brands: list[str], generics: list[str]) -> str:
    if brands and generics and brands[0].casefold() != generics[0].casefold():
        return f"{brands[0]} ({generics[0]})"
    return (brands or generics or [f"Drug label {set_id}"])[0]


def label_record_id(result: dict[str, Any]) -> str:
    return f"setid:{result['set_id']}@{result['version']}"


def _record(result: dict[str, Any], retrieved_at: datetime) -> Record:
    set_id, version = str(result["set_id"]), str(result["version"])
    openfda = result.get("openfda") or {}
    summary = _summary(result)
    pharm_class: list[JsonValue] = [
        *_names(openfda, "pharm_class_epc"),
        *_names(openfda, "pharm_class_moa"),
    ]
    product_types = _names(openfda, "product_type")
    extras: dict[str, JsonValue] = {
        **summary,
        "pharm_class": pharm_class,
        "product_type": product_types[0] if product_types else None,
        "spl_id": result.get("id"),
    }
    date = summary["effective_date"]
    return Record(
        record_id=label_record_id(result),
        kind="drug_label",
        ids=RecordIds(setid=set_id),
        title=_title(set_id, summary["brand_names"], summary["generic_names"]),
        published=PublishedDate(date=date, precision="day") if date else None,
        venue=summary["manufacturer"],
        provenance=Provenance(
            source=SOURCE,
            endpoint="drug/label.json",
            retrieved_at=retrieved_at,
            upstream_version=version,
        ),
        extras=extras,
    )


def parse_label(result: dict[str, Any], retrieved_at: datetime) -> RecordBundle:
    """A label as a record with `<section_key>.N` passages of at most ~1,000 characters."""
    drafts: list[PassageDraft] = []
    for key in section_keys(result):
        label = key.replace("_", " ").capitalize()
        texts = [normalize_text(v) for v in result[key] if isinstance(v, str)]
        pieces = [piece for text in texts if text for piece in chunk_text(text)]
        for index, piece in enumerate(pieces, start=1):
            drafts.append(PassageDraft(path=f"{key}.{index}", text=piece, section_label=label))
    return RecordBundle(record=_record(result, retrieved_at), passages=drafts)


def label_hit(result: dict[str, Any], retrieved_at: datetime) -> LabelHit:
    record = _record(result, retrieved_at)
    return LabelHit.model_validate(
        {"record_id": record.record_id, "title": record.title, **_summary(result)}
    )
