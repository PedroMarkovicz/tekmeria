import { describe, expect, it, vi } from "vitest";
import {
  REPORT_ELEMENT_ID,
  embeddedReport,
  startSource,
  stateFromToolResult,
  type HostApp,
  type ToolResult,
  type ViewState,
} from "./source";
import demo from "./test/fixtures/demo-report.json";

function docWith(text: string | null): Document {
  const doc = document.implementation.createHTMLDocument("t");
  if (text !== null) {
    const script = doc.createElement("script");
    script.type = "application/json";
    script.id = REPORT_ELEMENT_ID;
    script.textContent = text;
    doc.body.append(script);
  }
  return doc;
}

class FakeApp implements HostApp {
  handlers: Record<string, (value: never) => void> = {};
  connected = false;
  addEventListener(event: string, handler: (value: never) => void): void {
    this.handlers[event] = handler;
  }
  async connect(): Promise<void> {
    this.connected = true;
  }
  getHostContext() {
    return undefined;
  }
  send(result: ToolResult): void {
    (this.handlers.toolresult as (r: ToolResult) => void)(result);
  }
}

const top = { get parent() { return this; } } as unknown as Window;
const framed = { parent: {} } as unknown as Window;

describe("embeddedReport", () => {
  it("reads the report embedded in the page", () => {
    expect(embeddedReport(docWith(JSON.stringify(demo)))).toEqual({ kind: "report", report: demo });
  });

  it("is null for an empty slot or no slot", () => {
    expect(embeddedReport(docWith(""))).toBeNull();
    expect(embeddedReport(docWith(null))).toBeNull();
  });

  it("reports bad JSON and non-reports as errors", () => {
    expect(embeddedReport(docWith("{nope"))?.kind).toBe("error");
    expect(embeddedReport(docWith('{"claims": 3}'))?.kind).toBe("error");
    expect(embeddedReport(docWith('{"claims": [], "summary": null}'))?.kind).toBe("error");
  });
});

describe("stateFromToolResult", () => {
  it("shows the structured report", () => {
    expect(stateFromToolResult({ structuredContent: demo })).toEqual({ kind: "report", report: demo });
  });

  it("shows a tool error with its text", () => {
    const state = stateFromToolResult({
      isError: true,
      structuredContent: { code: "invalid_input" },
      content: [{ type: "text", text: "error invalid_input: provide exactly one" }],
    });
    expect(state).toEqual({ kind: "error", message: "error invalid_input: provide exactly one" });
  });

  it("refuses a result without a report", () => {
    expect(stateFromToolResult({ content: [{ type: "text", text: "hi" }] }).kind).toBe("error");
  });
});

describe("startSource", () => {
  it("prefers the embedded report and never connects", () => {
    const update = vi.fn();
    const createApp = vi.fn();
    startSource(update, { doc: docWith(JSON.stringify(demo)), win: framed, createApp });
    expect(update).toHaveBeenCalledWith({ kind: "report", report: demo });
    expect(createApp).not.toHaveBeenCalled();
  });

  it("is empty as a top-level page without a report", () => {
    const update = vi.fn();
    startSource(update, { doc: docWith(null), win: top, createApp: vi.fn() });
    expect(update).toHaveBeenCalledWith({ kind: "empty" });
  });

  it("waits for the host's tool result inside a frame", async () => {
    const states: ViewState[] = [];
    const app = new FakeApp();
    startSource((s) => states.push(s), { doc: docWith(null), win: framed, createApp: () => app });
    await Promise.resolve();
    expect(app.connected).toBe(true);
    app.send({ structuredContent: demo });
    expect(states).toEqual([{ kind: "waiting" }, { kind: "report", report: demo }]);
  });

  it("shows a connection failure", async () => {
    const states: ViewState[] = [];
    const app = new FakeApp();
    app.connect = () => Promise.reject(new Error("no host"));
    startSource((s) => states.push(s), { doc: docWith(null), win: framed, createApp: () => app });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(states.at(-1)).toEqual({ kind: "error", message: "Could not connect to the host: Error: no host" });
  });
});
