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
    expect(Object.values(FLAGS)).toEqual(["Retracted", "Preprint", "Version changed"]);
  });

  it("shows undefined ratios as a dash", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0.666)).toBe("67%");
  });
});
