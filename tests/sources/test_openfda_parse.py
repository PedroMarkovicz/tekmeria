from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tekmeria.contract.normalize import normalize_text
from tekmeria.sources.openfda_parse import chunk_text, label_hit, parse_label, section_keys

NOW = datetime(2026, 10, 7, tzinfo=UTC)
LONG = " ".join(f"Sentence number {i} says something about bleeding risk." for i in range(40))

LABEL: dict[str, Any] = {
    "set_id": "0cbce382-9c88-4f58-ae0f-532a841e8f95",
    "id": "b8c33971-54b3-4971-bf7f-1bec66999a29",
    "version": "20",
    "effective_time": "20250617",
    "openfda": {
        "brand_name": ["Coumadin"],
        "generic_name": ["WARFARIN SODIUM"],
        "manufacturer_name": ["Bristol-Myers Squibb"],
        "route": ["ORAL"],
        "rxcui": ["855288", "855296"],
        "pharm_class_epc": ["Vitamin K Antagonist [EPC]"],
        "pharm_class_moa": ["Vitamin K Inhibitors [MoA]"],
        "product_type": ["HUMAN PRESCRIPTION DRUG"],
    },
    "indications_and_usage": ["1 INDICATIONS AND USAGE Prophylaxis of venous thrombosis."],
    "boxed_warning": [f"WARNING: BLEEDING RISK {LONG}"],
    "zz_unlisted_section": ["Kept, after the listed sections."],
    "drug_interactions_table": ["<table>...</table>"],
    "package_label_principal_display_panel": ["PRINCIPAL DISPLAY PANEL"],
    "spl_product_data_elements": ["Coumadin warfarin sodium"],
    "description": ["  "],
}


def test_sections_in_prescribing_order_without_tables_or_packaging() -> None:
    assert section_keys(LABEL) == ["boxed_warning", "indications_and_usage", "zz_unlisted_section"]


def test_passages_are_chunks_of_each_section() -> None:
    passages = parse_label(LABEL, NOW).passages
    boxed = [p for p in passages if p.path.startswith("boxed_warning.")]
    assert [p.path for p in boxed] == [f"boxed_warning.{i}" for i in range(1, len(boxed) + 1)]
    assert len(boxed) > 1 and all(len(p.text) <= 1000 for p in boxed)
    assert " ".join(p.text for p in boxed) == normalize_text(LABEL["boxed_warning"][0])
    assert boxed[0].section_label == "Boxed warning"
    assert [p.path for p in passages if not p.path.startswith("boxed")] == [
        "indications_and_usage.1",
        "zz_unlisted_section.1",
    ]


def test_record_fields() -> None:
    record = parse_label(LABEL, NOW).record
    assert record.record_id == "setid:0cbce382-9c88-4f58-ae0f-532a841e8f95@20"
    assert record.kind == "drug_label"
    assert record.ids.setid == "0cbce382-9c88-4f58-ae0f-532a841e8f95"
    assert record.title == "Coumadin (WARFARIN SODIUM)"
    assert record.venue == "Bristol-Myers Squibb"
    assert record.published is not None and record.published.date == "2025-06-17"
    assert record.provenance.upstream_version == "20"
    assert record.extras["pharm_class"] == [
        "Vitamin K Antagonist [EPC]",
        "Vitamin K Inhibitors [MoA]",
    ]
    assert record.extras["has_boxed_warning"] is True


def test_hit_and_title_fallbacks() -> None:
    hit = label_hit(LABEL, NOW)
    assert hit.brand_names == ["Coumadin"] and hit.rxcui == ["855288", "855296"]
    assert (hit.effective_date, hit.routes, hit.has_boxed_warning) == ("2025-06-17", ["ORAL"], True)
    bare = {"set_id": "s", "version": "1", "openfda": {"generic_name": ["X"], "brand_name": ["x"]}}
    assert label_hit(bare, NOW).title == "x"
    nameless = label_hit({"set_id": "s", "version": "1"}, NOW)
    assert (nameless.title, nameless.manufacturer, nameless.effective_date) == (
        "Drug label s",
        None,
        None,
    )


@given(st.lists(st.text(alphabet="abcXY. ?!(", min_size=1, max_size=300), max_size=12))
def test_chunks_rejoin_to_the_text_and_respect_the_limit(parts: list[str]) -> None:
    text = normalize_text(" ".join(parts))
    pieces = chunk_text(text, limit=120)
    assert " ".join(pieces) == text
    assert all(p for p in pieces)
    for piece in pieces:
        assert len(piece) <= 120 or chunk_text(piece, limit=120) == [piece]


@pytest.mark.parametrize(
    ("elements", "title"),
    [
        ("Warfarin Sodium Warfarin WARFARIN SODIUM LACTOSE", "Warfarin Sodium"),
        (
            "Acetaminophen and Codeine Phosphate acetaminophen and codeine phosphate STARCH",
            "Acetaminophen and Codeine Phosphate",
        ),
        ("one two three four five six seven eight nine ten", "Drug label s"),
    ],
)
def test_a_label_without_openfda_names_is_titled_from_its_product_data(
    elements: str, title: str
) -> None:
    result = {"set_id": "s", "version": "1", "spl_product_data_elements": [elements]}
    hit = label_hit(result, NOW)
    assert hit.title == title
    assert hit.brand_names == [] and hit.generic_names == []


def test_set_ids_are_lower_case_in_the_record() -> None:
    upper = {**LABEL, "set_id": LABEL["set_id"].upper()}
    record = parse_label(upper, NOW).record
    assert record.record_id == "setid:0cbce382-9c88-4f58-ae0f-532a841e8f95@20"
    assert record.ids.setid == "0cbce382-9c88-4f58-ae0f-532a841e8f95"
