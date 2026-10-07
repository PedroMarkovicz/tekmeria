import { useEffect, useState } from "react";
import { AuditView } from "./AuditView";
import { startSource, type ViewState } from "./source";

/** Shows whatever the source provides: a report, an error, or a short explanation. */
export function Root({ start = startSource }: { start?: typeof startSource }) {
  const [state, setState] = useState<ViewState>({ kind: "waiting" });
  useEffect(() => start(setState), [start]);
  switch (state.kind) {
    case "report":
      return <AuditView report={state.report} />;
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
