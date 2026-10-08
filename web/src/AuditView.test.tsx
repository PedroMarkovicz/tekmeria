import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuditView } from "./AuditView";
import type { AuditReport } from "./report";
import demo from "./test/fixtures/demo-report.json";
import edge from "./test/fixtures/edge-report.json";

const DEMO = demo as AuditReport;
const EDGE = edge as AuditReport;

function chips(): HTMLElement[] {
  return screen.getAllByRole("button");
}

function chipFor(label: string): HTMLElement {
  const chip = chips().find((button) => within(button).queryByText(label, { exact: true }));
  if (!chip) throw new Error(`no chip labelled ${label}`);
  return chip;
}

function detailOf(chip: HTMLElement): HTMLElement {
  return document.getElementById(chip.getAttribute("aria-controls")!)!;
}

describe("summary", () => {
  it("shows the overall verdict and the metrics", () => {
    render(<AuditView report={DEMO} />);
    expect(screen.getByText(/Audit verdict:/)).toHaveTextContent("Audit verdict: Fail");
    expect(screen.getByText("Fabricated").nextElementSibling).toHaveTextContent("1");
    expect(screen.getByText("Retracted cited").nextElementSibling).toHaveTextContent("1");
    expect(screen.getByText("Sentence coverage").nextElementSibling).toHaveTextContent("33% (2/6)");
    expect(screen.getByLabelText("Citations by verdict")).toHaveTextContent("Forged: 1");
  });

  it("puts the verdict in the page's only top-level heading", () => {
    render(<AuditView report={DEMO} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Audit verdict: Fail");
  });

  it("says so when there is nothing to fail on, or no sentence", () => {
    render(<AuditView report={{ ...EDGE, claims: [], fail_on: [] }} />);
    expect(screen.getByText(/Fails on:/)).toHaveTextContent("Fails on: nothing");
    expect(screen.getByText("No sentences were audited.")).toBeInTheDocument();
  });

  it("shows an incomplete verdict and undefined ratios", () => {
    render(<AuditView report={EDGE} />);
    expect(screen.getByText(/Audit verdict:/)).toHaveTextContent("Incomplete");
    expect(screen.getByText("Citation validity").nextElementSibling).toHaveTextContent("67%");
  });
});

describe("citation chips", () => {
  it.each([
    [DEMO, "OK"],
    [DEMO, "Quote mismatch"],
    [DEMO, "Forged"],
    [DEMO, "Malformed"],
    [EDGE, "Unavailable"],
    [EDGE, "Withdrawn"],
  ])("show the %# verdict %s with an icon and a text label", (report, label) => {
    render(<AuditView report={report} />);
    const chip = chipFor(label);
    expect(chip.querySelector(".chip-icon")).toHaveAttribute("aria-hidden", "true");
    expect(chip.querySelector(".chip-icon")?.textContent).not.toBe("");
  });

  it.each([
    [DEMO, "Retracted"],
    [EDGE, "Preprint"],
    [EDGE, "Version changed"],
  ])("show the %# flag %s as a badge with an icon", (report, flag) => {
    render(<AuditView report={report} />);
    const labels = screen.getAllByText(flag, { selector: ".badge-label" });
    expect(labels.length).toBeGreaterThan(0);
    const icon = labels[0].parentElement!.querySelector('[aria-hidden="true"]');
    expect(icon?.textContent).not.toBe("");
  });

  it("are all reachable with the keyboard", async () => {
    const user = userEvent.setup();
    render(<AuditView report={DEMO} />);
    const reached: Element[] = [];
    for (let i = 0; i < chips().length; i++) {
      await user.tab();
      reached.push(document.activeElement!);
    }
    expect(reached).toEqual(chips());
  });

  it("open on hover and show the quote marked in the passage", async () => {
    const user = userEvent.setup();
    render(<AuditView report={DEMO} />);
    const chip = chipFor("OK");
    expect(detailOf(chip)).not.toBeVisible();
    await user.hover(chip);
    await waitFor(() => expect(chip).toHaveAttribute("aria-expanded", "true"));
    const quote = DEMO.claims[0].citations[0].quote!;
    expect(detailOf(chip).querySelector("mark.mark-quote")).toHaveTextContent(quote);
    await user.unhover(chip);
    await waitFor(() => expect(detailOf(chip)).not.toBeVisible());
  });

  it("stay open after a click on a hover-opened chip, and close on the next click", async () => {
    const user = userEvent.setup();
    render(<AuditView report={DEMO} />);
    const chip = chipFor("OK");
    await user.hover(chip);
    await waitFor(() => expect(chip).toHaveAttribute("aria-expanded", "true"));
    await user.click(chip);
    await user.unhover(chip);
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(chip).toHaveAttribute("aria-expanded", "true");
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(detailOf(chip)).not.toBeVisible();
  });

  it("scroll the marked quote into view when opened", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const user = userEvent.setup();
    render(<AuditView report={DEMO} />);
    const chip = chipFor("OK");
    await user.click(chip);
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: "nearest" }));
    expect(scroll.mock.contexts[0]).toBe(detailOf(chip).querySelector("mark"));
  });

  it("say so when a verified quote is not in the current text", async () => {
    const user = userEvent.setup();
    render(<AuditView report={EDGE} />);
    const chip = chipFor("Version changed").closest("button")!;
    await user.click(chip);
    expect(detailOf(chip)).toHaveTextContent(
      "The quote matched the text as first issued; the current text below has changed.",
    );
    expect(detailOf(chip).querySelector("mark")).toBeNull();
  });

  it("open with focus and Enter, and close with Escape", async () => {
    const user = userEvent.setup();
    render(<AuditView report={DEMO} />);
    const chip = chipFor("Forged");
    chip.focus();
    await user.keyboard("{Enter}");
    expect(detailOf(chip)).toBeVisible();
    expect(detailOf(chip)).toHaveTextContent("this server never issued the handle");
    await user.keyboard("{Escape}");
    expect(detailOf(chip)).not.toBeVisible();
  });

  it("show the closest match for a misquote, not the quote", async () => {
    const user = userEvent.setup();
    render(<AuditView report={DEMO} />);
    const chip = chipFor("Quote mismatch");
    await user.click(chip);
    const detail = detailOf(chip);
    expect(detail).toHaveTextContent("Closest match in the passage");
    const closest = DEMO.claims[1].citations[0].closest_match!;
    expect(detail.querySelector("mark.mark-closest")).toHaveTextContent(closest);
    expect(detail.querySelector("mark.mark-quote")).toBeNull();
  });

  it("explain a version change with the current version", async () => {
    const user = userEvent.setup();
    render(<AuditView report={EDGE} />);
    const chip = chipFor("Version changed").closest("button")!;
    await user.click(chip);
    expect(detailOf(chip)).toHaveTextContent("Version changed: 2026-03-25");
  });
});

describe("claims", () => {
  it.each([
    ["cited", "Cited"],
    ["uncited", "Uncited"],
    ["invalid_only", "No valid citation"],
  ])("mark a %s sentence visibly", (status, label) => {
    render(<AuditView report={DEMO} />);
    const items = document.querySelectorAll(`li.claim-${status}`);
    expect(items.length).toBeGreaterThan(0);
    expect(items[0].querySelector(".status")).toHaveTextContent(label);
  });

  it("list every sentence in order", () => {
    render(<AuditView report={DEMO} />);
    const sentences = [...document.querySelectorAll(".sentence")].map((s) => s.textContent);
    expect(sentences).toEqual(DEMO.claims.map((claim) => claim.sentence));
  });
});
