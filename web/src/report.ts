// What the view knows about an audit report: labels, icons, and where a quote sits in its
// passage. Types come from the Python models through schemas/audit_report.schema.json.
import type {
  AuditReport,
  CitationResult,
  ClaimResult,
  Flag,
  Passage,
  Verdict,
} from "./generated/audit-report";

export type { AuditReport, CitationResult, ClaimResult, Flag, Passage, Verdict };
export type ClaimStatus = ClaimResult["status"];
export type OverallVerdict = AuditReport["verdict"];
export type Tone = "good" | "warn" | "bad";

interface Label {
  label: string;
  icon: string;
  tone: Tone;
}

// Every verdict has an icon and a text label: colour is never the only signal.
export const VERDICTS: Record<Verdict, Label & { explain: string }> = {
  ok: {
    label: "OK",
    icon: "✓",
    tone: "good",
    explain: "Genuine handle; the record and passage exist, and any quote is verbatim.",
  },
  quote_mismatch: {
    label: "Quote mismatch",
    icon: "≠",
    tone: "bad",
    explain: "The quote is not verbatim in the cited passage.",
  },
  withdrawn: {
    label: "Withdrawn",
    icon: "⊘",
    tone: "bad",
    explain: "The record, or this passage of it, no longer exists upstream.",
  },
  unavailable: {
    label: "Unavailable",
    icon: "?",
    tone: "warn",
    explain: "A genuine handle whose source could not be reached. Not fabrication; retry later.",
  },
  forged: {
    label: "Forged",
    icon: "✗",
    tone: "bad",
    explain: "The check code is wrong: this server never issued the handle.",
  },
  malformed: {
    label: "Malformed",
    icon: "!",
    tone: "bad",
    explain: "The text does not match the handle grammar.",
  },
};

export const OVERALL: Record<OverallVerdict, Label> = {
  pass: { label: "Pass", icon: "✓", tone: "good" },
  warn: { label: "Warn", icon: "!", tone: "warn" },
  fail: { label: "Fail", icon: "✗", tone: "bad" },
  incomplete: { label: "Incomplete", icon: "?", tone: "warn" },
};

export const STATUSES: Record<ClaimStatus, Label> = {
  cited: { label: "Cited", icon: "✓", tone: "good" },
  uncited: { label: "Uncited", icon: "∅", tone: "warn" },
  invalid_only: { label: "No valid citation", icon: "✗", tone: "bad" },
};

export const FLAGS: Record<Flag["name"], string> = {
  retracted: "Retracted",
  preprint: "Preprint",
  version_changed: "Version changed",
};

// The same typographic folds the server applies before comparing a quote (spec 4.5).
const FOLDS: Record<string, string> = {
  "‘": "'",
  "’": "'",
  "‚": "'",
  "‛": "'",
  "“": '"',
  "”": '"',
  "„": '"',
  "‟": '"',
  "‐": "-",
  "‑": "-",
  "–": "-",
  "—": "-",
  "−": "-",
  " ": " ",
  "…": "...",
};

function fold(text: string): { folded: string; origin: number[] } {
  // `origin[i]` is the index in `text` of the character that produced folded[i].
  let folded = "";
  const origin: number[] = [];
  let lastWasSpace = true; // trims leading whitespace
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const out = /\s/.test(char) ? " " : (FOLDS[char] ?? char);
    for (const piece of out) {
      if (piece === " ") {
        if (lastWasSpace) continue;
        lastWasSpace = true;
      } else {
        lastWasSpace = false;
      }
      folded += piece;
      origin.push(i);
    }
  }
  if (folded.endsWith(" ")) {
    folded = folded.slice(0, -1);
    origin.pop();
  }
  return { folded, origin };
}

/** Where `quote` sits in `text` as [start, end) indices into `text`, matching the way the
 * audit compares them (whitespace collapsed, typographic variants folded), or null. */
export function findQuote(text: string, quote: string): [number, number] | null {
  const needle = fold(quote.normalize("NFC")).folded;
  if (!needle) return null;
  const { folded, origin } = fold(text.normalize("NFC"));
  const at = folded.indexOf(needle);
  if (at < 0) return null;
  return [origin[at], origin[at + needle.length - 1] + 1];
}

/** A ratio as a whole percentage, or an em dash when it is undefined (null). */
export function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}
