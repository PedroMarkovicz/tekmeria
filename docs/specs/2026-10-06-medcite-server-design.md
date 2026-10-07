# medcite: evidence server with cite-by-handle (spec 1)

- **Date:** 2026-10-06
- **Status:** design approved section by section; written spec awaiting review
- **Working name:** `medcite` (free on PyPI on 2026-10-06; can be renamed before implementation)
- **Contract version introduced:** 0.1.0

## 1. Context

### 1.1 Where this comes from

This rewrites an unfinished project, `healthcare_mcp_codespaces`. That project is a FastAPI REST service with nine tools over public biomedical APIs. Its MCP surface is limited to an `echo` tool. Its outputs are not reliable enough for downstream retrieval or clinical-reasoning agents:

- **Inconsistent shapes.** Article shapes differ between sources, and some tools return an object or a list depending on how many matches they find.
- **Wrong or misleading values.** Structured abstracts are truncated. Evidence grades are guessed from title substrings. Gene fields always come back as empty lists. Preprint search uses exact substrings. ICD-10 chapters are wrong.

The rewrite keeps the old project's ideas: one adapter per upstream source, explicit provenance states (from its PubTator fallback), PubTator3 entity normalization, and public non-PHI data only. It does not reuse code wholesale.

### 1.2 Why this direction

These findings were checked on 2026-10-05:

