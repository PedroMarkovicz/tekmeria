# Low-level design

Three diagrams of how tekmeria works. The [README](../README.md) has the architecture and the order of the audit's checks, and the [design document](specs/2026-10-06-tekmeria-server-design.md) records the decisions and their reasons.

## From a question to an audited answer

```mermaid
sequenceDiagram
    participant M as Model
    participant S as tekmeria
    participant E as Evidence service
    participant A as Upstream APIs

    M->>S: search_literature, search_trials, search_drug_labels
    S->>E: query, with filters and a time budget
    E->>A: rate-limited requests
    A-->>E: records
    E-->>S: records, merged
    S-->>M: hits
    M->>S: fetch_passages
    S->>E: fetch the records
    E->>E: cut records into passages, mint a handle for each,<br/>keep the first text issued in the cache
    E-->>S: passages with handles
    S-->>M: passages with handles
    M->>M: writes the answer, citing [handle "exact words"]
    M->>S: audit_answer
    S->>S: split sentences, read every citation group,<br/>verify each check code
    S->>E: resolve the genuine handles to their passages
    S->>S: verdicts, flags, statuses, metrics, overall verdict
    S-->>M: the report, and the audit view in the host
```

## The evaluation

```mermaid
flowchart TD
    SN["Recorded responses<br/>a snapshot of 7 records, 213 passages"] --> OR["Reference oracle<br/>issues and forges handles, checks quotes"]
    OR --> CA["Planted cases<br/>34 categories"]
    CA --> AU["Each case audited<br/>as structured claims and as answer text"]
    CA --> WA["Whole answers audited<br/>mixed, clean, paired"]
    AU --> SC["Scored against the plan<br/>verdicts, flags, statuses, metrics, overall verdict"]
    WA --> SC
    FA["Injected faults<br/>the audit broken in 38 ways, one at a time"] -. "rerun" .-> AU
    MU["Mutation analysis<br/>78 mutants of the audit's code"] -. "rerun" .-> AU
```

## From a push to a release

```mermaid
flowchart LR
    P["Push or pull request"] --> PY["python<br/>ruff, pyright, pytest,<br/>demo, evaluation, faults"]
    P --> WE["web<br/>types, tests, build,<br/>committed view unchanged"]
    PY --> PG["pages<br/>after CI passes on main:<br/>the demo's audit view"]
    WE --> PG
    R["GitHub release v*"] --> B["build<br/>tag = version, demo page live,<br/>tests, package"]
    B --> AP["approval<br/>in the pypi environment"]
    AP --> PI["PyPI<br/>trusted publishing, no token"]
    R --> Z["Zenodo<br/>archive and DOI"]
```
