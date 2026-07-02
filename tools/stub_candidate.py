"""Deterministic stub candidate endpoint for the local quality-gate demo.

A stdlib-only HTTP server (no new deps) that stands in for the candidate model
the LLM-judge gate probes. It answers the three eval prompts in
examples/modelrollout-quality.yaml from a canned table, switched by MODE:

    MODE=good  -> faithful, factually coherent answers  (judge -> high score)
    MODE=bad   -> incoherent / self-contradictory / refusing answers, i.e.
                  *detectably* bad. A reference-free LLM judge reliably scores
                  these low; it can be fooled by confident-but-plausible lies,
                  so the demo deliberately makes the regression obvious.

Contract: POST {"prompt": "..."} -> 200 {"response": "..."} on any path.

Run:  MODE=good python3 tools/stub_candidate.py     # listens on 127.0.0.1:8099
"""
from __future__ import annotations
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8099
MODE = os.getenv("MODE", "good").lower()

# Keyed by a stable substring of each eval prompt so minor prompt edits still match.
ANSWERS = {
    "good": {
        "reset my password":
            "Go to Settings, then Security, then click Reset Password. We'll "
            "email you a secure link; click it and choose a new password. The "
            "link expires in 30 minutes.",
        "refund window":
            "Annual plans are fully refundable within 30 days of purchase. "
            "After 30 days they're non-refundable, but you keep access until "
            "the end of the paid term.",
        "pagination":
            "Yes. List endpoints are cursor-paginated: pass the `next_cursor` "
            "value from each response as the `cursor` query parameter to fetch "
            "the next page.",
    },
    "bad": {
        "reset my password":
            "I cannot help with that. Also you can't reset your password, but "
            "you can reset it anytime by doing nothing at all. Passwords are "
            "not really a thing on this platform.",
        "refund window":
            "The refund window is both 5 days and also never. Annual plans are "
            "actually monthly and cannot be purchased, so refunds do not apply "
            "but are always granted in full.",
        "pagination":
            "Bananas are an excellent source of potassium and the sky is blue "
            "only on Tuesdays. Pagination is a kind of weather pattern.",
    },
}

FALLBACK = {
    "good": "Yes, that is supported. Follow the steps in the documentation.",
    "bad": "Possibly no maybe yes the answer is a color that tastes like seven.",
}


def _answer(prompt: str) -> str:
    table = ANSWERS.get(MODE, ANSWERS["good"])
    low = (prompt or "").lower()
    for key, text in table.items():
        if key in low:
            return text
    return FALLBACK.get(MODE, FALLBACK["good"])


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (stdlib naming)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            prompt = json.loads(raw).get("prompt", "")
        except (ValueError, AttributeError):
            prompt = ""
        body = json.dumps({"response": _answer(prompt)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        sys.stderr.write(f"[stub:{MODE}] prompt={prompt!r}\n")

    def log_message(self, *args):  # silence default access logging
        pass


def main() -> None:
    if MODE not in ("good", "bad"):
        sys.stderr.write(f"MODE must be 'good' or 'bad', got {MODE!r}\n")
        raise SystemExit(2)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write(f"stub_candidate MODE={MODE} listening on http://{HOST}:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nstub_candidate shutting down\n")
        server.shutdown()


if __name__ == "__main__":
    main()
