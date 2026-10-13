// Where the report comes from: embedded in the page (the standalone page), or the result
// of audit_answer sent by an MCP Apps host (inside a chat).
import { App, applyDocumentTheme } from "@modelcontextprotocol/ext-apps/app-with-deps";
import type { AuditReport } from "./report";

export const REPORT_ELEMENT_ID = "tekmeria-report";

export type ViewState =
  | { kind: "waiting" }
  | { kind: "report"; report: AuditReport }
  | { kind: "error"; message: string }
  | { kind: "cancelled"; reason?: string }
  | { kind: "empty" };

/** The subset of a CallToolResult the view reads. */
export interface ToolResult {
  isError?: boolean;
  structuredContent?: Record<string, unknown>;
  content?: { type: string; text?: string }[];
}

function isReport(value: unknown): value is AuditReport {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    Array.isArray(candidate.claims) &&
    typeof candidate.summary === "object" &&
    candidate.summary !== null
  );
}

/** The report embedded in `<script type="application/json" id="tekmeria-report">`. */
export function embeddedReport(doc: Document): ViewState | null {
  const text = doc.getElementById(REPORT_ELEMENT_ID)?.textContent?.trim();
  if (!text) return null;
  try {
    const data: unknown = JSON.parse(text);
    return isReport(data)
      ? { kind: "report", report: data }
      : { kind: "error", message: "The embedded data is not an audit report." };
  } catch {
    return { kind: "error", message: "The embedded report is not valid JSON." };
  }
}

/** What to show for a tool result from the host. */
export function stateFromToolResult(result: ToolResult): ViewState {
  const text = (result.content ?? [])
    .filter((block) => block.type === "text" && block.text)
    .map((block) => block.text)
    .join("\n");
  if (result.isError) return { kind: "error", message: text || "The audit failed." };
  if (isReport(result.structuredContent)) return { kind: "report", report: result.structuredContent };
  return { kind: "error", message: "The tool result carries no audit report." };
}

/** The parts of the ext-apps App the view uses, so tests can pass a fake. */
export interface HostApp {
  addEventListener(event: "toolresult", handler: (result: ToolResult) => void): void;
  addEventListener(event: "toolcancelled", handler: (params: { reason?: string }) => void): void;
  addEventListener(
    event: "hostcontextchanged",
    handler: (context: { theme?: "light" | "dark" }) => void,
  ): void;
  connect(): Promise<void>;
  getHostContext(): { theme?: "light" | "dark" } | undefined;
}

export function createHostApp(): HostApp {
  // No cast: the compiler checks the library's App against HostApp.
  return new App({ name: "tekmeria audit view", version: "0.1.0" }, {}, { autoResize: true });
}

/** Start the view: an embedded report wins; inside a frame, wait for the host's tool result;
 * a top-level page without a report is empty. */
export function startSource(
  update: (state: ViewState) => void,
  env: { doc: Document; win: Window; createApp: () => HostApp } = {
    doc: document,
    win: window,
    createApp: createHostApp,
  },
): void {
  const embedded = embeddedReport(env.doc);
  if (embedded) {
    update(embedded);
    return;
  }
  if (env.win.parent === env.win) {
    update({ kind: "empty" });
    return;
  }
  update({ kind: "waiting" });
  const app = env.createApp();
  let answered = false; // a late cancel notice must not hide a result already shown
  app.addEventListener("toolresult", (result) => {
    answered = true;
    update(stateFromToolResult(result));
  });
  app.addEventListener("toolcancelled", (params) => {
    if (!answered) update({ kind: "cancelled", reason: params.reason });
  });
  app.addEventListener("hostcontextchanged", (context) => {
    if (context.theme) applyDocumentTheme(context.theme);
  });
  app.connect().then(
    () => {
      const theme = app.getHostContext()?.theme;
      if (theme) applyDocumentTheme(theme);
    },
    (error: unknown) => update({ kind: "error", message: `Could not connect to the host: ${String(error)}` }),
  );
}
