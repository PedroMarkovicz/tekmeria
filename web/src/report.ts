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
    icon: "\u2713",
    tone: "good",
    explain: "Genuine handle; the record and passage exist, and any quote is verbatim.",
  },
  quote_mismatch: {
    label: "Quote mismatch",
    icon: "\u2260",
    tone: "bad",
    explain: "The quote is not verbatim in the cited passage.",
  },
  withdrawn: {
    label: "Withdrawn",
    icon: "\u2298",
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
    icon: "\u2717",
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
  pass: { label: "Pass", icon: "\u2713", tone: "good" },
  warn: { label: "Warn", icon: "!", tone: "warn" },
  fail: { label: "Fail", icon: "\u2717", tone: "bad" },
  incomplete: { label: "Incomplete", icon: "?", tone: "warn" },
};

export const STATUSES: Record<ClaimStatus, Label> = {
  cited: { label: "Cited", icon: "\u2713", tone: "good" },
  uncited: { label: "Uncited", icon: "\u2205", tone: "warn" },
  invalid_only: { label: "No valid citation", icon: "\u2717", tone: "bad" },
};

// Flags never change a verdict, but a retracted source must not read as a plain "OK".
export const FLAGS: Record<Flag["name"], Label> = {
  retracted: { label: "Retracted", icon: "\u26a0", tone: "bad" },
  preprint: { label: "Preprint", icon: "\u25c7", tone: "warn" },
  version_changed: { label: "Version changed", icon: "\u21bb", tone: "warn" },
};

// The same typographic folds the server applies before comparing a quote (spec 4.5).
const FOLDS: Record<string, string> = {
  "\u2018": "'",
  "\u2019": "'",
  "\u201a": "'",
  "\u201b": "'",
  "\u201c": '"',
  "\u201d": '"',
  "\u201e": '"',
  "\u201f": '"',
  "\u2010": "-",
  "\u2011": "-",
  "\u2013": "-",
  "\u2014": "-",
  "\u2212": "-",
  "\u00a0": " ",
  "\u2026": "...",
};

// Exactly the characters Python's re module counts as whitespace (\s), which is what the
// server collapses. JavaScript's \s differs: it adds U+FEFF and lacks U+001C-U+001F, U+0085.
const SPACE = /^[\t\n\v\f\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$/;
// A base character with its combining marks (or stray marks): the unit normalized to NFC.
const CLUSTER = /\P{M}\p{M}*|\p{M}+/gu;

function fold(text: string): { folded: string; start: number[]; end: number[] } {
  // folded[i] (a UTF-16 code unit) comes from text.slice(start[i], end[i]). Each cluster is
  // normalized to NFC on its own, so this holds even for text not stored in NFC.
  let folded = "";
  const start: number[] = [];
  const end: number[] = [];
  let lastWasSpace = true; // trims leading whitespace
  for (const match of text.matchAll(CLUSTER)) {
    const from = match.index;
    const to = from + match[0].length;
    for (const char of match[0].normalize("NFC")) {
      const out = SPACE.test(char) ? " " : (FOLDS[char] ?? char);
      for (const piece of out) {
        if (piece === " ") {
          if (lastWasSpace) continue;
          lastWasSpace = true;
        } else {
          lastWasSpace = false;
        }
        folded += piece;
        for (let unit = 0; unit < piece.length; unit++) {
          start.push(from);
          end.push(to);
        }
      }
    }
  }
  if (folded.endsWith(" ")) {
    folded = folded.slice(0, -1);
    start.pop();
    end.pop();
  }
  return { folded, start, end };
}

/** Where `quote` sits in `text` as [start, end) indices into `text`, matching the way the
 * audit compares them (NFC, whitespace collapsed, typographic variants folded), or null. */
export function findQuote(text: string, quote: string): [number, number] | null {
  const needle = fold(quote).folded;
  if (!needle) return null;
  const { folded, start, end } = fold(text);
  const at = folded.indexOf(needle);
  if (at < 0) return null;
  return [start[at], end[at + needle.length - 1]];
}

/** A ratio as a whole percentage, or an em dash when it is undefined (null). 100% and 0%
 * appear only when exact: 199 of 200 is 99%, not a rounded-up 100%. */
export function percent(value: number | null): string {
  if (value === null) return "\u2014";
  const rounded = Math.round(value * 100);
  if (rounded === 100 && value < 1) return "99%";
  if (rounded === 0 && value > 0) return "1%";
  return `${rounded}%`;
}
