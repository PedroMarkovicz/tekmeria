"""How much of the audit the evaluation exercises: a mutation score (spec section 16.11).

Each mutant changes one operator or constant inside a function of the citation contract's
code. A mutant is killed when the evaluation then reports a wrong result, crashes, or runs past
the time limit, and survives when the evaluation cannot tell it from the real code. A survivor
is either a fault the evaluation misses or an equivalent mutant (one that changes no behavior);
the survivors are listed so a reader can tell which. A mutant that stops the snapshot from
loading is not evaluated: the evaluation never gets to judge it, so it is left out of the score.
String and regular-expression constants are not mutated.

    python -m tekmerion.evaluation.mutants [--per-category N] [--workers N] [--json-out PATH]
"""

import argparse
import ast
import contextlib
import importlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import anyio

TARGETS = (
    "tekmerion.contract.audit",
    "tekmerion.contract.citations",
    "tekmerion.contract.handles",
    "tekmerion.contract.normalize",
)
# Functions the evaluation does not score, so their mutants say nothing about it: the
# closest-match diagnostic (never a verdict), the check_handles tool, the key-length check,
# the cursor signature, and the record-ID parsing and passage addressing that only the
# evidence service uses to issue handles (a mutant there breaks loading the snapshot, not a
# planted case).
OUT_OF_SCOPE = {
    "tekmerion.contract.audit": {"check_handles"},
    "tekmerion.contract.handles": {"__init__", "sign", "parse_record_id", "at"},
    "tekmerion.contract.normalize": {"closest_match"},
}
TIMEOUT_SECONDS = 300  # a mutant that hangs the evaluation counts as killed
# What happened to a mutant. The child process reports it as its exit code.
SURVIVED, WRONG_RESULT, CRASH, NOT_LOADED, TIMEOUT = (
    "survived",
    "wrong result",
    "crash",
    "not loaded",
    "timeout",
)
_EXIT_CODES = {SURVIVED: 0, WRONG_RESULT: 1, CRASH: 2, NOT_LOADED: 3}
KILLS = (WRONG_RESULT, CRASH, TIMEOUT)
_NEGATED: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
_BOUNDARY: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}


@dataclass(frozen=True)
class Site:
    line: int
    change: str
    apply: Callable[[], None]


def _compare_sites(node: ast.Compare) -> Iterator[Site]:
    for index, op in enumerate(node.ops):
        for table, word in ((_NEGATED, "negated"), (_BOUNDARY, "boundary")):
            if type(op) in table:
                new = table[type(op)]

                def apply(index: int = index, new: type[ast.cmpop] = new) -> None:
                    node.ops[index] = new()

                yield Site(node.lineno, f"{type(op).__name__} -> {new.__name__} ({word})", apply)


def _sites_in(node: ast.AST) -> Iterator[Site]:
    if isinstance(node, ast.Compare):
        yield from _compare_sites(node)
    elif isinstance(node, ast.BoolOp):
        new_op = ast.Or if isinstance(node.op, ast.And) else ast.And

        def swap() -> None:
            node.op = new_op()

        yield Site(node.lineno, f"{type(node.op).__name__} -> {new_op.__name__}", swap)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        unary = node

        def drop() -> None:
            unary.operand = ast.UnaryOp(ast.Not(), unary.operand)  # `not not x` is `bool(x)`

        yield Site(node.lineno, "not removed", drop)
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        constant = node

        def flip() -> None:
            constant.value = not constant.value

        yield Site(node.lineno, f"{node.value} -> {not node.value}", flip)
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        number, bumped = node, node.value + 1

        def bump() -> None:
            number.value = bumped

        yield Site(node.lineno, f"{node.value} -> {bumped}", bump)


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def sites(tree: ast.Module, module: str) -> list[Site]:
    """Every mutation inside a function body in scope, in a fixed order."""
    skip = _docstrings(tree)
    found: list[Site] = []
    seen: set[int] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name in OUT_OF_SCOPE.get(module, ()):
            continue
        for statement in function.body:
            for node in ast.walk(statement):
                if id(node) in seen or id(node) in skip:
                    continue  # a nested function is walked once, and docstrings never
                seen.add(id(node))
                found.extend(_sites_in(node))
    return found


def _source(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin)


def parse(module: str) -> ast.Module:
    return ast.parse(_source(module).read_text(encoding="utf-8"))


