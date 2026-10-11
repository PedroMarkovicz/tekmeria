# Changelog

## 0.1.0

The first release.

- **Tools.** Seven MCP tools: `search_literature` (PubMed and Europe PMC), `search_trials` (ClinicalTrials.gov), `search_drug_labels` (openFDA), `fetch_passages`, `resolve_handles`, `audit_answer` and `describe_contract`.
- **Handles.** Every passage carries a handle, `scheme:id[@version]/path~check`, whose check code is the first 25 bits of HMAC-SHA256 over the locator, under a key created once per installation.
- **Audit.** Each citation gets one verdict (`malformed`, `forged`, `unavailable`, `withdrawn`, `quote_mismatch` or `ok`) and flags (`retracted`, `preprint`, `version_changed`); each sentence a status; the answer metrics and an overall verdict. An answer passes only when every sentence is cited and every citation is valid.
- **Audit view.** Hosts that support MCP Apps render the report in an interactive view.
- **Offline demo.** `tekmerion demo` and `tekmerion --demo` run on recorded responses, with a public demo key.
- **Evaluation.** `tekmerion evaluate` plants citations in 34 categories: 1,600 audits and 257 whole answers, all correct. A fault harness and a mutation harness measure what the evaluation would miss.
- **Contract.** The citation contract, version 0.1.0, is in [docs/CONTRACT.md](docs/CONTRACT.md).

Known limits are listed in the README.
