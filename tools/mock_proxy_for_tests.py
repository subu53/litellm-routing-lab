"""A minimal mock of LiteLLM's POST /auto_router/test_routing, for testing
eval/archive_results.sh without a Gemini key or a real proxy.

It answers the way the real endpoint does: a routing_decision with a numeric
score, a tier, and a signals list. Scores are deterministic and depend on the
prompt length, which is enough to exercise every path in the archive script.

Usage:  python3 tools/_mock_proxy.py [port]
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4789


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        if self.path != "/auto_router/test_routing":
            self.send_error(404, "only /auto_router/test_routing is mocked")
            return

        messages = body.get("messages") or [{}]
        text = (messages[-1].get("content") or "") if messages else ""
        words = len(text.split())

        # Deterministic pseudo-score: longer prompts score higher, plus a bump for
        # the phrases the real scorer recognises.
        score = round(-0.1 + min(words, 60) / 200.0, 4)
        signals = []
        if words < 15:
            signals.append(f"short ({words} tokens)")
        if "step by step" in text.lower() or "reason" in text.lower():
            signals.append("reasoning (step by step)")
            score = round(score + 0.175, 4)
        if any(t in text.lower() for t in ("event-driven", "idempotency", "observability")):
            signals.append("technical (event-driven, idempotency, observability)")
            score = round(score + 0.25, 4)

        if score < 0.15:
            tier = "SIMPLE"
        elif score < 0.35:
            tier = "MEDIUM"
        elif score < 0.60:
            tier = "COMPLEX"
        else:
            tier = "REASONING"

        payload = json.dumps({
            "routed_model": "gemini-flash-lite" if tier in ("SIMPLE", "MEDIUM")
                            else "gemini-flash",
            "routed_model_configured": True,
            "routing_decision": {
                "router_model_name": "mock_router",
                "router_type": "complexity",
                "routed_model": "gemini-flash-lite" if tier in ("SIMPLE", "MEDIUM")
                                else "gemini-flash",
                "cause": "heuristic_scorer",
                "tier": tier,
                "score": score,
                "signals": signals,
            },
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass          # keep the test output readable


if __name__ == "__main__":
    print(f"mock test_routing on http://127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
