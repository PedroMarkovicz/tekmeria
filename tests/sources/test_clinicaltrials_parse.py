from datetime import UTC, datetime
from typing import Any

from medcite.sources.clinicaltrials_parse import eligibility_items, parse_study, trial_hit

NOW = datetime(2026, 10, 7, tzinfo=UTC)

CRITERIA = """Inclusion Criteria:

1. Subject age \\>18 hospitalized for acute HF
2. Dyspnoea and 1 of the following signs:

   * Congestion on chest X-ray
   * Rales on chest auscultation
3. NT-proBNP \\>=300 ng/L

Newly enrolled participants aged 18 to 55 years.

Exclusion Criteria: Cardiogenic shock

* eGFR \\<20 mL/min
* Type 1 diabetes mellitus
"""

STUDY: dict[str, Any] = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT01234567",
            "briefTitle": "Drug X in Heart Failure",
            "officialTitle": "A Randomized Trial of Drug X",
        },
        "statusModule": {
            "overallStatus": "COMPLETED",
            "lastUpdatePostDateStruct": {"date": "2025-11-02"},
            "studyFirstPostDateStruct": {"date": "2020-04-30"},
            "startDateStruct": {"date": "2020-05"},
            "completionDateStruct": {"date": "2023-02-10"},
        },
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Pharma"}},
        "descriptionModule": {"briefSummary": "Drug X\nversus placebo."},
        "conditionsModule": {"conditions": ["Heart Failure"]},
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE3"],
            "enrollmentInfo": {"count": 300},
        },
        "armsInterventionsModule": {
            "armGroups": [
                {
                    "label": "Drug X",
                    "type": "EXPERIMENTAL",
                    "description": "10 mg daily",
                    "interventionNames": ["Drug: Drug X"],
                },
                {"label": "Placebo", "type": "PLACEBO_COMPARATOR"},
            ],
            "interventions": [{"type": "DRUG", "name": "Drug X"}, {"name": "Placebo"}],
        },
        "outcomesModule": {
            "primaryOutcomes": [
                {
                    "measure": "Change in KCCQ score",
                    "description": "Higher is better",
                    "timeFrame": "90 days",
                }
            ],
            "secondaryOutcomes": [{"measure": "Death from any cause", "timeFrame": "1 year"}],
        },
        "eligibilityModule": {"eligibilityCriteria": CRITERIA},
        "referencesModule": {
            "references": [
                {"pmid": "33301246", "type": "RESULT"},
                {"pmid": "11111111", "type": "BACKGROUND"},
                {"pmid": "33301246", "type": "RESULT"},
            ]
        },
    },
    "resultsSection": {
        "outcomeMeasuresModule": {
            "outcomeMeasures": [
                {
                    "type": "PRIMARY",
                    "title": "Change in KCCQ score",
                    "timeFrame": "90 days",
                    "paramType": "MEAN",
                    "dispersionType": "Standard Deviation",
                    "unitOfMeasure": "points",
                    "groups": [
                        {"id": "OG000", "title": "Drug X"},
                        {"id": "OG001", "title": "Placebo"},
                    ],
                    "denoms": [
                        {
                            "units": "Participants",
                            "counts": [
                                {"groupId": "OG000", "value": "150"},
                                {"groupId": "OG001", "value": "148"},
                            ],
                        }
                    ],
                    "classes": [
                        {
                            "categories": [
                                {
                                    "measurements": [
                                        {"groupId": "OG000", "value": "5.1", "spread": "2.0"},
                                        {"groupId": "OG001", "value": "1.2", "spread": "2.1"},
                                    ]
                                }
                            ]
                        }
                    ],
                    "analyses": [
                        {
                            "groupIds": ["OG000", "OG001"],
                            "paramType": "Mean Difference (Net)",
                            "paramValue": "3.9",
                            "ciPctValue": "95",
                            "ciLowerLimit": "2.5",
                            "ciUpperLimit": "5.3",
                            "pValue": "<0.001",
                        }
                    ],
                }
            ]
        }
    },
    "hasResults": True,
}


def test_eligibility_items_split_lists_and_join_sub_bullets() -> None:
    items = eligibility_items(CRITERIA)
    assert items["inclusion"] == [
        "Subject age >18 hospitalized for acute HF",
        "Dyspnoea and 1 of the following signs: Congestion on chest X-ray; "
        "Rales on chest auscultation",
        "NT-proBNP >=300 ng/L",
        "Newly enrolled participants aged 18 to 55 years.",
    ]
    assert items["exclusion"] == [
        "Cardiogenic shock",
        "eGFR <20 mL/min",
        "Type 1 diabetes mellitus",
    ]
    assert items["other"] == []


