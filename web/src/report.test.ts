import { describe, expect, it } from "vitest";
import { FLAGS, OVERALL, STATUSES, VERDICTS, findQuote, percent } from "./report";

describe("findQuote", () => {
  const text = "MMR vaccination does “not” increase the risk — hazard ratio 0.93.";

  it("finds an exact quote", () => {
    const quote = "increase the risk";
    const [start, end] = findQuote(text, quote)!;
    expect(text.slice(start, end)).toBe(quote);
  });

  it("matches straight quotes and hyphens against typographic ones", () => {
    const [start, end] = findQuote(text, 'does "not" increase the risk - hazard')!;
    expect(text.slice(start, end)).toBe("does “not” increase the risk — hazard");
  });

  it("collapses whitespace in the quote", () => {
    const [start, end] = findQuote(text, "  hazard\n ratio   0.93 ")!;
    expect(text.slice(start, end)).toBe("hazard ratio 0.93");
  });

  it("expands an ellipsis to three dots", () => {
    const [start, end] = findQuote("It rose… then fell.", "rose... then")!;
    expect("It rose… then fell.".slice(start, end)).toBe("rose… then");
  });

  it("finds a quote in text that is not stored in NFC", () => {
    const decomposed = "cafe\u0301 au lait";
    const [start, end] = findQuote(decomposed, "caf\u00e9 au")!;
    expect(decomposed.slice(start, end)).toBe("cafe\u0301 au");
  });

  it("treats exactly the characters Python treats as whitespace as spaces", () => {
    const [start, end] = findQuote("risk\u0085ratio", "risk ratio")!;
    expect("risk\u0085ratio".slice(start, end)).toBe("risk\u0085ratio");
    expect(findQuote("risk\ufeffratio", "risk ratio")).toBeNull();
  });

  it("maps characters outside the basic plane back correctly", () => {
    const text = "\u{1F489} risk ratio";
    const [start, end] = findQuote(text, "risk ratio")!;
    expect(text.slice(start, end)).toBe("risk ratio");
  });

  it("returns null for a quote that is not there, or empty", () => {
    expect(findQuote(text, "strongly increases")).toBeNull();
    expect(findQuote(text, "   ")).toBeNull();
  });
});

describe("labels", () => {
  it("give every verdict, status and flag an icon and a text label", () => {
    for (const entry of [...Object.values(VERDICTS), ...Object.values(OVERALL), ...Object.values(STATUSES)]) {
      expect(entry.icon).not.toBe("");
      expect(entry.label).not.toBe("");
    }
    expect(Object.keys(VERDICTS).sort()).toEqual(
      ["forged", "malformed", "ok", "quote_mismatch", "unavailable", "withdrawn"],
    );
    expect(Object.values(FLAGS).map((f) => f.label)).toEqual([
      "Retracted",
      "Preprint",
      "Version changed",
    ]);
    for (const flag of Object.values(FLAGS)) expect(flag.icon).not.toBe("");
    expect(FLAGS.retracted.tone).toBe("bad");
  });

  it("shows undefined ratios as a dash", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0.666)).toBe("67%");
  });

  it("shows 100% and 0% only when they are exact", () => {
    expect(percent(199 / 200)).toBe("99%");
    expect(percent(1 / 300)).toBe("1%");
    expect(percent(1)).toBe("100%");
    expect(percent(0)).toBe("0%");
  });
});
