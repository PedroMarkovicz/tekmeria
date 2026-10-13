"""The mutation harness changes one thing at a time, and the evaluation kills a real fault."""

import ast
import subprocess

import pytest

from tekmeria.evaluation import mutants
from tekmeria.evaluation.mutants import Outcome, render, sites

SOURCE = '''
def scored(a, b):
    """Docstring 1."""
    if a == b and not a:
        return True
    return a < 2


def skipped(a):
    return a == 1
'''


def _changes(source: str, module: str = "example") -> list[str]:
    return [site.change for site in sites(ast.parse(source), module)]


def test_every_operator_and_constant_is_a_site() -> None:
    assert _changes(SOURCE) == [
        "And -> Or",
        "Eq -> NotEq (negated)",
        "not removed",
        "True -> False",
        "Lt -> GtE (negated)",
        "Lt -> LtE (boundary)",
        "2 -> 3",
        "Eq -> NotEq (negated)",
        "1 -> 2",
    ]


def test_functions_out_of_scope_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    every = _changes(SOURCE)
    monkeypatch.setitem(mutants.OUT_OF_SCOPE, "example", {"skipped"})
    assert _changes(SOURCE) == every[:7]  # nothing from skipped()


def test_a_site_changes_only_its_own_node() -> None:
    tree = ast.parse(SOURCE)
    sites(tree, "example")[1].apply()
    assert "if a != b and (not a):" in ast.unparse(tree)
    assert "return a == 1" in ast.unparse(tree)


def test_removing_not_keeps_the_truth_value() -> None:
    tree = ast.parse("def f(a):\n    return not a\n")
    sites(tree, "example")[0].apply()
    namespace: dict[str, object] = {}
    exec(compile(ast.fix_missing_locations(tree), "f", "exec"), namespace)
    f = namespace["f"]
    assert callable(f) and f(0) is False and f(3) is True


def _index(module: str, change: str) -> int:
    return [site.change for site in sites(mutants.parse(module), module)].index(change)


def test_the_real_code_survives_and_a_broken_quote_check_gives_a_wrong_result() -> None:
    module = "tekmeria.contract.normalize"
    assert mutants.outcome_of(module, -1, 1) == mutants.SURVIVED  # index -1: no mutation
    broken = _index(module, "In -> NotIn (negated)")
    assert mutants.outcome_of(module, broken, 1) == mutants.WRONG_RESULT


def test_a_mutant_that_stops_the_snapshot_loading_is_not_evaluated() -> None:
    # The evidence service issues every passage's handle with the check code, so a broken
    # check code stops the snapshot from loading: the evaluation never gets to judge it.
    module = "tekmeria.contract.handles"
    assert mutants.outcome_of(module, _index(module, "31 -> 32"), 1) == mutants.NOT_LOADED


def _outcome(index: int, kind: str) -> Outcome:
    return Outcome(module=mutants.TARGETS[0], index=index, line=index, change="c", kind=kind)


def test_the_report_splits_kills_by_kind_and_leaves_unloaded_mutants_out_of_the_score() -> None:
    kinds = [mutants.WRONG_RESULT, mutants.CRASH, mutants.TIMEOUT, mutants.SURVIVED]
    outcomes = [_outcome(i, kind) for i, kind in enumerate([*kinds, mutants.NOT_LOADED])]
    text = render(outcomes)
    assert text.splitlines()[0] == (
        "mutation score: 3/4 mutants killed by the evaluation "
        "(1 by a wrong result, 1 by a crash, 1 by a timeout)"
    )
    assert text.splitlines()[1] == "not evaluated: 1 (the snapshot could not load)"
    assert f"| `{mutants.TARGETS[0]}` | 5 | 1 | 1 | 1 | 1 | 1 |" in text
    assert f"- `{mutants.TARGETS[0]}` line 3: c" in text  # the survivor is listed
    assert f"- `{mutants.TARGETS[0]}` line 4: c" in text  # and so is the unloaded mutant


def test_a_failing_evaluation_stops_the_run_before_any_mutant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Otherwise every mutant would count as killed, and the score would read 100%.
    monkeypatch.setattr(mutants, "outcome_of", lambda module, index, per_category: "crash")
    with pytest.raises(RuntimeError, match="reloaded and unchanged"):
        mutants.run_all(per_category=1, workers=1)


def test_every_target_is_checked_unchanged_before_any_mutant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reloading a module can break identity checks elsewhere; each target is checked alone.
    broken = mutants.TARGETS[-1]

    def outcome(module: str, index: int, per_category: int) -> str:
        return mutants.CRASH if module == broken else mutants.SURVIVED

    monkeypatch.setattr(mutants, "outcome_of", outcome)
    with pytest.raises(RuntimeError, match=broken):
        mutants.run_all(per_category=1, workers=1)


def test_a_mutant_that_hangs_counts_as_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def hangs(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="evaluate", timeout=mutants.TIMEOUT_SECONDS)

    monkeypatch.setattr(mutants.subprocess, "run", hangs)
    assert mutants.outcome_of("tekmeria.contract.normalize", 0, 1) == mutants.TIMEOUT
    assert _outcome(0, mutants.TIMEOUT).killed


def test_handle_parsing_only_the_service_uses_is_out_of_scope() -> None:
    # parse_record_id and RecordRef.at build handles for the evidence service; the audit
    # never calls them. Their mutants would break the snapshot's loading, not a planted case.
    module = "tekmeria.contract.handles"
    tree = mutants.parse(module)
    service_only = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"parse_record_id", "at"}
    ]
    lines = {n for f in service_only for n in range(f.lineno, (f.end_lineno or f.lineno) + 1)}
    assert len(service_only) == 2
    assert not {site.line for site in sites(tree, module)} & lines
