import { CitationChip } from "./CitationChip";
import {
  OVERALL,
  STATUSES,
  VERDICTS,
  percent,
  type AuditReport,
  type ClaimResult,
  type Verdict,
} from "./report";

const VERDICT_ORDER: Verdict[] = [
  "ok",
  "quote_mismatch",
  "withdrawn",
  "unavailable",
  "forged",
  "malformed",
];

function Summary({ report }: { report: AuditReport }) {
  const s = report.summary;
  const overall = OVERALL[report.verdict];
  const numbers: [string, string][] = [
    ["Citations", String(s.citations)],
    ["Fabricated", String(s.fabricated)],
    ["Retracted cited", String(s.retracted_cited)],
    ["Citation validity", percent(s.citation_validity)],
    ["Sentence coverage", `${percent(s.sentence_coverage)} (${s.cited_sentences}/${s.sentences})`],
    ["Quote accuracy", `${percent(s.quote_accuracy)} (${s.quotes_ok}/${s.quotes_checked})`],
  ];
  return (
    <header className="summary">
      <h1 className={`verdict tone-${overall.tone}`}>
        <span aria-hidden="true">{overall.icon}</span> Audit verdict: <strong>{overall.label}</strong>
      </h1>
      <dl className="numbers">
        {numbers.map(([term, value]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <ul className="by-verdict" aria-label="Citations by verdict">
        {VERDICT_ORDER.filter((v) => s.by_verdict[v]).map((v) => (
          <li key={v} className={`tone-${VERDICTS[v].tone}`}>
            <span aria-hidden="true">{VERDICTS[v].icon}</span> {VERDICTS[v].label}: {s.by_verdict[v]}
          </li>
        ))}
      </ul>
      <p className="fail-on">Fails on: {report.fail_on.join(", ") || "nothing"}</p>
    </header>
  );
}

function Claim({ claim }: { claim: ClaimResult }) {
  const status = STATUSES[claim.status];
  return (
    <li className={`claim claim-${claim.status}`}>
      <span className={`status tone-${status.tone}`}>
        <span aria-hidden="true">{status.icon}</span> {status.label}
      </span>{" "}
      <span className="sentence">{claim.sentence}</span>{" "}
      {claim.citations.map((citation, i) => (
        <CitationChip key={`${i}-${citation.handle}`} citation={citation} />
      ))}
    </li>
  );
}

/** A whole report: summary on top, then the answer sentence by sentence. */
export function AuditView({ report }: { report: AuditReport }) {
  return (
    <main className="audit">
      <Summary report={report} />
      <h2>Answer, sentence by sentence</h2>
      {report.claims.length ? (
        <ol className="claims">
          {report.claims.map((claim) => (
            <Claim key={claim.index} claim={claim} />
          ))}
        </ol>
      ) : (
        <p className="notice">No sentences were audited.</p>
      )}
      <footer className="meta">
        Contract {report.contract_version} {"·"} audited {report.audited_at}
      </footer>
    </main>
  );
}
