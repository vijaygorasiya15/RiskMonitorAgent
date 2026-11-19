"""Tiny webhook receiver for the local demo. Stores payloads per path (= per tenant).

POST /<tenant>            receive an alert
GET  /received            all alerts grouped by path
GET  /received/<tenant>   alerts for one tenant
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECEIVED: dict[str, list[dict]] = {}
SEEN_KEYS: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("Idempotency-Key", "")
        path = self.path.strip("/")
        dedupe = f"{path}:{key}"
        if key and dedupe in SEEN_KEYS:
            return self._send(200, {"ok": True, "duplicate": True})
        SEEN_KEYS.add(dedupe)
        RECEIVED.setdefault(path, []).append(payload)
        print(
            f"[ALERT -> /{path}] {payload.get('severity')} txn={payload['transaction']['transaction_id']} "
            f"reason={payload.get('reason')}",
            flush=True,
        )
        self._send(200, {"ok": True})

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if parts[0] == "received":
            if len(parts) > 1:
                return self._send(200, RECEIVED.get(parts[1], []))
            return self._send(200, RECEIVED)
        self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
