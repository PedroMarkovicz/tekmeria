import { useEffect, useId, useRef, useState } from "react";
import { FLAGS, VERDICTS, findQuote, type CitationResult, type Passage } from "./report";

// Hover opens details only after a short pause, so sweeping the mouse across the answer
// does not open and close blocks under the cursor; leaving waits a little too.
const HOVER_OPEN_MS = 200;
const HOVER_CLOSE_MS = 150;

/** The handle without its check code, or the raw text for a malformed one. */
function shortHandle(handle: string): string {
  const cut = handle.lastIndexOf("~");
  const short = cut > 0 ? handle.slice(0, cut) : handle;
  return short.length > 60 ? `${short.slice(0, 57)}...` : short;
}

/** Why a quote the audit accepted is not marked in the passage shown. */
function unmarkedReason(citation: CitationResult): string {
  return citation.checked_against === "issued"
    ? "The quote matched the text as first issued; the current text below has changed."
    : "The quote could not be located in the text below.";
}

/** The passage text, with the quote (or, for a mismatch, the closest match) marked. */
function PassageText({ citation, passage }: { citation: CitationResult; passage: Passage }) {
  const mismatch = citation.verdict === "quote_mismatch";
  const target = mismatch ? citation.closest_match : citation.quote;
  const span = target ? findQuote(passage.text, target) : null;
  const where = [passage.section_label, passage.record_id].filter(Boolean).join(" \u00b7 ");
  return (
    <figure className="passage">
      <figcaption>
        {where}
        {citation.quote && citation.checked_against
          ? ` (quote checked against the ${citation.checked_against} text)`
          : ""}
      </figcaption>
      {mismatch && citation.closest_match ? (
        <p className="closest">
          Closest match in the passage
          {citation.similarity != null ? ` (similarity ${citation.similarity.toFixed(2)})` : ""}:
        </p>
      ) : null}
      {target && !span && !mismatch ? <p className="unmarked">{unmarkedReason(citation)}</p> : null}
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

/** One citation: a chip with its verdict (icon and text). Hovering opens its details; a click,
 * or focus and Enter, pins them open and the next click closes them; Escape closes them. */
export function CitationChip({ citation }: { citation: CitationResult }) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const detail = useRef<HTMLDivElement>(null);
  const open = hovered || pinned;
  const detailId = useId();
  const verdict = VERDICTS[citation.verdict];
  const flags = citation.flags ?? [];

  const schedule = (value: boolean, ms: number) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setHovered(value), ms);
  };
  const close = () => {
    clearTimeout(timer.current);
    setPinned(false);
    setHovered(false);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    // A long passage scrolls inside its box; bring the mark into view when it opens.
    if (open) detail.current?.querySelector("mark")?.scrollIntoView?.({ block: "nearest" });
  }, [open]);

  return (
    <span
      className="citation"
      onMouseEnter={() => schedule(true, HOVER_OPEN_MS)}
      onMouseLeave={() => schedule(false, HOVER_CLOSE_MS)}
      onKeyDown={(event) => {
        if (event.key === "Escape") close();
      }}
    >
      <button
        type="button"
        className={`chip tone-${verdict.tone}`}
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => {
          if (pinned) {
            close();
          } else {
            clearTimeout(timer.current);
            setHovered(false);
            setPinned(true);
          }
        }}
      >
        <span className="chip-icon" aria-hidden="true">
          {verdict.icon}
        </span>
        <span className="chip-label">{verdict.label}</span>
        <code className="chip-handle">{shortHandle(citation.handle)}</code>
        {flags.map((flag) => (
          <span key={flag.name} className={`badge tone-${FLAGS[flag.name].tone}`}>
            <span aria-hidden="true">{FLAGS[flag.name].icon}</span>{" "}
            <span className="badge-label">{FLAGS[flag.name].label}</span>
          </span>
        ))}
      </button>
      <div
        id={detailId}
        ref={detail}
        className="detail"
        role="region"
        aria-label={`${verdict.label}: ${shortHandle(citation.handle)}`}
        hidden={!open}
      >
        <p className="detail-verdict">
          <strong>{verdict.label}.</strong> {verdict.explain}
        </p>
        <p className="detail-handle">
          <code>{citation.handle}</code>
        </p>
        {flags.map((flag) => (
          <p key={flag.name} className={`detail-flag tone-${FLAGS[flag.name].tone}`}>
            <span aria-hidden="true">{FLAGS[flag.name].icon}</span> {FLAGS[flag.name].label}
            {flag.detail ? `: ${flag.detail}` : ""}
          </p>
        ))}
        {citation.quote ? <blockquote className="detail-quote">{citation.quote}</blockquote> : null}
        {citation.passage ? <PassageText citation={citation} passage={citation.passage} /> : null}
      </div>
    </span>
  );
}
