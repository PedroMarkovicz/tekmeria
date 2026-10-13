# The tekmeria citation contract

Contract version: `0.1.0`

This document is the reference for everything a client relies on: the handle grammar, passage paths, how citations are written in an answer, the audit's verdicts and flags, its metrics, and the versioning policy. The server reports the same contract version in `describe_contract` and in every audit report. A test (`tests/contract/test_contract_doc.py`) fails if a verdict, flag, scheme, path, status, metric or `fail_on` value in the code is missing here.

## 1. Handles

Every passage the server returns has a handle. An answer cites handles, and the audit checks them.

```
handle   = locator "~" check
locator  = scheme ":" id [ "@" version ] "/" path
```

The regular expression the server validates with:

```
^(pmid|pmcid|ppr|nct|setid):([A-Za-z0-9-]+)(?:@([A-Za-z0-9.-]+))?/([a-z0-9_]+(?:\.[a-z0-9_]+)*)~([0-9a-hjkmnp-tv-z]{5})$
```

Examples, issued under the public demo key: they resolve with `tekmeria --demo`, and their check codes fail under any other key.

```
pmid:30831578/abstract.7~ae2tc
pmcid:PMC12585062/body.methods.data_sources.p1~7xf52
nct:NCT04363697@2026-06-16/results.outcome.1.analysis.1~cxt60
setid:7c4deb97-9966-4c8e-9ba6-bb260b71e130@6/boxed_warning.1~dkqnm
```

### 1.1 Schemes

| Scheme | Record | Source | Version in the handle |
|---|---|---|---|
| `pmid` | Journal article | PubMed | none |
| `pmcid` | Open-access article in PubMed Central | Europe PMC | none |
| `ppr` | Preprint | Europe PMC | none |
| `nct` | Trial registration | ClinicalTrials.gov | the last-update post date, `YYYY-MM-DD` |
| `setid` | Drug label | openFDA | the SPL label version, an integer |

The registries serve only their current version. A version in a handle records what was cited; it does not promise that the same text can still be fetched. When the current version differs, the citation gets the `version_changed` flag.

### 1.2 Check code

The check code is the first 25 bits of HMAC-SHA256 over the locator, under the installation's handle key, written as five lower-case Crockford base32 characters. It covers the locator only, not the passage text.

- **What it proves:** this server issued this handle. A guessed check code passes about once in 33.5 million tries, which is enough to catch fabrication; it is not designed to stop a determined attacker.
- **What it does not prove:** that the cited text still says the same thing. The content checks in section 4 answer that.
- **Keys are per installation:** a handle issued by one installation does not verify on another. The offline demo uses a fixed public key, accepted only in demo mode.

## 2. Passage paths

Path segments are lower case; indices are 1-based and follow document order.

| Path | Passage |
|---|---|
| `title` | Article title. |
| `abstract.N` | Nth non-empty abstract section (an unlabelled abstract is `abstract.1`). |
| `body.<section>[.<subsection>].pN` | Nth paragraph of an open-access full-text section (`pmcid:` records only). Sections nest as deep as the article does, and paragraphs directly under the body are `body.pN`. |
| `summary.1` | Brief summary of a trial. |
| `eligibility.inclusion.N` | Nth inclusion criterion of a trial. |
| `eligibility.exclusion.N` | Nth exclusion criterion of a trial. |
| `eligibility.other.N` | Nth eligibility statement outside the inclusion and exclusion lists. |
| `outcome.primary.N` | Nth registered primary outcome. |
| `outcome.secondary.N` | Nth registered secondary outcome. |
| `arm.N` | Nth trial arm, with its interventions. |
| `results.outcome.N` | Nth posted outcome measure: what was measured and how many were analysed. Its values are in `results.outcome.N.values.M` (one row per category) and its statistical analyses in `results.outcome.N.analysis.M`. |
| `<section_key>.N` | Nth chunk of a drug label section, keyed by the openFDA field name, for example `boxed_warning.1`. Chunks end at sentence ends and hold about 1,000 characters at most; a single longer sentence stays whole. |

## 3. Text normalization

Passage text is issued normalized, and quotes are normalized the same way before they are compared:

1. Unicode NFC.
2. Every run of whitespace becomes one space, and the ends are trimmed.
3. For comparison only, typographic variants are folded and invisible characters removed:
   - U+2018, U+2019, U+201A and U+201B (single curly and low quotes), U+02BC (modifier letter apostrophe) and U+2032 (prime) become `'`;
   - U+201C, U+201D, U+201E and U+201F (double curly and low quotes) become `"`;
   - U+2010 (hyphen), U+2011 (non-breaking hyphen), U+2012 (figure dash), U+2013 (en dash), U+2014 (em dash), U+2015 (horizontal bar), U+2212 (minus sign) and U+FF0D (fullwidth hyphen-minus) become `-`;
   - U+00A0 (non-breaking space) becomes a space;
   - U+2026 (ellipsis) becomes three dots;
   - U+00AD (soft hyphen), U+200B (zero-width space), U+2060 (word joiner) and U+FEFF (zero-width no-break space) are removed.

## 4. Citing in an answer

`audit_answer` takes either free text (`answer_text`) or structured claims (`claims`).

