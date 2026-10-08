"""How much of the audit the evaluation exercises: a mutation score (spec section 16.11).

Each mutant changes one operator or constant inside a function of the citation contract's
code. A mutant is killed when `tekmerion evaluate` no longer passes with it, and survives when
the evaluation cannot tell it from the real code. A survivor is either a fault the evaluation
misses or an equivalent mutant (one that changes no behavior); the survivors are listed so a
reader can tell which.

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


def _run_mutant(module: str, index: int, per_category: int) -> bool:
    """In a fresh interpreter: load `module` with mutation `index`, then run the evaluation.
    True when the evaluation still passes."""
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

    from tekmerion.__main__ import _evaluate  # imported after the mutant is in place

    with contextlib.redirect_stdout(io.StringIO()):
        return anyio.run(_evaluate, 0, per_category, None)


@dataclass(frozen=True)
class Outcome:
    module: str
    index: int
    line: int
    change: str
    killed: bool


def is_killed(module: str, index: int, per_category: int) -> bool:
    """Run one mutant in a subprocess; True when it is killed."""
    command = [sys.executable, "-m", "tekmerion.evaluation.mutants", "--mutant", module]
    command += [str(index), "--per-category", str(per_category)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return True
    return result.returncode != 0


def run_all(per_category: int, workers: int) -> list[Outcome]:
    if is_killed(TARGETS[0], -1, per_category):
        raise RuntimeError("the evaluation fails without any mutant; fix that first")
    jobs = [(m, i, site) for m in TARGETS for i, site in enumerate(sites(parse(m), m))]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        killed = pool.map(lambda job: is_killed(job[0], job[1], per_category), jobs)
        return [
            Outcome(module=m, index=i, line=site.line, change=site.change, killed=k)
            for (m, i, site), k in zip(jobs, killed, strict=True)
        ]


def render(outcomes: list[Outcome]) -> str:
    killed = sum(o.killed for o in outcomes)
    lines = [
        f"mutation score: {killed}/{len(outcomes)} mutants killed by the evaluation",
        "",
        "| Module | Mutants | Killed |",
        "|---|---|---|",
    ]
    for module in TARGETS:
        mine = [o for o in outcomes if o.module == module]
        lines.append(f"| `{module}` | {len(mine)} | {sum(o.killed for o in mine)} |")
    survivors = [o for o in outcomes if not o.killed]
    if survivors:
        lines += ["", "Survivors (a missed fault, or a mutant that changes nothing):"]
        lines += [f"- `{o.module}` line {o.line}: {o.change}" for o in survivors]
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
        sys.exit(0 if _run_mutant(module, int(index), args.per_category) else 1)
    outcomes = run_all(args.per_category, args.workers)
    print(render(outcomes))
    if args.json_out is not None:
        args.json_out.write_text(
            json.dumps([asdict(o) for o in outcomes], indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
