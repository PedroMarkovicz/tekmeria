"""The mutation harness changes one thing at a time, and the evaluation kills a real fault."""

import ast
import subprocess

import pytest

from tekmerion.evaluation import mutants
from tekmerion.evaluation.mutants import Outcome, render, sites

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


def test_the_real_code_survives_and_a_broken_quote_check_is_killed() -> None:
    module = "tekmerion.contract.normalize"
    assert not mutants.is_killed(module, -1, 1)  # index -1: no mutation
    changes = [site.change for site in sites(mutants.parse(module), module)]
    assert mutants.is_killed(module, changes.index("In -> NotIn (negated)"), 1)


def test_the_report_lists_survivors() -> None:
    outcomes = [
        Outcome(module=mutants.TARGETS[0], index=0, line=5, change="Eq -> NotEq", killed=True),
        Outcome(module=mutants.TARGETS[0], index=1, line=9, change="2 -> 3", killed=False),
    ]
    text = render(outcomes)
    assert text.startswith("mutation score: 1/2 mutants killed by the evaluation")
    assert f"- `{mutants.TARGETS[0]}` line 9: 2 -> 3" in text


def test_a_failing_evaluation_stops_the_run_before_any_mutant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Otherwise every mutant would count as killed, and the score would read 100%.
    monkeypatch.setattr(mutants, "is_killed", lambda module, index, per_category: True)
    with pytest.raises(RuntimeError, match="fails without any mutant"):
        mutants.run_all(per_category=1, workers=1)


def test_a_mutant_that_hangs_counts_as_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    def hangs(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="evaluate", timeout=mutants.TIMEOUT_SECONDS)

    monkeypatch.setattr(mutants.subprocess, "run", hangs)
    assert mutants.is_killed("tekmerion.contract.normalize", 0, 1)