- **Free text.** Citations go in square brackets after the claim they support: `[handle]`, several as `[handle; handle]` (a comma also works), and a verbatim quote as `[handle "exact words"]`. Single or curly quotes also work, and the quote may follow the handle without a space. Backticks, asterisks and underscores around a handle are ignored, and so is punctuation right after it (`[handle.]`). A bracket group is a citation group only if one of its entries looks like `scheme:...`, so `[1]` stays plain text, and so does a bracketed web address such as `[https://example.org]`. A handle-shaped token written outside any brackets still counts as a citation, so a well-formed handle cannot slip past the audit.
- **Sentences.** The text is split into sentences (pySBD, English, rule-based), and each sentence is one claim. A citation group that opens a sentence belongs to the sentence before it.
- **Markdown.** Headings (`#` lines) and bold runs that open a line (`**Results.**` before the text, or `**Safety**` alone) are sentences of their own, so they never merge with the text after them. Like any sentence, they are uncited unless they carry a citation: nothing is dropped, so no text can hide from the audit. The `#`, `**` and `__` markers are removed. A line break also ends a sentence, which keeps list items apart.
- **Structured claims.** `claims` is a list of `{sentence, citations: [{handle, quote}]}` taken exactly as given.
- **Limits.** An `answer_text` of at most 20,000 characters, or at most 100 claims; at most 10 citations per sentence, counting citations written after its full stop; handles of at most 300 characters; quotes of at most 1,000 characters; sentences of at most 4,000 characters. Larger input is `invalid_input`, except that an over-long handle in free text is cut to 300 characters (it then fails as `malformed` or `forged`). An answer with no sentences is `invalid_input` too: there is nothing to audit.

## 5. Verdicts

Each citation gets exactly one verdict. The checks run in this order, and the first one that fails decides:

| Order | Verdict | Meaning |
|---|---|---|
| 1 | `malformed` | The text does not match the handle grammar. A copy error that breaks the grammar (a dropped character, a letter outside the check-code alphabet) lands here, so it is never counted as fabrication. |
| 2 | `forged` | The grammar matches but the check code is wrong: this server never issued the handle. This is fabrication, as far as the server can tell: a copy error that keeps the grammar, such as one changed check-code character, also lands here. |
| 3 | `unavailable` | A genuine handle whose source could not be reached in time. Retryable, and never counted as fabrication. |
| 4 | `withdrawn` | The record, or the passage path within it, no longer exists upstream. |
| 5 | `quote_mismatch` | A quote was given and is not a substring of the passage after the normalization in section 3. |
| 6 | `ok` | Every check passed. |

A quote counts as verbatim if it matches the passage text as it was first issued or as it is now. The text as first issued is available only while the server's cache holds it. For a verbatim quote, `checked_against` says which text it matched (`issued` or `current`). Without a quote, or on a `quote_mismatch`, it says which text was available to check against: `issued` when the cache holds it, `current` otherwise. For a `quote_mismatch`, the report adds `closest_match`, the most similar window of the current text, and its `similarity` from 0 to 1; both are diagnostics and never change the verdict.

Every citation also carries `support: "not_checked"`. Whether a passage supports the claim is a semantic question outside this contract.

## 6. Flags

Flags describe the cited record. They never change a verdict.

| Flag | Meaning |
|---|---|
| `retracted` | The record has been retracted, according to PubMed, Europe PMC or Crossref; `detail` is the retraction notice's DOI when known. |
| `preprint` | The record is a preprint, so not peer-reviewed. A record's `peer_reviewed` signal means published in a journal rather than as a preprint; it does not promise that a letter or an editorial was formally reviewed. |
| `version_changed` | The record's current version differs from the version in the handle; `detail` is the current version. |

## 7. Claim status

| Status | Meaning |
|---|---|
| `cited` | At least one citation is `ok`. |
| `uncited` | The sentence has no citations. |
| `invalid_only` | The sentence has citations, but none is `ok`. |

## 8. Metrics

| Metric | Definition |
|---|---|
| `citation_validity` | `ok` citations / (citations - `unavailable` citations); `null` when the denominator is 0. |
| `sentence_coverage` | `cited` sentences / sentences; `null` when there are no sentences. |
| `quote_accuracy` | verbatim quotes / quotes on `ok` or `quote_mismatch` citations. Quotes on `malformed`, `forged`, `unavailable` or `withdrawn` citations never reach the quote check and are not counted. `null` when there are none. |
| `fabricated` | The number of `forged` citations. |
| `retracted_cited` | The number of `ok` citations whose record is retracted. |

The summary also counts sentences by status and citations by verdict (`by_verdict`).

## 9. Overall verdict

The report's `verdict` is decided in this order:

1. `fail` if anything listed in `fail_on` occurs.
2. Otherwise `incomplete` if any citation is `unavailable`.
3. Otherwise `warn` if any sentence is uncited, any citation is not `ok` (with no `ok` citation in its sentence, which makes the sentence `invalid_only`, or beside an `ok` one), or any `ok` citation is retracted.
4. Otherwise `pass`: every sentence is cited, and every citation is `ok` and not retracted.

`fail_on` may list any of `malformed`, `forged`, `unavailable`, `withdrawn`, `quote_mismatch`, `uncited` and `retracted`. The default is `forged` and `malformed`, the two signs that a model invented or mangled a citation.

## 10. Versioning

The contract follows semantic versioning, separately from the package version:

- **Breaking changes** bump the major version, or the minor version while the contract is `0.x`. Breaking means:
  - removing or renaming a field;
  - adding, removing or renaming a verdict, flag, claim status, overall verdict or `fail_on` value, because clients (and the audit view) treat these as closed sets;
  - changing the handle grammar, including adding a scheme, or the check-code computation, or an existing passage path;
  - changing a metric's definition.
- **Additive changes** (a new optional field, a new tool, or a new kind of passage path for an existing scheme) bump the minor version, or the patch version while the contract is `0.x`.
- **The same input, handle key, upstream content and cached issued text always give the same report,** apart from `audited_at`.
