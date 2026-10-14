# Contributing

Thank you for helping. Tekmeria is research software with one maintainer, so issues and pull
requests are answered on a best-effort basis.

## Before you start

- **A bug or a question:** open an [issue](https://github.com/PedroMarkovicz/tekmeria/issues).
  For a wrong verdict, include the answer text you audited and the report, and say whether you
  used the demo key: a handle verifies only on the installation that issued it.
- **A vulnerability:** report it privately, as [SECURITY.md](SECURITY.md) describes, not in an
  issue.
- **A larger change** (a new source, a new tool, a change to what the audit decides): open an
  issue first, so we can agree on the design before you write the code.

## Setting up

You need [uv](https://docs.astral.sh/uv/) and, for the audit view, Node.js 20 or later
(CI uses 24).

```bash
git clone https://github.com/PedroMarkovicz/tekmeria
cd tekmeria
uv sync
```

The tests run offline, on recorded responses. No API key or email is needed.

## The checks

A pull request runs the same checks as CI. Run them before you push:

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest
uv run tekmeria demo
uv run tekmeria evaluate
uv run python -m tekmeria.evaluation.faults --per-category 10
```

If you changed the audit view:

```bash
cd web
npm ci
npm run typecheck && npm test
npm run build            # rewrites src/tekmeria/ui/audit-view.html; commit it
```

CI rebuilds the audit view and fails if the committed file differs from the build.

## What a change needs

- **Tests.** A fix comes with a test that fails without it. A change to what the audit decides
  also needs a planted case in the evaluation (`src/tekmeria/evaluation/`), so that the
  evaluation would catch the change being undone.
- **The report models.** After changing them, run `uv run python -m tekmeria.contract.schemas`
  and commit the regenerated schema.
- **The citation contract.** [docs/CONTRACT.md](docs/CONTRACT.md) is versioned separately from
  the package, and its section 10 says which changes are breaking. A change to the contract
  updates that document, `CONTRACT_VERSION` and the contract tests in the same pull request.
- **Recorded responses.** Fixtures are redistributed with the repository, so they keep only text
  that may be redistributed (see [NOTICE](NOTICE)). Re-record them with
  `uv run tekmeria record-fixtures`, with no email or API keys set, rather than editing them by
  hand.
- **Design decisions.** A decision that changes how the server behaves is recorded in section 16
  of the [design document](docs/specs/2026-10-06-tekmeria-server-design.md), with its reason.
- **The changelog.** Add a line under the next version in [CHANGELOG.md](CHANGELOG.md).

## Style

- Python 3.12, formatted and linted by ruff and type-checked by pyright, strictly for
  `src/tekmeria/contract`; the configuration is in `pyproject.toml`.
- Commit messages start with a type and a colon (`fix:`, `docs:`, `ci:`, `chore:`), then say
  what the change does, in the present tense.
- Keep pull requests to one change each.

## Licence

By contributing, you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), as section 5 of that licence provides.
