"""Europe PMC search results and JATS full text to records and passage drafts.

Pure functions: no network. Europe PMC is the source of record for preprints
(`ppr:`) and PubMed Central articles (`pmcid:`); PubMed stays the source of
record for anything cited as `pmid:`.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from tekmerion.contract.models import (
    Link,
    OpenAccess,
    Provenance,
    PublishedDate,
    Quality,
    Record,
    RecordIds,
    Retraction,
    Signal,
    StudyDesign,
)
from tekmerion.contract.normalize import normalize_text
from tekmerion.sources.base import PassageDraft, RecordBundle
from tekmerion.sources.pubmed_parse import PUBLICATION_TYPE_DESIGNS

SOURCE = "europepmc"
_HEADING_RE = re.compile(r"<(h[1-6]|title)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WORD_RE = re.compile(r"[a-z0-9]+")
# Europe PMC labels some PMC articles with JATS article types rather than MEDLINE ones.
_EXTRA_DESIGNS: dict[str, StudyDesign] = {"review-article": "review", "case-report": "case_report"}


def strip_markup(text: str) -> str:
    return normalize_text(_TAG_RE.sub(" ", text))


def split_abstract(abstract: str | None) -> list[tuple[str | None, str]]:
    """Split Europe PMC abstract markup into (heading, text) sections.

    Headings come as <h4>Background</h4> (PubMed records) or <title>Methods</title>
    (preprints). A lone "Abstract" heading is dropped. Text before the first heading
    has no label.
    """
    if not abstract:
        return []
    parts = _HEADING_RE.split(abstract)
    sections: list[tuple[str | None, str]] = []
    lead = strip_markup(parts[0])
    if lead:
        sections.append((None, lead))
    for index in range(1, len(parts), 3):
        heading = strip_markup(parts[index + 1])
        text = strip_markup(parts[index + 2]) if index + 2 < len(parts) else ""
        if not text:
            continue
        label = None if heading.lower() == "abstract" else heading or None
        sections.append((label, text))
    return sections


def _designs(publication_types: list[str]) -> Signal[list[StudyDesign]]:
    found: list[StudyDesign] = []
    for publication_type in publication_types:
        design = PUBLICATION_TYPE_DESIGNS.get(publication_type) or _EXTRA_DESIGNS.get(
            publication_type.lower()
        )
        if design is not None and design not in found:
            found.append(design)
    # Europe PMC types are not MEDLINE indexing: no match means unknown, not "other".
    return (
        Signal[list[StudyDesign]].known(found, SOURCE)
        if found
        else Signal[list[StudyDesign]].unknown(SOURCE)
    )


def _published(result: dict[str, Any]) -> PublishedDate | None:
    first = str(result.get("firstPublicationDate") or "")
    if _DATE_RE.match(first):
        return PublishedDate(date=first, precision="day")
    year = str(result.get("pubYear") or "")
    return PublishedDate(date=year, precision="year") if year.isdigit() else None


def _venue(result: dict[str, Any]) -> str | None:
    journal = (result.get("journalInfo") or {}).get("journal") or {}
    title = journal.get("title")
    if title:
        return str(title)
    publisher = (result.get("bookOrReportDetails") or {}).get("publisher")
    return str(publisher) if publisher else None


def _links(result: dict[str, Any]) -> list[Link]:
    links: list[Link] = []
    corrections = (result.get("commentCorrectionList") or {}).get("commentCorrection") or []
    for item in corrections:
        if item.get("type") == "Preprint of" and item.get("source") == "MED" and item.get("id"):
            links.append(Link(rel="published_as", target=f"pmid:{item['id']}"))
    return links


def record_id_for(result: dict[str, Any]) -> str:
    """How a search hit is cited: by PMID when it has one, else PMCID, else preprint ID."""
    if result.get("pmid"):
        return f"pmid:{result['pmid']}"
    if result.get("pmcid"):
        return f"pmcid:{result['pmcid']}"
    return f"ppr:{result['id']}"


def parse_result(
    result: dict[str, Any], retrieved_at: datetime, *, record_id: str | None = None
) -> RecordBundle:
    """One `resultType=core` search result as a record with title and abstract passages."""
    is_preprint = result.get("source") == "PPR"
    publication_types = [str(t) for t in (result.get("pubTypeList") or {}).get("pubType") or []]
    retracted = any(t.lower() == "retracted publication" for t in publication_types)
    record = Record(
        record_id=record_id or record_id_for(result),
        kind="preprint" if is_preprint else "journal_article",
        ids=RecordIds(
            pmid=result.get("pmid"),
            pmcid=result.get("pmcid"),
            doi=result.get("doi"),
            ppr=result.get("id") if is_preprint else None,
        ),
        title=strip_markup(str(result.get("title") or "")),
        published=_published(result),
        venue=_venue(result),
        provenance=Provenance(source=SOURCE, endpoint="search", retrieved_at=retrieved_at),
        quality=Quality(
            study_designs=Signal[list[StudyDesign]].unknown(SOURCE)
            if is_preprint
            else _designs(publication_types),
            publication_types=Signal[list[str]].known(publication_types, SOURCE),
            peer_reviewed=Signal[bool].known(not is_preprint, SOURCE),
            retraction=Signal[Retraction].known(Retraction(retracted=True), SOURCE)
            if retracted
            else Signal[Retraction].not_checked(),
            open_access_full_text=Signal[OpenAccess].known(
                OpenAccess(
                    available=result.get("isOpenAccess") == "Y",
                    license=result.get("license"),
                ),
                SOURCE,
            ),
        ),
        links=_links(result),
        extras={"europepmc_source": str(result.get("source") or "")},
    )
    passages = [PassageDraft(path="title", text=record.title)] if record.title else []
    for index, (label, text) in enumerate(split_abstract(result.get("abstractText")), 1):
        passages.append(PassageDraft(path=f"abstract.{index}", text=text, section_label=label))
    return RecordBundle(record=record, passages=passages)


def _slug(title: str | None, sec_type: str | None) -> str:
    words = [w for w in _WORD_RE.findall((sec_type or title or "").lower()) if not w.isdigit()]
    return "_".join(words[:3]) or "section"


def parse_full_text(xml_text: str) -> list[PassageDraft]:
    """Body paragraphs of a JATS article as `body.<section>[.<subsection>].pN` passages.

    Only <p> elements directly inside <body> or a <sec> become passages; tables,
    figures and boxes are skipped. Sibling sections with the same slug get _2, _3.
    """
    root = ET.fromstring(xml_text)
    body = root.find("body")
    if body is None:
        return []
    drafts: list[PassageDraft] = []

    def walk(element: ET.Element, prefix: str, label: str | None) -> None:
        index = 0
        for paragraph in element.findall("p"):
            text = normalize_text("".join(paragraph.itertext()))
            if text:
                index += 1
                drafts.append(
                    PassageDraft(path=f"{prefix}.p{index}", text=text, section_label=label)
                )
        used: dict[str, int] = {}
        for section in element.findall("sec"):
            title_element = section.find("title")
            title = (
                normalize_text("".join(title_element.itertext()))
                if title_element is not None
                else None
            )
            slug = _slug(title, section.get("sec-type"))
            used[slug] = used.get(slug, 0) + 1
            if used[slug] > 1:
                slug = f"{slug}_{used[slug]}"
            walk(section, f"{prefix}.{slug}", title or label)

    walk(body, "body", None)
    return drafts
