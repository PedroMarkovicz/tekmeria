"""PubMed EFetch XML to records and passage drafts (spec section 8, PubMed row).

Pure functions: no network. Paths are exact (`PubmedData/ArticleIdList`), so
IDs of cited references never leak into the record.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime

from pydantic import JsonValue

from tekmeria.contract.models import (
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
from tekmeria.contract.normalize import normalize_text
from tekmeria.sources.base import PassageDraft, RecordBundle

SOURCE = "pubmed"
_MONTHS = {
    m: f"{i:02d}"
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}
_DOI_IN_REFSOURCE = re.compile(r"doi:\s*(10\.\S+?)\.?$", re.IGNORECASE)
_NCT_RE = re.compile(r"^NCT\d{8}$")

# MEDLINE publication type -> study design (spec 5.2). Never inferred from text.
PUBLICATION_TYPE_DESIGNS: dict[str, StudyDesign] = {
    "Meta-Analysis": "meta_analysis",
    "Systematic Review": "systematic_review",
    "Randomized Controlled Trial": "randomized_controlled_trial",
    "Clinical Trial": "clinical_trial",
    "Clinical Trial, Phase I": "clinical_trial",
    "Clinical Trial, Phase II": "clinical_trial",
    "Clinical Trial, Phase III": "clinical_trial",
    "Clinical Trial, Phase IV": "clinical_trial",
    "Controlled Clinical Trial": "clinical_trial",
    "Pragmatic Clinical Trial": "clinical_trial",
    "Equivalence Trial": "clinical_trial",
    "Observational Study": "observational_study",
    "Case Reports": "case_report",
    "Review": "review",
    "Scoping Review": "review",
    "Guideline": "guideline",
    "Practice Guideline": "guideline",
    "Consensus Development Conference": "guideline",
    "Consensus Development Conference, NIH": "guideline",
}


def _text(element: ET.Element | None) -> str:
    return normalize_text("".join(element.itertext())) if element is not None else ""


def _published(article: ET.Element) -> PublishedDate | None:
    pub_date = article.find("Journal/JournalIssue/PubDate")
    if pub_date is None:
        return None
    year = pub_date.findtext("Year")
    month = pub_date.findtext("Month")
    day = pub_date.findtext("Day")
    if year is None:
        medline = pub_date.findtext("MedlineDate") or ""
        match = re.match(r"(\d{4})(?:\s+([A-Z][a-z]{2}))?", medline)
        if not match:
            return None
        year, month, day = match.group(1), match.group(2), None
    if month is not None and not month.isdigit():
        month = _MONTHS.get(month[:3])
    if month is None:
        return PublishedDate(date=year, precision="year")
    month = month.zfill(2)
    if day is None:
        return PublishedDate(date=f"{year}-{month}", precision="month")
    return PublishedDate(date=f"{year}-{month}-{day.zfill(2)}", precision="day")


def study_designs(publication_types: list[str]) -> list[StudyDesign]:
    found: list[StudyDesign] = []
    for publication_type in publication_types:
        design = PUBLICATION_TYPE_DESIGNS.get(publication_type)
        if design is not None and design not in found:
            found.append(design)
    return found or ["other"]


def _notice_doi(comment: ET.Element) -> str | None:
    match = _DOI_IN_REFSOURCE.search(comment.findtext("RefSource") or "")
    return match.group(1) if match else None


def parse_article(article_xml: ET.Element, retrieved_at: datetime) -> RecordBundle:
    citation = article_xml.find("MedlineCitation")
    if citation is None:
        raise ValueError("PubmedArticle without MedlineCitation")
    pmid = citation.findtext("PMID") or ""
    article = citation.find("Article")
    if article is None:
        raise ValueError(f"PMID {pmid}: no Article element")
    status = citation.get("Status") or ""

    ids = {
        (element.get("IdType") or ""): (element.text or "").strip()
        for element in article_xml.findall("PubmedData/ArticleIdList/ArticleId")
    }
    publication_types = [
        _text(element) for element in article.findall("PublicationTypeList/PublicationType")
    ]
    indexed = status == "MEDLINE"
    retraction_notices = [
        comment
        for comment in citation.findall("CommentsCorrectionsList/CommentsCorrections")
        if comment.get("RefType") == "RetractionIn"
    ]
    retracted = bool(retraction_notices) or "Retracted Publication" in publication_types
    notice_doi = _notice_doi(retraction_notices[-1]) if retraction_notices else None

    links: list[Link] = []
    for comment in citation.findall("CommentsCorrectionsList/CommentsCorrections"):
        target = comment.findtext("PMID")
        if not target:
            continue
        if comment.get("RefType") == "RetractionIn":
            links.append(Link(rel="retracted_by", target=f"pmid:{target.strip()}"))
        elif comment.get("RefType") == "ErratumIn":
            links.append(Link(rel="erratum_in", target=f"pmid:{target.strip()}"))
    for bank in article.findall("DataBankList/DataBank"):
        if (bank.findtext("DataBankName") or "") != "ClinicalTrials.gov":
            continue
        for accession in bank.findall("AccessionNumberList/AccessionNumber"):
            nct = (accession.text or "").strip()
            if _NCT_RE.match(nct):
                links.append(Link(rel="results_of", target=f"nct:{nct}"))

    mesh: list[JsonValue] = []
    for heading in citation.findall("MeshHeadingList/MeshHeading"):
        descriptor = heading.find("DescriptorName")
        if descriptor is None:
            continue
        qualifiers: list[JsonValue] = [_text(q) for q in heading.findall("QualifierName")]
        mesh.append(
            {
                "descriptor": _text(descriptor),
                "major": descriptor.get("MajorTopicYN") == "Y",
                "qualifiers": qualifiers,
            }
        )

    record = Record(
        record_id=f"pmid:{pmid}",
        kind="preprint" if "Preprint" in publication_types else "journal_article",
        ids=RecordIds(pmid=pmid, pmcid=ids.get("pmc"), doi=ids.get("doi")),
        title=_text(article.find("ArticleTitle")),
        published=_published(article),
        venue=_text(article.find("Journal/Title")) or None,
        provenance=Provenance(source=SOURCE, endpoint="efetch", retrieved_at=retrieved_at),
        quality=Quality(
            study_designs=(
                Signal[list[StudyDesign]].known(study_designs(publication_types), SOURCE)
                if indexed
                else Signal[list[StudyDesign]].unknown(SOURCE)
            ),
            publication_types=Signal[list[str]].known(publication_types, SOURCE),
            peer_reviewed=Signal[bool].known("Preprint" not in publication_types, SOURCE),
            retraction=Signal[Retraction].known(
                Retraction(retracted=retracted, notice_doi=notice_doi), SOURCE
            ),
            medline_indexed=Signal[bool].known(indexed, SOURCE),
            open_access_full_text=Signal[OpenAccess].not_checked(),
        ),
        links=links,
        extras={"medline_status": status, "mesh_terms": mesh},
    )

    passages = [PassageDraft(path="title", text=record.title)] if record.title else []
    sections = article.findall("Abstract/AbstractText")
    index = 0
    for section in sections:
        text = _text(section)
        if not text:
            continue
        index += 1
        passages.append(
            PassageDraft(path=f"abstract.{index}", text=text, section_label=section.get("Label"))
        )
    return RecordBundle(record=record, passages=passages)


def parse_efetch(xml_text: str, retrieved_at: datetime) -> list[RecordBundle]:
    root = ET.fromstring(xml_text)
    return [parse_article(element, retrieved_at) for element in root.findall("PubmedArticle")]
