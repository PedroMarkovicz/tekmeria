# medcite

Biomedical evidence over MCP, cited by handle. Every passage medcite returns comes with a short handle such as `pmid:30831578/abstract.7~rbhq7`. An agent cites those handles, and a deterministic audit then shows, for any model, which citations are genuine, which point to text that no longer exists, and which quotes are not verbatim.

![The audit view showing the offline demo's report](docs/assets/audit-view.png)

The demo's audit view is published at <https://pedromarkovicz.github.io/medcite/>.

## Why

- **More sources would not set this apart.** [BioMCP](https://github.com/genomoncology/biomcp) (MIT) already reaches dozens of biomedical sources.
- **Better retrieval barely helps.** Nourbakhsh et al. ([*When Retrieval Doesn't Help*](https://arxiv.org/abs/2606.04127)) found biomedical RAG improves on no retrieval by typically 1–2 points; the choice of model matters far more.
- **Citations are where models fail.**
  - When explicitly asked to cite, the best model in Ahmed et al. ([arXiv 2604.17114](https://arxiv.org/abs/2604.17114)) produced 15.3% relevant PMIDs; most of the rest resolved to real papers in unrelated fields.
  - In Chen et al. ([arXiv 2608.30676](https://arxiv.org/abs/2608.30676)), citation fabrication by a medical agent rose from 16.5% to 31.8% as reinforcement learning improved its accuracy.
- **MCP now points this way.** The [2026-07-28 MCP revision](https://modelcontextprotocol.io/specification/2026-07-28/changelog) makes servers stateless and says that servers needing state across calls use "explicit, server-minted handles passed as ordinary tool arguments".

So medcite does not compete on coverage. It makes every citation checkable.

## How it works

```
pmid:30831578/abstract.7~rbhq7
\__/ \______/ \________/ \___/
scheme  id      path     check code: HMAC of everything before the "~"
```

1. **Search and fetch.** The tools return records and passages from PubMed, Europe PMC (with preprints and open-access full text), ClinicalTrials.gov and openFDA drug labels, enriched with PubTator3 entities and Crossref retraction status. Every passage carries its handle.
2. **Answer.** The model writes its answer with citations in brackets: `[handle]`, or `[handle "exact words"]` for numbers and doses.
3. **Audit.** `audit_answer` checks each citation, in this order: is the handle well formed, did this server issue it, can its source be reached, does the passage still exist, and is the quote verbatim? It reports a verdict per citation, a status per sentence, metrics such as the number of fabricated citations, and an overall `pass`, `warn`, `fail` or `incomplete`.

The audit checks integrity, not support: whether a passage actually backs the claim is reported as `not_checked`. [docs/CONTRACT.md](docs/CONTRACT.md) has the full rules.

## Quickstart

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if needed). No API keys are required.

See the audit on the offline demo, in the terminal and in your browser:

```bash
uvx --from git+https://github.com/PedroMarkovicz/medcite medcite demo --open
```

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings, Developer, Edit Config) and restart:

```json
{
  "mcpServers": {
    "medcite": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/PedroMarkovicz/medcite", "medcite"]
    }
  }
}
```

Then start a chat with the `cite_by_handle` prompt. Hosts that support MCP Apps show `audit_answer` results as the audit view.

### VS Code

Add this to `.vscode/mcp.json`:

```json
{
  "servers": {
    "medcite": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/PedroMarkovicz/medcite", "medcite"]
    }
  }
}
```

### MCP Inspector

```bash
npx @modelcontextprotocol/inspector uvx --from git+https://github.com/PedroMarkovicz/medcite medcite
```

### Other ways to run it

| Command | What it does |
|---|---|
| `medcite` | Serves MCP over stdio. |
| `medcite --http --port 8000` | Serves Streamable HTTP on `http://127.0.0.1:8000/mcp` (localhost only). |
| `medcite --demo` | Serves the recorded demo data with the public demo key: no network needed. |
| `medcite demo` | Runs the offline demo and prints the audit. Add `--json-out PATH` for the report, `--html-out PATH` for the audit view, `--open` to open it. |

### Configuration

Everything is optional and read from the environment (or a `.env` file):

| Variable | Use |
|---|---|
| `NCBI_API_KEY` | Raises the PubMed limit from 3 to 10 requests per second. |
| `NCBI_EMAIL` | Contact address NCBI asks for; sent only when set. |
| `OPENFDA_API_KEY` | Lifts openFDA's 1,000 requests per day limit. |
| `CROSSREF_MAILTO` | Puts Crossref requests in its polite pool; sent only when set. |
| `MEDCITE_HANDLE_KEY` | The handle key, in hex. By default a random key is created on first run and stored in your user data folder. |
| `MEDCITE_CACHE_PATH` | Where the SQLite cache lives (default: your user cache folder). |
| `MEDCITE_LOG_LEVEL` | Log level for the JSON logs on stderr (default `INFO`). |
| `MEDCITE_TOOL_DEADLINE_S` | Seconds each tool call may take (default 45). |

## Tools

| Tool | What it does |
|---|---|
| `search_literature` | Searches PubMed and Europe PMC (preprints included), merged without duplicates. Filters: dates, study designs, peer review, open access, retractions. |
| `search_trials` | Searches ClinicalTrials.gov by condition, intervention or terms; filters by status, phase and posted results. |
| `search_drug_labels` | Finds every openFDA drug label naming a drug, newest first. |
| `fetch_passages` | Fetches records (`pmid:`, `pmcid:`, `ppr:`, `nct:`, `setid:`) and their passages, each with a handle; adds entities and retraction status. |
| `resolve_handles` | Looks up the passage behind each handle and says whether the handle is genuine. |
| `audit_answer` | Audits an answer's citations and returns the report; MCP Apps hosts show it as the audit view. |
| `describe_contract` | Returns the contract: handle grammar, verdicts, flags, metrics and the citing instructions. |

There is also a prompt, `cite_by_handle`, and two resource templates, `evidence://record/{record_id}` and `evidence://passage/{handle}`.

## The demo

`medcite demo` runs offline on recorded responses. It searches, fetches two papers, and audits an answer with six planted cases: a correct quote, a misquote, a forged check code, a malformed handle, a citation of a retracted paper and an uncited sentence. This is its audit:

```text
verdict: FAIL  (5 citations: 1 malformed, 1 forged, 1 quote_mismatch, 2 ok)
fabricated: 1  retracted cited: 1  cited sentences: 2/6  verbatim quotes: 1/2
[1] cited: A nationwide Danish cohort found no increased risk of autism after MMR vaccination.
    ok             pmid:30831578/abstract.7~rbhq7 "During 5 025 754 person-years of follow-up, 6517 children were diagnosed with autism"
[2] invalid_only: The same study concluded that MMR vaccination raises autism risk.
    quote_mismatch pmid:30831578/abstract.9~kmtk5 "MMR vaccination strongly increases the risk of autism"
                   closest: "MMR vaccination does not increase the risk for autism" (0.8491)
[3] invalid_only: A second cohort reported the same null association.
    forged         pmid:30831578/abstract.7~rbhq0
[4] invalid_only: Earlier reviews reached the same conclusion.
    malformed      pmid:30831578-abstract
[5] cited: A 1998 case series that proposed a link was later retracted.
    ok             pmid:9500320/abstract.4~5z8a5 [retracted]
[6] uncited: Parents should discuss the vaccination schedule with their paediatrician.
```

## Architecture

```
  PubMed   Europe PMC   PubTator3   Crossref   ClinicalTrials.gov   openFDA
     \          |            |          |               |              /
      +------------------- sources/: one adapter per upstream -------+
                              |   (rate limits, validation, cache)
                       EvidenceService: routing, merging, time budgets,
                              |   handle minting, handle resolution
                     contract/: handles, models, citation parsing, audit
                              |
                 server/: MCP tools, resources, prompt, audit view (ui://)
```

- `src/medcite/contract/` is pure and fully typed: the handle codec, the evidence and report models, citation parsing and the audit. The JSON Schema in `schemas/` is exported from these models.
- `src/medcite/sources/` has one adapter and one parser per upstream, and `service.py`, which routes requests, merges sources and resolves handles for the audit.
- `src/medcite/infra/` holds settings, keys, the rate-limited HTTP client, the SQLite cache and the recorded-fixture transports.
- `src/medcite/server/` is the thin MCP layer; `src/medcite/ui/audit-view.html` is the audit view, built from `web/` (React and TypeScript) and committed, so running medcite never needs Node.

The design is in [docs/specs](docs/specs/2026-10-06-medcite-server-design.md), with the decisions made while building it in its section 16.

## Development

```bash
uv sync
uv run pytest            # Python tests, offline (recorded fixtures)
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest -m live    # smoke tests against the real APIs
```

The audit view:

```bash
cd web
npm ci
npm test && npm run typecheck
npm run build            # rewrites src/medcite/ui/audit-view.html; commit it
```

After changing the report models, run `uv run python -m medcite.contract.schemas`. To re-record the fixtures from the live APIs, delete `src/medcite/demo/fixtures/*.json` and run `uv run medcite record-fixtures` (with no email or API keys set).

[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) tracks the acceptance criteria and how each one is checked.

## Limits

- **Not medical advice.** medcite retrieves and audits evidence; it does not judge it, and nothing it returns is clinical guidance.
- **Public data only.** It queries public biomedical APIs and is not built for patient data.
- **Integrity, not support.** The audit proves a citation is genuine, current and quoted verbatim. Whether the passage supports the sentence is not checked.
- **Current versions only.** ClinicalTrials.gov and openFDA serve only the current version of a record; an older cited version is flagged `version_changed`, but its old text can come only from medcite's cache.
- **Handles are per installation.** A handle verifies only on the installation (key) that issued it.

## License

Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