def _run_mutant(module: str, index: int, per_category: int) -> str:
    """In a fresh interpreter: load `module` with mutation `index`, then run the evaluation.
    Returns what happened: SURVIVED, WRONG_RESULT, CRASH or NOT_LOADED."""
    tree = parse(module)
    if index >= 0:
        sites(tree, module)[index].apply()
        ast.fix_missing_locations(tree)
    parent, _, name = module.rpartition(".")
    package = importlib.import_module(parent)
    spec = importlib.util.spec_from_file_location(module, _source(module))
    assert spec is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[module] = loaded  # every later import of `module` gets the mutant
    exec(compile(tree, str(_source(module)), "exec"), loaded.__dict__)
    setattr(package, name, loaded)

    # Imported after the mutant is in place, so they see it.
    from tekmerion.__main__ import demo_config
    from tekmerion.evaluation.run import evaluate_snapshot
    from tekmerion.evaluation.snapshot import load_snapshot
    from tekmerion.server.runtime import open_runtime

    async def run() -> str:
        config = demo_config()
        try:
            async with open_runtime(config) as app:
                snapshot = await load_snapshot(app.service)
        except Exception:  # the mutant broke the evidence service, not a planted case
            return NOT_LOADED
        try:
            report = await evaluate_snapshot(
                snapshot, config.handle_key, seed=0, per_category=per_category
            )
        except Exception:
            return CRASH
        return SURVIVED if report.passed() else WRONG_RESULT

    with contextlib.redirect_stdout(io.StringIO()):
        return anyio.run(run)


@dataclass(frozen=True)
class Outcome:
    module: str
    index: int
    line: int
    change: str
    kind: str  # SURVIVED, WRONG_RESULT, CRASH, NOT_LOADED or TIMEOUT

    @property
    def killed(self) -> bool:
        return self.kind in KILLS


def outcome_of(module: str, index: int, per_category: int) -> str:
    """Run one mutant in a subprocess and say what happened to it."""
    command = [sys.executable, "-m", "tekmerion.evaluation.mutants", "--mutant", module]
    command += [str(index), "--per-category", str(per_category)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return TIMEOUT
    kinds = {code: kind for kind, code in _EXIT_CODES.items()}
    return kinds.get(result.returncode, CRASH)  # any other exit is an unexpected crash


def run_all(per_category: int, workers: int) -> list[Outcome]:
    for module in TARGETS:  # reloading a module must not by itself change any result
        if outcome_of(module, -1, per_category) != SURVIVED:
            raise RuntimeError(f"the evaluation fails with {module} reloaded and unchanged")
    jobs = [(m, i, site) for m in TARGETS for i, site in enumerate(sites(parse(m), m))]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        kinds = pool.map(lambda job: outcome_of(job[0], job[1], per_category), jobs)
        return [
            Outcome(module=m, index=i, line=site.line, change=site.change, kind=k)
            for (m, i, site), k in zip(jobs, kinds, strict=True)
        ]


def render(outcomes: list[Outcome]) -> str:
    evaluated = [o for o in outcomes if o.kind != NOT_LOADED]
    killed = [o for o in evaluated if o.killed]
    by_kind = {kind: sum(o.kind == kind for o in killed) for kind in KILLS}
    lines = [
        f"mutation score: {len(killed)}/{len(evaluated)} mutants killed by the evaluation "
        f"({by_kind[WRONG_RESULT]} by a wrong result, {by_kind[CRASH]} by a crash, "
        f"{by_kind[TIMEOUT]} by a timeout)",
    ]
    not_loaded = [o for o in outcomes if o.kind == NOT_LOADED]
    if not_loaded:
        lines.append(f"not evaluated: {len(not_loaded)} (the snapshot could not load)")
    lines += [
        "",
        "| Module | Mutants | Wrong result | Crash | Timeout | Survived | Not evaluated |",
        "|---|---|---|---|---|---|---|",
    ]
    for module in TARGETS:
        mine = [o for o in outcomes if o.module == module]
        counts = [sum(o.kind == kind for o in mine) for kind in (*KILLS, SURVIVED, NOT_LOADED)]
        lines.append(f"| `{module}` | {len(mine)} | " + " | ".join(map(str, counts)) + " |")
    for title, kind in (
        ("Survivors (a missed fault, or a mutant that changes nothing):", SURVIVED),
        ("Not evaluated (the snapshot could not load):", NOT_LOADED),
    ):
        listed = [o for o in outcomes if o.kind == kind]
        if listed:
            lines += ["", title]
            lines += [f"- `{o.module}` line {o.line}: {o.change}" for o in listed]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tekmerion.evaluation.mutants")
    parser.add_argument("--per-category", type=int, default=25)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--mutant", nargs=2, metavar=("MODULE", "INDEX"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.mutant:
        module, index = args.mutant
        sys.exit(_EXIT_CODES[_run_mutant(module, int(index), args.per_category)])
    outcomes = run_all(args.per_category, args.workers)
    print(render(outcomes))
    if args.json_out is not None:
        args.json_out.write_text(
            json.dumps([asdict(o) for o in outcomes], indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