def test_eligibility_without_headings_is_other() -> None:
    assert eligibility_items("Adults with asthma.\n\nNo smokers.") == {
        "inclusion": [],
        "exclusion": [],
        "other": ["Adults with asthma.", "No smokers."],
    }
    assert eligibility_items(None) == {"inclusion": [], "exclusion": [], "other": []}


def test_record_carries_version_links_and_extras() -> None:
    record = parse_study(STUDY, NOW).record
    assert record.record_id == "nct:NCT01234567@2025-11-02"
    assert record.kind == "trial_registration"
    assert record.ids.nct == "NCT01234567"
    assert record.title == "Drug X in Heart Failure"
    assert record.published is not None and record.published.date == "2020-04-30"
    assert record.venue == "ClinicalTrials.gov"
    assert record.provenance.upstream_version == "2025-11-02"
    assert [link.target for link in record.links] == ["pmid:33301246"]
    assert {link.rel for link in record.links} == {"has_results_in"}
    assert record.extras["linked_pmids"] == ["33301246"]
    assert record.extras["phases"] == ["PHASE3"]
    assert record.extras["sponsor"] == "Acme Pharma"
    assert record.extras["start_date"] == "2020-05"
    assert record.quality.study_designs.state == "not_checked"


def test_passages_follow_registry_order_with_spec_paths() -> None:
    passages = parse_study(STUDY, NOW).passages
    assert [p.path for p in passages] == [
        "summary.1",
        "eligibility.inclusion.1",
        "eligibility.inclusion.2",
        "eligibility.inclusion.3",
        "eligibility.inclusion.4",
        "eligibility.exclusion.1",
        "eligibility.exclusion.2",
        "eligibility.exclusion.3",
        "outcome.primary.1",
        "outcome.secondary.1",
        "arm.1",
        "arm.2",
        "results.outcome.1",
        "results.outcome.1.values.1",
        "results.outcome.1.analysis.1",
    ]
    text = {p.path: p.text for p in passages}
    assert text["summary.1"] == "Drug X versus placebo."
    assert (
        text["outcome.primary.1"] == "Change in KCCQ score. Higher is better. Time frame: 90 days."
    )
    assert text["outcome.secondary.1"] == "Death from any cause. Time frame: 1 year."
    assert text["arm.1"] == "Drug X (experimental). 10 mg daily. Interventions: Drug: Drug X."
    assert text["arm.2"] == "Placebo (placebo comparator)."
    assert text["results.outcome.1"] == (
        "Change in KCCQ score. Time frame: 90 days. Unit: points. "
        "Measure: mean (Standard Deviation). "
        "Analysed (participants): Drug X: 150; Placebo: 148."
    )
    assert text["results.outcome.1.values.1"] == "Drug X: 5.1 (2.0); Placebo: 1.2 (2.1)."
    assert text["results.outcome.1.analysis.1"] == (
        "Analysis, Drug X vs Placebo: Mean Difference (Net) 3.9 (95% CI 2.5 to 5.3), p=<0.001."
    )
    labels = {p.path: p.section_label for p in passages}
    assert labels["eligibility.exclusion.1"] == "Exclusion criteria"
    assert labels["results.outcome.1.analysis.1"] == "Change in KCCQ score"


def test_trial_hit_from_a_search_result_with_few_fields() -> None:
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT07245069", "briefTitle": "Dapagliflozin study"},
            "statusModule": {
                "overallStatus": "RECRUITING",
                "lastUpdatePostDateStruct": {"date": "2025-11-24"},
            },
            "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 100}},
        },
        "hasResults": False,
    }
    hit = trial_hit(study, NOW)
    assert hit.record_id == "nct:NCT07245069@2025-11-24"
    assert (hit.status, hit.phases, hit.enrollment) == ("RECRUITING", ["PHASE2"], 100)
    assert hit.conditions == [] and hit.interventions == [] and hit.linked_pmids == []
    assert hit.has_results is False


def test_study_without_version_has_an_unversioned_record_id() -> None:
    study = {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}}
    bundle = parse_study(study, NOW)
    assert bundle.record.record_id == "nct:NCT00000001"
    assert bundle.record.title == "NCT00000001"
    assert bundle.passages == []
