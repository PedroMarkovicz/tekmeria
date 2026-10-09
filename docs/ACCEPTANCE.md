# Acceptance

How each acceptance criterion in spec section 13 is checked, and where it stands. Criteria 1 to 5 and 8 are automated; 6 and 7 need a real MCP host and a real model, so they are manual and follow the checklist below. A test (`tests/test_acceptance_doc.py`) fails if a test named here no longer exists.

## Status

| # | Criterion | How it is checked | Status |
|---|---|---|---|
| 1 | Everything below works on a clean machine with no API keys and no network, except 6 and 7. | The CI jobs in `.github/workflows/ci.yml` run with no secrets; every server test replays recorded fixtures through `FixtureTransport`; `tekmerion demo` runs offline. | Automated |
| 2 | `tekmerion demo` detects all six planted cases, with the expected verdicts, flags, claim statuses and hand-computed metrics. | `tests/demo/test_demo.py::test_demo_detects_every_planted_case` | Automated |
| 3 | `tools/list` returns the seven tools in a fixed order, each with an `outputSchema`, and every tool result validates against its schema. | Each tool's output is validated against its schema in at least one test: `tests/server/test_server_offline.py::test_tools_listed_in_fixed_order_with_schemas`, plus the schema-validating `_call` helpers in `tests/server/test_server.py::test_search_returns_hits_with_title_handles`, `tests/server/test_server_literature.py::test_default_search_merges_both_sources` and `tests/server/test_server_registries.py::test_trial_search_pages_without_repeats` | Automated |
| 4 | CI passes: lint, types, Python tests and web tests. | The `python` and `web` jobs in `.github/workflows/ci.yml` | Automated; first run when the repository is pushed |
| 5 | The audit view renders the demo report on GitHub Pages, and keyboard navigation reaches every citation chip. | `.github/workflows/pages.yml` publishes `tekmerion demo --html-out`; `web/src/AuditView.test.tsx::are all reachable with the keyboard` | Keyboard: automated. Publication: pending, since Pages needs a public repository (or a paid plan) and is not enabled yet |
| 6 | The audit view renders inside an MCP Apps host (Claude Desktop), documented with a screenshot in the README. | Manual: checklist A; the screenshot is `docs/assets/host-audit-view.png` | Done (2026-10-07): Claude Desktop rendered the audit view as a widget, under the project's earlier name (spec 16.8) |
| 7 | A live run in Claude Desktop or VS Code, using the `cite_by_handle` prompt, gives an answer whose audit is `pass` or `warn` with zero fabricated citations; the transcript goes in the README. | Manual: checklist B; transcripts in `docs/live-run.md` | Done (2026-10-07): run 2 audited `warn` with 0 fabricated (16 of 16 citations genuine, 4 of 4 quotes verbatim). Run 1 failed on one forged citation, the instructions' own example handle repeated by the model (fixed, spec 16.6) |
| 8 | `docs/CONTRACT.md` matches the implementation. | `tests/contract/test_contract_doc.py::test_every_value_in_the_code_is_documented` and `tests/contract/test_contract_doc.py::test_verdicts_are_listed_in_precedence_order` | Automated |

## Manual checklist

Both checks use Claude Desktop with tekmerion installed from GitHub. They need network access to the public biomedical APIs, and no API keys.

### Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. Open Claude Desktop's configuration (Settings, Developer, Edit Config) and add:

   ```json
   {
     "mcpServers": {
       "tekmerion": {
         "command": "uvx",
         "args": ["--from", "git+https://github.com/PedroMarkovicz/tekmerion", "tekmerion"]
       }
     }
   }
   ```

3. Run `uvx --from git+https://github.com/PedroMarkovicz/tekmerion tekmerion --help` once in a terminal. The first run downloads and installs tekmerion, which can take longer than Claude Desktop waits for a server to start.

   While the repository is private, `uvx` needs your GitHub credentials to fetch it (Git Credential Manager provides them on Windows). Alternatively, point `--from` at a local checkout, for example `"args": ["--from", "C:/path/to/tekmerion", "tekmerion"]`.

4. Restart Claude Desktop. tekmerion's seven tools should appear in the tools menu.

### A. The audit view inside the host (criterion 6)

1. Start a new chat and ask: *"Use tekmerion's audit_answer to audit this answer: Vaccines are tested [pmid:1/title~abcde]."*
2. The result should render as the audit view inside the chat: the verdict on top, and the sentence with a red "Forged" chip.
3. Take a screenshot of the rendered view and save it as `docs/assets/host-audit-view.png`.
4. If the host shows only text, note the Claude Desktop version; the structured result and the text are still complete, as the spec requires.

### B. A live run citing by handle (criterion 7)

1. Start a new chat with the `cite_by_handle` prompt and the question *"Does MMR vaccination cause autism?"*. If the host lists MCP prompts (for example as slash commands), pick it there; the Claude Desktop version used on 2026-10-07 did not, so paste the prompt text instead (`describe_contract` returns it, as `instructions`).
2. Let the model search and fetch, then answer with handle citations.
3. Ask: *"Now call audit_answer on your answer exactly as written."*
4. The audit must be `pass` or `warn`, with `fabricated: 0`. If it is `fail`, keep the transcript anyway: it is a finding, not a failed check of this checklist.
5. Copy the whole exchange (question, the model's answer with its citations, and the audit's text output, including the count of citations by verdict) into `docs/live-run.md`, with the date, the Claude Desktop version and the model name at the top. The counts matter because `warn` is also reached when a citation is a `quote_mismatch` or `withdrawn`, not only when a sentence is uncited.

### After both

Add `docs/assets/host-audit-view.png` and `docs/live-run.md`, then mark criteria 6 and 7 as done in the table above, with the date. The README gets a short "In a chat" section with the screenshot and a "A real run" section linking the transcript.
