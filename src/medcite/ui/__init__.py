"""The audit view (spec 10): one self-contained HTML file, built from web/ and shipped as
package data, so serving it never needs Node."""

from importlib.resources import files

AUDIT_VIEW_URI = "ui://medcite/audit-view"
REPORT_SLOT = '<script type="application/json" id="medcite-report"></script>'


def audit_view_html() -> str:
    return files("medcite.ui").joinpath("audit-view.html").read_text(encoding="utf-8")


def standalone_page(report_json: str) -> str:
    """The view with an audit report embedded, for a browser outside any MCP host.

    Every `<` in the JSON becomes the escape `\\u003c`, so no string in the report can
    close the script tag; JSON.parse turns it back into `<`.
    """
    html = audit_view_html()
    if html.count(REPORT_SLOT) != 1:
        raise RuntimeError("audit-view.html has no single report slot; rebuild it from web/")
    payload = report_json.replace("<", "\\u003c")
    return html.replace(REPORT_SLOT, REPORT_SLOT.replace("></script>", f">{payload}</script>"))
