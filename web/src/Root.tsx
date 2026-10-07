import { Component, useEffect, useState, type ReactNode } from "react";
import { AuditView } from "./AuditView";
import { startSource, type ViewState } from "./source";

/** A report this view cannot draw (say, a verdict added by a newer server) shows a message
 * instead of an empty frame. */
class ReportBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };

  static getDerivedStateFromError(error: unknown) {
    return { error: String(error) };
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <main className="audit">
        <p role="alert" className="notice tone-bad">
          Could not display this report ({this.state.error}). The audit_answer text output
          still has every verdict.
        </p>
      </main>
    );
  }
}

/** Shows whatever the source provides: a report, an error, or a short explanation. */
export function Root({ start = startSource }: { start?: typeof startSource }) {
  const [state, setState] = useState<ViewState>({ kind: "waiting" });
  useEffect(() => start(setState), [start]);
  switch (state.kind) {
    case "report":
      return (
        <ReportBoundary>
          <AuditView report={state.report} />
        </ReportBoundary>
      );
    case "error":
      return (
        <main className="audit">
          <p role="alert" className="notice tone-bad">
            {state.message}
          </p>
        </main>
      );
    case "empty":
      return (
        <main className="audit">
          <p className="notice">
            No audit report here. Open this view from an MCP host after calling audit_answer, or
            run <code>medcite demo --open</code>.
          </p>
        </main>
      );
    case "waiting":
      return (
        <main className="audit">
          <p className="notice">Waiting for the audit result...</p>
        </main>
      );
  }
}
