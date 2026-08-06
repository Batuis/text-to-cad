"""A small local surface for importing an RC CAD handoff and generating its CAD artifacts.

── What a person can do here that they could not before ─────────────

Export a handoff from Stabileo, drop the downloaded `.json` on this page, and get a STEP they can
open — without running a Python command, knowing where the repository is, copying files into a
catalogue directory, or building a viewer query string.

── Why stdlib only ─────────────────────────────────────────────────

`http.server` and a single self-contained HTML document, with no new runtime dependency. The heavy
dependencies this package already needs are OCCT and IfcOpenShell; adding a web framework so a
non-developer can click one button would be a poor trade, and the surface is one page and two
endpoints.

── What this does NOT do ───────────────────────────────────────────

It does not convert anything itself. Every artifact comes from `service.import_handoff_bytes`, which
calls the existing pipeline. It does not talk to the CAD viewer either — it only builds the URL that
opens the generated assembly there, so the viewer stays a generic CAD file viewer with no knowledge
of this schema.

Bound to 127.0.0.1 by default, because this reads and writes local files on behalf of whoever can
reach it.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .manifest import CONTRACTS
from .service import (
    ARTIFACT_ROLES,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_VIEWER_ORIGIN,
    import_handoff_bytes,
)

#: The surface's own port.
#:
#: 4179 sits beside the two services a reviewer already has open — 4003 for Stabileo and 4178 for the
#: CAD viewer — and collides with neither.
DEFAULT_PORT = 4179
DEFAULT_HOST = "127.0.0.1"

#: Refuse a body larger than this. A handoff for one footing is ~150 kB; ten megabytes is generous
#: and still bounds what an accidental upload can cost.
MAX_BODY_BYTES = 10 * 1024 * 1024

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RC CAD handoff</title>
<style>
  :root { color-scheme: light dark; --line: #8884; --bad: #b3261e; --good: #1b5e20; }
  @media (prefers-color-scheme: dark) { :root { --bad: #f2b8b5; --good: #a5d6a7; } }
  body { font: 15px/1.55 system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem 1.25rem;
         max-width: 54rem; margin-inline: auto; }
  h1 { font-size: 1.35rem; margin: 0 0 .35rem; }
  .lede { margin: 0 0 1.5rem; opacity: .8; }
  #drop { border: 2px dashed var(--line); border-radius: .6rem; padding: 2.25rem 1.5rem;
          text-align: center; transition: background .15s, border-color .15s; }
  #drop.over { border-color: currentColor; background: #8881; }
  button { font: inherit; padding: .55rem 1rem; border-radius: .4rem; border: 1px solid var(--line);
           background: #8881; cursor: pointer; }
  button.primary { background: #2563eb; color: #fff; border-color: #2563eb; font-weight: 600; }
  button:disabled { opacity: .5; cursor: default; }
  table { border-collapse: collapse; width: 100%; margin: .75rem 0; }
  th, td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { font-weight: 600; }
  code { font-family: ui-monospace, monospace; font-size: .92em; }
  .panel { border: 1px solid var(--line); border-radius: .5rem; padding: 1rem 1.1rem;
           margin-top: 1.25rem; }
  .err { color: var(--bad); }
  .ok { color: var(--good); }
  .muted { opacity: .75; font-size: .93em; }
  details { margin-top: .6rem; }
  ul { margin: .4rem 0 0; padding-left: 1.2rem; }
</style>
</head>
<body>
<h1>RC CAD handoff</h1>
<p class="lede">
  A Stabileo RC CAD handoff is a <strong>semantic handoff file, not a CAD drawing</strong>. It
  describes the reinforcement — bar families, layer order, covers, findings and verdicts — and no CAD
  program can open it directly. Drop it here and this tool generates the CAD artifacts from it.
</p>

<div id="drop">
  <p style="margin:0 0 .9rem"><strong>Drag the downloaded <code>.json</code> here</strong></p>
  <button id="pick" class="primary" type="button">Open RC handoff JSON…</button>
  <input id="file" type="file" accept=".json,application/json" hidden>
  <p class="muted" style="margin:.9rem 0 0">
    The file you exported from Stabileo, usually named
    <code>rc-cad-handoff-v2-&lt;footing&gt;-det&lt;n&gt;-dem&lt;n&gt;.json</code>.
  </p>
</div>

<div id="status"></div>

<div class="panel muted">
  <strong>What gets generated</strong>
  <table>
    <tr><th>STEP</th><td>The assembly. This is what the CAD viewer displays, and what a CAD tool
      opens.</td></tr>
    <tr><th>GLB</th><td>A lightweight 3-D visualisation of the same assembly.</td></tr>
    <tr><th>IFC4</th><td>For inspection in an external BIM tool. <strong>The companion CAD viewer
      does not read IFC</strong>, so it will not appear there.</td></tr>
    <tr><th>cad-review.json</th><td>Review metadata — the cross-check, the findings and the
      constructibility verdict. A report, not geometry.</td></tr>
  </table>
</div>

<script type="module">
const $ = (id) => document.getElementById(id);
const drop = $("drop"), status = $("status"), file = $("file");

$("pick").addEventListener("click", () => file.click());
file.addEventListener("change", () => { if (file.files[0]) send(file.files[0]); });

for (const ev of ["dragenter", "dragover"]) {
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
}
for (const ev of ["dragleave", "drop"]) {
  drop.addEventListener(ev, () => drop.classList.remove("over"));
}
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f) send(f);
});

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function busy(name) {
  status.innerHTML = `<div class="panel" data-testid="state-busy">
    <strong>Generating CAD artifacts…</strong>
    <p class="muted" style="margin:.4rem 0 0">Read <code>${esc(name)}</code>. Building the STEP,
    GLB, IFC and review outputs — the STEP has to exist before the viewer can show anything.</p>
  </div>`;
}

function failed(r) {
  status.innerHTML = `<div class="panel" data-testid="state-error">
    <strong class="err">Could not use that file</strong>
    <p style="margin:.5rem 0 0" data-testid="error-message">${esc(r.errorMessage || "Unknown error.")}</p>
    <p class="muted" style="margin:.4rem 0 0">File: <code>${esc(r.sourceName || "")}</code>
      · code <code data-testid="error-code">${esc(r.errorCode || "")}</code></p>
    ${r.errorDetail ? `<details><summary class="muted">Technical detail</summary>
      <pre style="white-space:pre-wrap"><code>${esc(r.errorDetail)}</code></pre></details>` : ""}
  </div>`;
}

function succeeded(r) {
  const s = r.summary || {};
  const fam = Object.entries(s.families || {})
    .map(([k, n]) => `${esc(k)} ${n}`).join(" · ");
  const rows = (r.artifacts || []).map((a) => `<tr>
      <th>${esc(a.label)}</th>
      <td><code>${esc(a.name)}</code><br><span class="muted">${esc(a.purpose)}</span><br>
        <span class="muted">In the CAD viewer: ${esc(a.viewable)}</span></td>
    </tr>`).join("");
  status.innerHTML = `<div class="panel" data-testid="state-success">
    <strong class="ok">Handoff accepted and artifacts generated</strong>
    <p style="margin:.5rem 0 0">
      <code data-testid="loaded-filename">${esc(r.sourceName)}</code> —
      <span data-testid="loaded-schema">${esc(r.schema)} v${esc(r.schemaVersion)}</span>,
      footing <strong>${esc(r.subject)}</strong>.
    </p>
    <p class="muted" style="margin:.35rem 0 0">
      ${esc(s.bars ?? "?")} bars — ${fam} · ${esc(s.findings ?? 0)} finding(s) ·
      constructible: <strong>${s.constructible === false ? "no" : "yes"}</strong> ·
      bottom anchorage: <strong>${esc(s.bottomAnchorage ?? "—")}</strong> ·
      top reinforcement: ${esc(s.topReinforcement ?? "—")} ·
      punching moment transfer: ${esc(s.punchingMomentTransfer ?? "—")}
    </p>
    <p style="margin:1rem 0 .25rem">
      <button class="primary" type="button" data-testid="open-in-viewer"
              onclick="window.open('${esc(r.viewerUrl)}','_blank')">
        Open assembly in CAD viewer
      </button>
    </p>
    <p class="muted" style="margin:.35rem 0 0">
      If nothing opens, the CAD viewer is not running. Start it and use this link:
      <br><a data-testid="viewer-url" href="${esc(r.viewerUrl)}" target="_blank"
             rel="noreferrer">${esc(r.viewerUrl)}</a>
    </p>
    <table>${rows}</table>
    <p class="muted" style="margin:0">Written to <code data-testid="output-dir">${esc(r.outputDir)}</code></p>
  </div>`;
}

async function send(f) {
  busy(f.name);
  try {
    const res = await fetch(`/api/import?name=${encodeURIComponent(f.name)}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: await f.text(),
    });
    const r = await res.json();
    (r.ok ? succeeded : failed)(r);
  } catch (e) {
    failed({ sourceName: f.name, errorCode: "TRANSPORT",
             errorMessage: "The handoff tool did not respond. Is it still running?",
             errorDetail: String(e) });
  } finally {
    file.value = "";
  }
}
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "rc-cad-handoff/1"
    #: Injected by `serve`.
    output_root: Path = DEFAULT_OUTPUT_ROOT
    viewer_origin: str = DEFAULT_VIEWER_ORIGIN

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # One tidy line per request instead of the default's stderr noise.
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        # No caching: the page is the tool, and a stale one would be confusing after an update.
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, object]) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path.split("?", 1)[0] == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "rc-cad-handoff",
                    "readsSchemas": [CONTRACTS[k] for k in sorted(CONTRACTS)],
                    "outputRoot": str(self.output_root),
                    "viewerOrigin": self.viewer_origin,
                    "artifactRoles": ARTIFACT_ROLES,
                },
            )
            return
        self._json(404, {"ok": False, "errorCode": "NOT_FOUND", "errorMessage": "No such path."})

    def do_POST(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if path != "/api/import":
            self._json(
                404, {"ok": False, "errorCode": "NOT_FOUND", "errorMessage": "No such path."}
            )
            return

        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            self._json(
                400,
                {
                    "ok": False,
                    "sourceName": "",
                    "errorCode": "MALFORMED_JSON",
                    "errorMessage": "No file content arrived. Try selecting the file again.",
                    "errorDetail": "empty request body",
                },
            )
            return
        if length > MAX_BODY_BYTES:
            self._json(
                413,
                {
                    "ok": False,
                    "sourceName": "",
                    "errorCode": "TOO_LARGE",
                    "errorMessage": (
                        f"That file is larger than {MAX_BODY_BYTES // (1024 * 1024)} MB, which is "
                        "far larger than any RC CAD handoff. It is probably not a handoff."
                    ),
                    "errorDetail": f"content-length {length}",
                },
            )
            return

        payload = self.rfile.read(length)
        # The display name only. It never touches the filesystem — see `service.safe_stem`.
        name = "handoff.json"
        for part in query.split("&"):
            if part.startswith("name="):
                from urllib.parse import unquote_plus

                name = unquote_plus(part[5:]) or name

        outcome = import_handoff_bytes(
            payload,
            source_name=name,
            output_root=self.output_root,
            viewer_origin=self.viewer_origin,
        )
        self._json(200 if outcome.ok else 422, outcome.as_json())


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    output_root: Path | None = None,
    viewer_origin: str = DEFAULT_VIEWER_ORIGIN,
) -> ThreadingHTTPServer:
    """Build the server. The caller decides whether to block on it."""
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "output_root": (Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT),
            "viewer_origin": viewer_origin,
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rc-cad-handoff-web",
        description=(
            "Local surface for importing a Stabileo RC CAD handoff and generating its CAD "
            "artifacts. Open the printed URL in a browser."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--output-root",
        metavar="DIR",
        help=f"Where generated artifacts go (default: {DEFAULT_OUTPUT_ROOT}).",
    )
    parser.add_argument(
        "--viewer-origin",
        default=DEFAULT_VIEWER_ORIGIN,
        help=f"Origin of the generic CAD viewer (default: {DEFAULT_VIEWER_ORIGIN}).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    httpd = serve(
        host=args.host,
        port=args.port,
        output_root=Path(args.output_root).expanduser() if args.output_root else None,
        viewer_origin=args.viewer_origin,
    )
    root = getattr(httpd.RequestHandlerClass, "output_root")
    print(f"RC CAD handoff surface: http://{args.host}:{args.port}/")
    print(f"  artifacts → {root}")
    print(f"  viewer    → {args.viewer_origin}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
