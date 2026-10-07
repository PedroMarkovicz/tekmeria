import { useId, useState } from "react";
import { FLAGS, VERDICTS, findQuote, type CitationResult, type Passage } from "./report";

/** The handle without its check code, or the raw text for a malformed one. */
function shortHandle(handle: string): string {
  const cut = handle.lastIndexOf("~");
  const short = cut > 0 ? handle.slice(0, cut) : handle;
  return short.length > 60 ? `${short.slice(0, 57)}...` : short;
}

/** The passage text, with the quote (or, for a mismatch, the closest match) marked. */
function PassageText({ citation, passage }: { citation: CitationResult; passage: Passage }) {
  const mismatch = citation.verdict === "quote_mismatch";
  const target = mismatch ? citation.closest_match : citation.quote;
  const span = target ? findQuote(passage.text, target) : null;
  const where = [passage.section_label, passage.record_id].filter(Boolean).join(" · ");
  return (
    <figure className="passage">
      <figcaption>
        {where}
        {citation.checked_against ? ` (quote checked against the ${citation.checked_against} text)` : ""}
      </figcaption>
      {mismatch && citation.closest_match ? (
        <p className="closest">
          Closest match in the passage
          {citation.similarity != null ? ` (similarity ${citation.similarity.toFixed(2)})` : ""}:
        </p>
      ) : null}
      <p className="passage-text">
        {span ? (
          <>
            {passage.text.slice(0, span[0])}
            <mark className={mismatch ? "mark-closest" : "mark-quote"}>
              {passage.text.slice(span[0], span[1])}
            </mark>
            {passage.text.slice(span[1])}
          </>
        ) : (
          passage.text
        )}
      </p>
    </figure>
  );
}

/** One citation: a chip with its verdict (icon and text), which opens its details on
 * hover, or on focus and Enter; Escape closes them. */
export function CitationChip({ citation }: { citation: CitationResult }) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || pinned;
  const detailId = useId();
  const verdict = VERDICTS[citation.verdict];
  const flags = citation.flags ?? [];
  return (
    <span
      className="citation"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        className={`chip tone-${verdict.tone}`}
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setPinned((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setPinned(false);
            setHovered(false);
          }
        }}
      >
        <span className="chip-icon" aria-hidden="true">
          {verdict.icon}
        </span>
        <span className="chip-label">{verdict.label}</span>
        <code className="chip-handle">{shortHandle(citation.handle)}</code>
        {flags.map((flag) => (
          <span key={flag.name} className={`badge badge-${flag.name}`}>
            {FLAGS[flag.name]}
          </span>
        ))}
      </button>
      <div
        id={detailId}
        className="detail"
        role="region"
        aria-label={`Details for ${shortHandle(citation.handle)}`}
        hidden={!open}
      >
        <p className="detail-verdict">
          <strong>{verdict.label}.</strong> {verdict.explain}
        </p>
        <p className="detail-handle">
          <code>{citation.handle}</code>
        </p>
        {flags.map((flag) => (
          <p key={flag.name} className="detail-flag">
            {FLAGS[flag.name]}
            {flag.detail ? `: ${flag.detail}` : ""}
          </p>
        ))}
        {citation.quote ? <blockquote className="detail-quote">{citation.quote}</blockquote> : null}
        {citation.passage ? <PassageText citation={citation} passage={citation.passage} /> : null}
      </div>
    </span>
  );
}
