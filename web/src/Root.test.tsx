import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Root } from "./Root";
import type { AuditReport } from "./report";
import type { ViewState } from "./source";
import demo from "./test/fixtures/demo-report.json";

function startWith(state: ViewState) {
  return (update: (state: ViewState) => void) => update(state);
}

describe("Root", () => {
  it("renders a report", () => {
    render(<Root start={startWith({ kind: "report", report: demo as AuditReport })} />);
    expect(screen.getByText(/Audit verdict:/)).toHaveTextContent("Fail");
  });

  it("shows an error as an alert", () => {
    render(<Root start={startWith({ kind: "error", message: "error invalid_input: nope" })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("error invalid_input: nope");
  });

  it("explains an empty page and a wait", () => {
    const { unmount } = render(<Root start={startWith({ kind: "empty" })} />);
    expect(screen.getByText(/No audit report here/)).toBeInTheDocument();
    unmount();
    render(<Root start={startWith({ kind: "waiting" })} />);
    expect(screen.getByText(/Waiting for the audit result/)).toBeInTheDocument();
  });
});