- **Source coverage is commoditised.** BioMCP (MIT) reaches roughly 70 biomedical sources. Adding sources does not differentiate a project.
- **Better retrieval barely moves accuracy.** A 2026 study (Nourbakhsh et al., *When Retrieval Doesn't Help*, arXiv 2606.04127) found that retrieval improves biomedical QA by only 1–2 points over no retrieval. The backbone model matters far more.
- **Citations are the open failure.**
  - Ahmed et al. (arXiv 2604.17114) report that the best of five frontier models produced 15.3% relevant PMIDs when asked to cite.
  - Chen et al. (arXiv 2608.30676) report citation fabrication rising from 16.5% to 31.8% under outcome-only training.
- **The MCP spec now favours stateless servers.** The 2026-07-28 revision removes sessions and recommends "explicit, server-minted handles passed as ordinary tool arguments".

### 1.3 Thesis

**Cite by handle.** The server mints a short handle for every passage it returns. Agents cite only handles. A deterministic audit then shows, on any model:

- whether each cited handle was really issued by the server
- whether the cited text still exists
- whether quoted spans are verbatim

Whether a passage *supports* a claim is a separate, semantic question. It is out of scope here and is reported as `not_checked`.

### 1.4 The intended outcome

| | |
|---|---|
| **What the user said** | Build an agnostic protocol layer that can feed later RAG systems and diagnostic or treatment agents. Maximise novelty and relevance; effort is not a constraint. Build a portfolio piece first, with academic novelty later. Spend nothing at first; later, API-only on a small budget. The headline result is faithfulness on evidence-grounded clinical question answering. |
| **Assumed, not yet challenged by the user** | Python. Free public APIs only. No patient data ever. A single developer. A fresh repository, not the old one. No hosted public server in spec 1. |
| **Success for spec 1** | A visitor can install the server with one command, or open a public page, and watch a fabricated citation get caught, with no keys, no network and no LLM. The contract is documented and stable enough for spec 2's agent and evaluation to build on. |

## 2. Scope

### 2.1 In scope

- One MCP server (MCP Python SDK v2, `MCPServer`). It targets protocol 2026-07-28 and still serves 2025-11-25 clients.
- Seven tools, two resource templates, one UI resource and one prompt (section 6).
- Sources:
  - PubMed
  - Europe PMC: search, preprints, and open-access full text
  - PubTator3 entities
  - Crossref retraction status
  - ClinicalTrials.gov v2
  - openFDA drug labels
- A deterministic citation audit (section 7).
- An audit view in React and TypeScript, served as an MCP App and as a static GitHub Pages site (section 10).
- A reproducible offline demo, CI, and `CONTRACT.md`.

### 2.2 Out of scope (later specs)

- The agent loop, model evaluation, and any LLM calls.
- Semantic support judging and stance (supports or contradicts).
- Retrieval over a fetched corpus: hybrid lexical and dense search, graph retrieval.
- Binding handles to a run.
- Further sources: GDC, cBioPortal, NCBI Gene, ICD-10 and other terminologies, guidelines.
- Diagnosis workflows.
- Hosting the server remotely, and authentication. The HTTP transport binds to localhost only.

## 3. Architecture

```
any MCP client ── stdio  or  Streamable HTTP on 127.0.0.1 ──▶ medcite server
src/medcite/
  contract/   evidence model, handle codec, normalization, audit logic. Pure: no I/O, imports nothing from medcite
  sources/    one adapter per upstream; maps raw responses into contract types
  infra/      HTTP clients, per-host rate limiters, retries, SQLite cache, settings, logging
  server/     MCP tool, resource and prompt definitions only; thin
  demo/       offline demo runner and fixture wiring
web/          React + TypeScript audit view (Vite single-file build)
```

Dependencies point inward only: `server → sources → contract`. `infra` is used by `sources` and `server`, never by `contract`.

The server keeps **no session state**:

- Handles verify themselves (section 4).
- Pagination cursors are opaque, server-minted, and encode everything needed to continue.
- The cache speeds things up and preserves passage text as issued (section 9.2). It is never needed to decide whether a handle is genuine.

## 4. Handles

### 4.1 Grammar

```
handle   = locator "~" check
locator  = scheme ":" id [ "@" version ] "/" path
scheme   = "pmid" | "pmcid" | "ppr" | "nct" | "setid"
check    = 5 characters of lowercase Crockford base32 (0-9 a-h j k m n p-t v-z)
```

The regular expression used for validation:

```
^(pmid|pmcid|ppr|nct|setid):([A-Za-z0-9-]+)(?:@([A-Za-z0-9.-]+))?/([a-z0-9_]+(?:\.[a-z0-9_]+)*)~([0-9a-hjkmnp-tv-z]{5})$
```

Examples:

```
pmid:38421676/abstract.2~7hq3k
pmcid:PMC10012345/body.methods.p3~m2c9x
setid:1d2e3f40-aaaa-bbbb-cccc-123456789abc@14/boxed_warning.1~p0c8m
nct:NCT04368728@2025-11-02/outcome.primary.1~a91tz
```

### 4.2 Versions

| Scheme | Version | Meaning |
|---|---|---|
| `pmid`, `pmcid`, `ppr` | none | Treated as stable. |
| `nct` | `YYYY-MM-DD` | The trial's last-update post date. |
| `setid` | integer | The SPL label version. |

The public ClinicalTrials.gov and openFDA APIs serve only the current version. A version in a handle therefore records what was cited; it does not guarantee that the same text can be fetched again (section 7.3).

### 4.3 Passage paths

Path segments are lowercase. Indices are 1-based and follow document order.

| Kind | Paths |
|---|---|
| Article (PubMed) | `title`, `abstract.N` (an unlabelled abstract is `abstract.1`) |
| Full text (Europe PMC JATS) | `body.<section_slug>.pN`, plus `abstract.N` and `title` |
| Trial | `summary.1`, `eligibility.inclusion.N`, `eligibility.exclusion.N`, `outcome.primary.N`, `outcome.secondary.N`, `arm.N`, `results.outcome.N` |
| Drug label | `<section_key>.N`, where `section_key` is the openFDA field name (for example `boxed_warning`, `indications_and_usage`, `contraindications`, `drug_interactions`) and `N` is the paragraph |

### 4.4 Check code

- **Computation:** `check = crockford32(HMAC-SHA256(key, utf8(locator)))[:25 bits]`, lowercased.
- **What it covers:** the locator only, not the passage text. The check code answers "did this server issue this handle?". The content checks in section 7 answer "does the cited text still say this?".
- **How strong it is:** a guessed handle passes about once in 33.5 million tries. That is enough to catch fabrication. It is not meant to stop a determined attacker.
- **Key:** 32 random bytes generated on first run and stored in the platform user data directory (`platformdirs`) as `medcite/handle.key`.
  - `MEDCITE_HANDLE_KEY` (hex) overrides the stored key.
  - The demo uses a fixed, published demo key, which is accepted only when the server is started with `--demo`.
- **Consequence:** handles issued by one installation do not verify on another.

### 4.5 Text normalization

Applied to passage text when it is issued, and to quotes before they are compared:

1. Unicode NFC.
2. Collapse all whitespace runs to a single space, and trim.
3. **For comparison only:** fold typographic variants. Curly quotes become straight quotes; en dash, em dash and minus sign become a hyphen; non-breaking space becomes a space; ellipsis becomes three dots.

Normalization must be idempotent.

## 5. Evidence model

All types are Pydantic v2 models in `contract/`. JSON Schemas (2020-12) are generated from them, and those schemas are the source of the MCP `outputSchema`s and the frontend's TypeScript types.

### 5.1 Signal state

Every quality signal or enrichment that might be missing is wrapped:

```
Signal[T] = { state: "known" | "unknown" | "not_checked", value: T | null, sources: [str] }
```

- `known`: the value was determined, and `sources` lists where it came from.
- `unknown`: it was checked, but the upstream does not say. For example, an article not yet MEDLINE-indexed has no publication types.
- `not_checked`: the server did not look in this call. For example, search results do not query Crossref.

An empty list inside a `known` signal means "checked, none found". An empty list never stands in for "not loaded".

### 5.2 Record

```
Record {
  record_id: str                 # canonical, e.g. "pmid:38421676", "nct:NCT04368728@2025-11-02", "setid:…@14"
  kind: "journal_article" | "preprint" | "trial_registration" | "drug_label"
  ids: { pmid?, pmcid?, doi?, ppr?, nct?, setid? }
  title: str
  published: { date: str, precision: "day" | "month" | "year" } | null
  venue: str | null              # journal name, preprint server, or label manufacturer
  provenance: { source: str, endpoint: str, retrieved_at: datetime, upstream_version: str | null }
  quality: {
    study_designs: Signal[list[StudyDesign]]
    publication_types: Signal[list[str]]           # MEDLINE publication types, verbatim
    peer_reviewed: Signal[bool]
    retraction: Signal[{ retracted: bool, notice_doi: str | null }]
    medline_indexed: Signal[bool]                  # articles only
    open_access_full_text: Signal[{ available: bool, license: str | null }]
  }
  links: [ { rel: "results_of" | "has_results_in" | "retracted_by" | "preprint_of" | "published_as" | "erratum_in", target: str } ]
  extras: dict                   # kind-specific fields: MeSH terms, trial status/phase/enrolment, label routes/RxCUI/pharm class
}
```

`StudyDesign` is mapped **only** from MEDLINE publication types, never from title or abstract text. Its values:

- `meta_analysis`
- `systematic_review`
- `randomized_controlled_trial`
- `clinical_trial`
- `observational_study`
- `case_report`
- `review`
- `guideline`
- `other`

There is no evidence-level grade. Ranking evidence is the agent's job.

### 5.3 Passage

```
Passage {
  handle: str
  record_id: str
  path: str                      # e.g. "abstract.2"
  section_label: str | null      # e.g. "RESULTS", "Boxed Warning"
  text: str                      # normalized (section 4.5 steps 1–2)
  entities: Signal[list[Entity]]
}
Entity { id: str, type: "gene" | "disease" | "chemical" | "species" | "mutation" | "cell_line", text: str, span: [int, int] }
```

- **Entity spans** are offsets into `Passage.text`.
- **Entity IDs** use PubTator3's namespaces (for example `NCBIGene:672`, `MESH:D001943`).
- **Alignment:** if PubTator offsets cannot be mapped onto the normalized passage text exactly, that passage's `entities` is `unknown`. Spans are never approximated.

## 6. MCP surface

### 6.1 Conventions

- **Outputs.** Every tool declares an `outputSchema` and returns `structuredContent` that validates against it. It also returns a compact text rendering: one line per item, handle first, for clients that only pass text to the model.
- **Ordering.** Results are deterministic for a given input and upstream state.
- **Pagination.** Uses an opaque `cursor`: base64url JSON of the normalized query plus upstream offsets, with an HMAC under the handle key. A tampered cursor returns `invalid_input`.
- **Errors.** Errors are tool results with `isError: true` and this body:

  ```
  ToolError { code: "invalid_input" | "not_found" | "upstream_unavailable" | "upstream_error" | "rate_limited" | "deadline_exceeded" | "internal",
              message: str, retryable: bool, hint: str | null }
  ```

- **Partial failures.** These are not errors. Each result carries `source_status: { <source>: "ok" | "degraded" | "unavailable" }`, and the affected fields are marked `not_checked` or `unknown`.
- **Tool annotations.**
  - Search and fetch tools: `readOnlyHint: true`, `openWorldHint: true`.
  - `audit_answer` and `resolve_handles`: `readOnlyHint: true`, `idempotentHint: true`.
  - `describe_contract`: `readOnlyHint: true`.
- **Tool order.** `tools/list` returns tools in a fixed order, which the 2026-07-28 spec recommends so clients and LLM prompt caches can reuse it.

### 6.2 Tools

| Tool | Input | Output |
|---|---|---|
| `search_literature` | `query` (PubMed syntax accepted), `sources` (`pubmed`, `europepmc`; default both), `date_from`/`date_to` (`YYYY` or `YYYY-MM-DD`), `study_designs`, `peer_reviewed_only`, `open_access_only`, `exclude_retracted`, `limit` 1–50 (default 10), `cursor` | `hits[]`: record summary (`record_id`, `kind`, `title`, `title_handle`, `preview` of at most 300 characters, `published`, `venue`, `study_designs`, `peer_reviewed`, `retraction`, `open_access_full_text`, `ids`), plus `next_cursor` and `source_status`. Duplicates are merged by PMID, then PMCID, then DOI. |
| `search_trials` | `condition`, `intervention`, `terms`, `statuses`, `phases`, `has_results`, `limit` 1–50, `cursor` | `hits[]`: trial summary (`record_id` with version, `title`, `status`, `phase`, `enrollment`, `conditions`, `interventions`, `has_results`, `linked_pmids`), plus `next_cursor` and `source_status` |
| `search_drug_labels` | `drug` (brand, generic or substance name), `limit` 1–50, `cursor` | `hits[]`: label summary (`record_id` as `setid:…@version`, `brand_names`, `generic_names`, `manufacturer`, `effective_date`, `routes`, `rxcui`, `has_boxed_warning`), plus `next_cursor` and `source_status`. Returns every matching label, not just the first. |
| `fetch_passages` | `record_ids` (1–20), `sections` (optional path prefixes), `full_text` (default false; open-access only), `include_entities` (default true), `max_passages` (default 60 across the call), `cursor` | `records[]`: full `Record` and `passages[]`, plus `next_cursor` and `source_status`. Runs the Crossref retraction check for records with a DOI. |
| `resolve_handles` | `handles` (1–50) | For each handle: `status` (the same values as the audit verdicts in section 7.3, except `quote_mismatch`), `passage` when resolvable, and `flags` |
| `audit_answer` | Exactly one of `answer_text` or `claims` (section 7.1), plus optional `fail_on` | `AuditReport` (section 7.4). Declares the audit view UI resource (section 10). |
| `describe_contract` | none | `contract_version`, the handle grammar, verdict and flag definitions, metric definitions, and the `cite_by_handle` instruction text |

Inputs that MEDLINE publication types and other upstream filters support are pushed upstream:

- PubMed publication-type filters for `study_designs`.
- Europe PMC `PUB_TYPE` and `OPEN_ACCESS`.
- PubMed's `Retracted Publication` type for `exclude_retracted`.

In search results:

- **Retraction.** `retraction` is `known` when PubMed metadata states it. Otherwise it is `not_checked`, because Crossref is consulted only during fetch.
- **Study design.** `study_designs` is `known` or `unknown` from MEDLINE publication types, which are already in the search response.

### 6.3 Resources and prompt

- `evidence://record/{record_id}`: a resource template returning the `Record` as JSON.
- `evidence://passage/{handle}`: a resource template returning the `Passage` as JSON. An invalid handle returns the spec's not-found error (`-32602`).
- `ui://medcite/audit-view`: the audit view HTML as an MCP App resource (section 10).
- `cite_by_handle(question)`: a prompt that instructs a model to:
  - answer only from passages it has fetched
  - cite one to three handles per sentence, in the form `[handle; handle]`
  - quote verbatim spans as `[handle "span"]` when making specific numeric or dosing claims
  - never invent handles

## 7. Audit

### 7.1 Input

- **`answer_text`.** Free text with inline citation groups in square brackets.
  - A group holds handles separated by `;`.
  - Each handle can be followed by an optional quote: `[pmid:…~7hq3k "reduced mortality by 12%"]`.
  - A bracket group counts as a citation group if any of its tokens matches `^[a-z]+:\S+`. Other brackets, such as `[1]`, are left as plain text.
- **`claims`.** Structured input: `[{ sentence: str, citations: [{ handle: str, quote: str | null }] }]`.

### 7.2 Claims

For `answer_text`:

1. Citation groups are extracted with their character positions and replaced by placeholders.
2. The text is split into sentences with pySBD (deterministic and rule-based; English, clean mode off).
3. Each group is attached to the sentence that contains its placeholder.

In spec 1, one sentence is one claim. For `claims` input, sentences are taken exactly as given.

### 7.3 Citation verdicts

Checks run in this order, and the first failure decides the verdict:

| Order | Verdict | Condition |
|---|---|---|
| 1 | `malformed` | Does not match the grammar in section 4.1. |
| 2 | `forged` | The check code does not equal the HMAC of the locator. |
| 3 | `unavailable` | The handle is genuine but the upstream source cannot be reached, after retries and within the deadline. Marked `retryable: true`. Never counted as fabrication. |
| 4 | `withdrawn` | The record no longer exists upstream, or the passage path no longer exists in the current record. |
| 5 | `quote_mismatch` | A quote was given and is not a substring of the passage text after section 4.5 normalization and folding. |
| 6 | `ok` | Everything above passed. |

For a `quote_mismatch`, the report also includes `closest_match` (the most similar window of quote length) and its `similarity`, from 0 to 1. These are for diagnosis only and never change the verdict.

**Which text quotes are checked against:**

- If the cache holds the passage text as issued, quotes are checked against that.
- Otherwise they are checked against the current upstream text.
- The report records which one was used, in `checked_against: "issued" | "current"`.

**Flags.** Flags are attached to citations and do not change the verdict:

- `retracted`, with the notice's DOI.
- `preprint`: not peer-reviewed.
- `version_changed`: the upstream's current version differs from the version in the handle.

### 7.4 Report

```
AuditReport {
  contract_version: str
  audited_at: datetime
  claims: [ { index, sentence, status: "cited" | "uncited" | "invalid_only",
              citations: [ { handle, quote, verdict, flags, checked_against, resolved_version,
                             closest_match?, similarity?, passage?: Passage, support: "not_checked" } ] } ]
  summary: {
    sentences, cited_sentences, uncited_sentences, invalid_only_sentences,
    citations, by_verdict: { <verdict>: int }, quotes_checked, quotes_ok,
    citation_validity: float | null,   # ok / (citations - unavailable); null if the denominator is 0
    sentence_coverage: float | null,   # cited_sentences / sentences; null if sentences is 0
    quote_accuracy: float | null,      # quotes_ok / quotes_checked (excluding unavailable); null if 0
    fabricated: int,                   # = by_verdict.forged
    retracted_cited: int               # ok citations with the retracted flag
  }
  verdict: "pass" | "warn" | "fail" | "incomplete"
  fail_on: [str]
}
```

How a claim's `status` is decided:

- `cited`: at least one citation is `ok`.
- `uncited`: there are no citations.
- `invalid_only`: there are citations, but none is `ok`.

How the overall `verdict` is decided:

- `fail_on` defaults to `["forged", "malformed"]`. It may add any verdict, plus `uncited` and `retracted`.
- The verdict is `fail` if any item in `fail_on` occurs.
- Otherwise it is `incomplete` if any citation is `unavailable`.
- Otherwise it is `warn` if any sentence is uncited or any `ok` citation is retracted.
- Otherwise it is `pass`.

**Reproducibility.** The same input, the same handle key and the same upstream content always produce the same report, apart from `audited_at`. Handle resolution is batched per source and goes through the cache.

## 8. Sources

| Source | Calls | Records and passages | Notes |
|---|---|---|---|
| PubMed (NCBI E-utilities) | ESearch (JSON) and EFetch (XML) | Article records. Passages are `title` and `abstract.N`, built with `itertext()` so inline markup keeps its text. | Extracts publication types, MEDLINE status (`MedlineCitation/@Status`), MeSH terms, ISO dates with precision, the ID crosswalk from `ArticleIdList`, NCT IDs from `DataBankList` (giving `results_of` links), and `CommentsCorrectionsList` `RetractionIn` and `ErratumIn` (giving links and the retraction signal). Sends `tool` and `email` as NCBI requires. |
| Europe PMC REST | `search` (`resultType=core`) and `{PMCID}/fullTextXML` | Search hits from MED, PMC and PPR. Full-text passages from JATS `<body>` sections and paragraphs, only when the article is in the open-access subset. | Records the licence per article. PPR records get `kind: preprint` and `peer_reviewed: known(false)`, plus `published_as` links where Europe PMC gives them. |
| PubTator3 | `publications/export/biocjson`, batched up to 100 PMIDs | Entity annotations on title and abstract passages | Offset alignment as in section 5.3. A PMID that is not indexed gives `entities: unknown`. Full-text entities are deferred. |
| Crossref REST | `works?filter=updates:{doi}` | Retraction signal | `update-type: retraction` from either the publisher or `retraction-watch`. Merged with PubMed's signal, and `sources` lists both. Uses the polite pool via `mailto`. |
| ClinicalTrials.gov v2 | `studies` (search) and `studies/{nct}` | Trial records. Passages as in section 4.3. | Version is `lastUpdatePostDate`. References of type RESULT give `has_results_in` links and `linked_pmids`. |
| openFDA | `drug/label.json` | Drug label records. One passage per paragraph of each label text section. | Extracts set ID, version, effective time, routes, RxCUI and pharmacologic class (EPC, then MOA). No regex extraction of pharmacokinetics. |

## 9. Infrastructure

### 9.1 HTTP, limits and retries

- **Clients.** One `httpx.AsyncClient` per upstream host. They are opened when the server starts and closed when it stops, with connection pooling and per-request timeouts of 10 s to connect and 30 s to read.
- **Rate limits.** An async token-bucket limiter per host. The table gives starting defaults. During implementation, each must be checked against the provider's current documentation and the documentation link recorded in code.

  | Host | Default limit |
  |---|---|
  | NCBI E-utilities and PubTator3 | 3 requests per second; 10 per second with `NCBI_API_KEY` (PubTator3 stays at 3) |
  | Europe PMC | 5 requests per second |
  | Crossref | 5 requests per second |
  | ClinicalTrials.gov | 50 requests per minute |
  | openFDA | 240 requests per minute |

- **Retries.** At most three, with exponential backoff and full jitter, only for 429, 5xx and network errors. `Retry-After` is honoured.
- **Deadline.** Every tool call has an overall deadline (default 45 s), after which it returns `deadline_exceeded`. Results already gathered from other sources are kept, with `source_status` set accordingly.

### 9.2 Cache

SQLite through `aiosqlite`, at a configurable path (default: the user cache directory). It has two tables:

- **`responses`.** Raw upstream responses keyed by method, URL and normalized parameters, each with a TTL:

  | Data | TTL |
  |---|---|
  | PubMed, Europe PMC and PubTator records | 7 days |
  | Crossref, trials and labels | 1 day |
  | Search result pages | 1 hour |

- **`issued_passages`.** For each handle issued: the passage text, its SHA-256, the record version and the time issued. It is used for `checked_against: "issued"` and for detecting `version_changed`. Rows do not expire.

Deleting the cache never changes a verdict when the upstream content is unchanged. When the upstream content has changed, the cache lets the audit check against what was originally issued.

### 9.3 Configuration and logging

- **Configuration.** Settings come from environment variables through `pydantic-settings`, and all are optional:
  - `NCBI_API_KEY`
  - `NCBI_EMAIL`
  - `OPENFDA_API_KEY`
  - `CROSSREF_MAILTO`
  - `MEDCITE_HANDLE_KEY`
  - `MEDCITE_CACHE_PATH`
  - `MEDCITE_LOG_LEVEL`
- **Logging.** `structlog` writes JSON to **stderr**, because stdout belongs to the stdio transport. Each tool call produces one line recording the tool, duration, upstream calls with their status and cache hit, and `source_status`.
- **What is never logged:** API keys and the handle key.

## 10. Audit view (frontend)

- **Stack:** React with TypeScript, built by Vite with `vite-plugin-singlefile` into one self-contained `audit-view.html`. There is no component library, no Tailwind and no state manager; styling is plain CSS.
- **Shared types:** TypeScript types are generated with `json-schema-to-typescript` from `schemas/audit_report.schema.json`. That schema is exported from the Pydantic models by a script that CI runs. If the schema changes, the frontend build breaks.
- **What the view shows:**
  - The summary numbers and the verdict at the top.
  - The answer, sentence by sentence, with each citation as a chip.
  - Each chip shows its verdict with an icon *and* a text label, never colour alone.
  - Activating a chip, by hover or by keyboard focus and Enter, shows the cited passage with the quoted span highlighted. For a `quote_mismatch` it shows the closest match instead.
  - Flags (retracted, preprint, version changed) appear as badges.
  - Uncited sentences are visibly marked.
- **Inside a chat (MCP App):** `audit_answer` declares `ui://medcite/audit-view` in its tool metadata, as the MCP Apps extension `io.modelcontextprotocol/ui` (stable 2026-01-26) specifies. The view uses `@modelcontextprotocol/ext-apps` to receive the tool result. Hosts that do not negotiate the extension get the same `structuredContent` and text.
- **Standalone page:** the same HTML loads a report embedded at build time. A GitHub Pages workflow builds the site from the demo's report output.
- **Packaging:** the built HTML ships inside the Python wheel as package data. Running the server never requires Node.
- **Tests:** Vitest with Testing Library renders fixture reports, at least one per verdict, flag and claim status.

## 11. Testing

- **`contract/`.** Hypothesis property tests, which must show that:
  - handles round-trip through encode and parse
  - changing any single character of a valid handle makes it `malformed` or `forged`
  - normalization is idempotent
  - every substring of a normalized passage matches as a quote
  - an edited substring does not match unless the only change is a typographic fold

  A table-driven test pins the verdict precedence, and unit tests cover the metric definitions, including the `null` denominators.
- **Adapters.** Recorded upstream responses (fixtures under `tests/fixtures/<source>/`) are replayed with `respx`, and the parsed records and passages are compared against snapshots with `syrupy`. The fixtures must include:
  - a structured abstract with inline markup
  - an article not yet MEDLINE-indexed
  - a retracted article, with both PubMed and Crossref signals
  - a preprint with a published version
  - an open-access full-text article
  - a trial with posted results and a linked PMID
  - a drug label with a boxed warning and several versions in its metadata
  - a PubTator response whose offsets cannot be aligned
- **Server.** An in-process MCP client over the SDK's memory transport, against the fixtures. It must:
  - list the tools and check their order
  - call every tool
  - validate each `structuredContent` against its tool's `outputSchema`
  - check the text rendering
  - exercise the `ToolError` paths and partial `source_status`
- **CI (GitHub Actions).** Runs ruff, ruff format check, pyright (strict on `contract/`, basic elsewhere), pytest, and the web build, typecheck and test. CI uses no network and no keys.
- **Live smoke tests.** Tests marked `live` run weekly in a scheduled workflow and can also be run manually. They are not part of CI.

## 12. Demo, packaging and documentation

- **Running it.** `uvx medcite` starts the stdio server, and `uvx medcite --http` serves Streamable HTTP on 127.0.0.1.
- **The demo.** `uvx medcite demo` runs entirely offline, with the demo key and the fixtures: search, then fetch, then audit. The planted answer contains:
  - a valid citation with a correct quote
  - a genuine handle with a misquote
  - a real PMID with a forged check code
  - a malformed handle
  - a citation of a retracted article
  - an uncited sentence

  It prints the report as text, writes `demo-report.json` (which the GitHub Pages site uses), and with `--open` opens the audit view in a browser.
- **Packaging.** A uv project on Python 3.12 or later, licensed Apache-2.0. Publishing to PyPI is optional, since `uvx --from git+…` also works.
- **`docs/CONTRACT.md`.** Documents the handle grammar, the paths, the verdicts and their precedence, the flags, the metric definitions and the versioning policy (semver; a breaking change bumps the major version, or the minor version while still 0.x). A test checks that every verdict and flag value in the code is documented.
- **README.** Covers:
  - the problem, with the citations from section 1.2
  - the contract, in brief
  - a quickstart, with configuration snippets for Claude Desktop, VS Code and MCP Inspector
  - a recording of the demo and a link to the GitHub Pages audit view
  - a transcript of a real model citing by handle
  - the architecture
  - the limits: not medical advice; public data only; the audit checks integrity, not support

## 13. Acceptance criteria

1. Every item below is met on a clean machine with no API keys and no network, except criteria 6 and 7.
2. `uvx medcite demo` detects all six planted cases, with the expected verdicts, flags and claim statuses, and its summary metrics match hand-computed values.
3. `tools/list` returns the seven tools in a fixed order, each with an `outputSchema`, and every tool result in the server tests validates against its schema.
4. CI passes: lint, types, Python tests and web tests.
5. The audit view renders the demo report on GitHub Pages. Keyboard navigation reaches every citation chip.
6. The audit view renders inside at least one MCP Apps host (Claude Desktop). This is checked manually and documented with a screenshot in the README.
7. A live run in Claude Desktop or VS Code, using the `cite_by_handle` prompt, produces an answer whose audit is `pass` or `warn` with zero fabricated citations. The transcript goes in the README.
8. `docs/CONTRACT.md` matches the implementation, which is enforced by the test in section 12.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Upstream APIs change shape | Snapshot tests catch parser changes, and the weekly live smoke tests catch upstream drift. |
| PubTator offsets do not align with normalized text | Mark the passage's entities `unknown` instead of guessing, and keep a fixture for the case. |
| JATS full-text structure varies widely | Fall back to coarser paragraphs under `body.pN`, and record unsupported structure in logs. |
| Models copy handles imperfectly | Keep the check code short (5 characters). Report `malformed` separately from `forged`, so copy errors are not counted as fabrication. |
| MCP Apps behaves differently across hosts | Text and `structuredContent` always carry the full report, and the view is an enhancement. |
| Sentence splitting fails on biomedical text | Use fixture tests with abbreviations, decimals and units. `claims` input avoids splitting entirely. |
| Full-text licensing | Serve only the open-access subset and record the licence for each record. |
| NCBI usage policy | Send `tool` and `email`, and respect the rate limits. |

## 15. Later specs (for orientation, not commitments)

1. **Spec 2: agent and evaluation.** An agent that cites by handle, and an evaluation with models.
2. **Spec 3: workspace retrieval.** A just-in-time corpus over fetched passages, with hybrid lexical and dense retrieval and graph retrieval over `links` and entities.
3. **Spec 4: coverage.** Terminology normalization (ICD-10 and others), genomics sources, guidelines, and a diagnosis slice.
4. **Hosting** a remote server, with auth and abuse protection.

## 16. Amendments

Decisions made while implementing the project, stage by stage. Where they differ from the sections above, these win.

### 16.1 Core server

- **7.1 Citation syntax.** Entries in a citation group may be separated by `;` or `,`. Backticks, asterisks and underscores around a handle are removed. A quote runs from the first quote mark after the handle to the last one, so it may contain straight quotes and brackets. A handle-shaped token outside any bracket group still counts as a citation, so no handle can vanish from the audit.
- **7.2 Placement.** A citation group that opens a sentence (`Claim one. [h] Claim two.`) belongs to the previous sentence.
- **7.3 Which text a quote is checked against.** A quote is verbatim if it matches the passage text as first issued or the passage as it is now. `checked_against` reports which one matched. The closest-match diagnostic uses the current text and is bounded: the first 200 characters of the quote, at most 120 windows.
- **7.3 Deadline.** Handle resolution gets 80% of the tool deadline. When upstream does not answer in time, the affected citations are `unavailable` and the rest of the report is still returned.
- **5.2 `peer_reviewed`.** This means "published in a journal, not a preprint". It does not promise that every article type (letters, editorials) was formally peer-reviewed.
- **6.2 Limits.** An `answer_text` whose sentences exceed the claim limits (10 citations per sentence, 1,000-character quotes, 4,000-character sentences) returns `invalid_input` with a hint naming the limits.
- **9.1 Response validation.** Each upstream response is validated before it is cached: the expected JSON shape, or the expected XML root such as `PubmedArticleSet` or a JATS `article`. An invalid body is an `upstream_error` and is never cached.
- **9.3 Settings.** `MEDCITE_TOOL_DEADLINE_S` sets the tool deadline (default 45). An empty `MEDCITE_HANDLE_KEY` counts as unset.
- **11 Testing tools.** Recorded responses are replayed by an httpx transport (`FixtureTransport`) and written by `RecordingTransport`, instead of `respx`. Parser tests use explicit assertions instead of syrupy snapshots.

### 16.2 Literature sources

- **6.2 `search_literature` sources.** `sources` defaults to both `pubmed` and `europepmc`.
  - **Merging.** Results are interleaved one at a time from each source. An item sharing a PMID, PMCID, DOI or preprint ID with an item already chosen is skipped.
  - **Paging.** The cursor records each source's position: an offset for PubMed, and a cursor mark plus items already consumed for Europe PMC.
  - **Counts.** `totals` gives the match count per source. `total` is the largest of them, because the sources overlap.
- **6.2 Partial failure.** If one source fails, the other's hits are returned and `source_status` marks the failed source `unavailable`. Only when every requested source fails is the result an `upstream_unavailable` error.
- **8 Who is the source of record.**
  - **`pmid:` records always come from PubMed,** including hits that Europe PMC found. If PubMed cannot be reached, such hits are still listed, with `title_handle: null`, because a `pmid:` passage is only citable with PubMed's text. Europe PMC's open-access status is copied onto these hits, since PubMed does not report it.
  - **`pmcid:` and `ppr:` records come from Europe PMC search metadata,** which supplies the title and abstract sections (split on `<h4>` and `<title>` headings).
  - **Full text** for open-access articles comes from the JATS `fullTextXML` endpoint, as `body.<section>[.<subsection>].pN` passages. A section's slug is its `sec-type`, or else up to three words of its title with numbering removed. Sibling sections with the same slug get `_2`, `_3` and so on.
- **6.2 `fetch_passages`.**
  - **`full_text=true` on a `pmid:` record** adds its open-access PMC version as a second record (`pmcid:…`) right after it. It also copies Europe PMC's open-access status onto the `pmid:` record.
  - **The `unavailable` field** lists requested records whose source did not answer.
  - **`source_status`** covers `pubmed`, `europepmc`, `pubtator` and `crossref`. An enrichment failure (entities or retraction status) is reported as `degraded`, and the affected fields stay `not_checked`.
- **8 PubTator3.** Entities are attached only to PubMed title and abstract passages. Gene IDs become `NCBIGene:…` and species IDs `NCBITaxon:…`. Annotations without an identifier are skipped.
- **8 Crossref.** A record is retracted when any Crossref notice updates its DOI with type `retraction`; the newest notice gives `notice_doi`. The status is merged with PubMed's, and `sources` lists both. Audits merge Crossref's status too, within the resolution deadline.
- **11 Recording fixtures.** `medcite record-fixtures` refuses to save a run in which any source reported `degraded` or `unavailable`, so a transient upstream error is never recorded as a fixture. Re-run it.
- **15 Later stages.** The work is now delivered in five stages:
  1. Core server (done)
  2. Literature sources (this amendment)
  3. ClinicalTrials.gov and openFDA, with `search_trials` and `search_drug_labels`
  4. The audit view, as an MCP App and on GitHub Pages
  5. `CONTRACT.md`, the README, the licence and the manual acceptance checks
