# Security policy

## Supported versions

Fixes go into the latest release only.

## Reporting a vulnerability

Please report it privately, not in a public issue: open a
[private vulnerability report](https://github.com/PedroMarkovicz/tekmeria/security/advisories/new)
(the repository's Security tab, "Report a vulnerability").

Include the version or commit, what you did, what happened, and what you expected. A small
script or an answer text that reproduces the problem helps most.

Tekmeria is research software with one maintainer, so reports are answered on a best-effort
basis. You will be credited in the fix's release notes unless you ask not to be.

## What counts

- A handle the audit accepts as issued by an installation that never issued it, found by any
  means cheaper than guessing its check code.
- A way to read or change an installation's handle key, cache or files through the tools.
- A tool input that makes the server send requests to a host other than the sources it lists.

## What is by design

- **The check code has 25 bits.** A guessed code passes about once in 33.5 million tries. That
  is sized to catch fabricated citations, not to stop a determined attacker
  ([contract, section 1.2](https://github.com/PedroMarkovicz/tekmeria/blob/main/docs/CONTRACT.md)).
- **The demo key is public.** `tekmeria --demo` and `tekmeria demo` use a key published in
  this repository, so their handles prove nothing outside the demo.
- **The audit checks provenance and quotation only.** A genuine handle can stand under a claim
  that its passage does not support.
