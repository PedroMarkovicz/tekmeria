from medcite.contract import CONTRACT_VERSION
from medcite.contract.audit_models import FLAG_NAMES, VERDICTS
from medcite.contract.descriptions import CITE_BY_HANDLE_INSTRUCTIONS, contract_description
from medcite.contract.handles import HANDLE_PATTERN


def test_every_verdict_and_flag_is_described_in_order() -> None:
    description = contract_description()
    assert [t.name for t in description.verdicts] == list(VERDICTS)
    assert [t.name for t in description.flags] == list(FLAG_NAMES)
    assert all(t.meaning for t in description.verdicts + description.flags + description.metrics)


def test_description_carries_version_pattern_and_instructions() -> None:
    description = contract_description()
    assert description.contract_version == CONTRACT_VERSION
    assert description.handle_pattern == HANDLE_PATTERN
    assert description.instructions == CITE_BY_HANDLE_INSTRUCTIONS
    assert "[handle" in CITE_BY_HANDLE_INSTRUCTIONS


def test_trial_and_label_paths_are_described() -> None:
    paths = {t.name for t in contract_description().passage_paths}
    assert {"summary.1", "eligibility.inclusion.N", "results.outcome.N", "<section_key>.N"} <= paths
