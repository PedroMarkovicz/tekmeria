# Changelog

## 0.1.1

A maintenance release. The audit, the handles and the contract (still 0.1.0) are unchanged.

- **MCP Registry.** The server is listed as `io.github.PedroMarkovicz/tekmeria`; each release updates the entry (`server.json`).
- **Install from PyPI.** The README and the package page install the released package with `uvx tekmeria`.
- **Releases.** A release now requires CI to have passed on the tagged commit, and checks the registry entry before anything is uploaded.
- **Licence metadata.** The package declares `Apache-2.0 AND CC-BY-4.0`: the code is Apache-2.0, and the wheel ships two CC BY 4.0 articles among its recorded responses (see NOTICE).
- **Recorded responses.** The recorder recognises a PubMed fetch by its host, path and `db` parameter, not by substrings of its URL. The recorded files are unchanged.
- **Tests.** The preprint flag is tested for a record whose kind and peer-review status disagree.
- **Contributing.** `CONTRIBUTING.md` lists the checks to run and what a change needs.

## 0.1.0

The first release.

- **Tools.** Seven MCP tools: `search_literature` (PubMed and Europe PMC), `search_trials` (ClinicalTrials.gov), `search_drug_labels` (openFDA), `fetch_passages`, `resolve_handles`, `audit_answer` and `describe_contract`.
- **Handles.** Every passage carries a handle, `scheme:id[@version]/path~check`, whose check code is the first 25 bits of HMAC-SHA256 over the locator, under a key created once per installation.
- **Audit.** Each citation gets one verdict (`malformed`, `forged`, `unavailable`, `withdrawn`, `quote_mismatch` or `ok`) and flags (`retracted`, `preprint`, `version_changed`); each sentence a status; the answer metrics and an overall verdict. An answer passes only when every sentence is cited and every citation is valid.
- **Audit view.** Hosts that support MCP Apps render the report in an interactive view.
- **Offline demo.** `tekmeria demo` and `tekmeria --demo` run on recorded responses, with a public demo key.
- **Evaluation.** `tekmeria evaluate` plants citations in 34 categories: 1,600 audits and 257 whole answers, all correct. A fault harness and a mutation harness measure what the evaluation would miss.
- **Contract.** The citation contract, version 0.1.0, is in [docs/CONTRACT.md](docs/CONTRACT.md).

Known limits are listed in the README.
