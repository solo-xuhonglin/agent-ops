import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class FakeApi:
    """An in-process HTTP server speaking contract v1 wire shapes."""

    def __init__(self):
        self.routes: dict = {}
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def route(self, method: str, path: str, *, status: int = 200, body=None, handler=None):
        """Register one route; `handler(request) -> (status, body)` overrides static body."""
        self.routes[(method, path)] = {"status": status, "body": body, "handler": handler, "raw": None}

    def route_raw(self, path: str, scripts: list[list[tuple[float, bytes]]], hold: float = 5.0, drop: bool = False):
        """Serve GET as text/event-stream; each connection pops the next script.

        With drop=True the connection closes after the script instead of
        holding, simulating a dropped SSE link. The last script repeats.
        """
        self.routes[("GET", path)] = {
            "status": 200,
            "body": None,
            "handler": None,
            "raw": list(scripts),
            "hold": hold,
            "drop": drop,
        }

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def _make_handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _dispatch(self, method: str):
                path, _, query = self.path.partition("?")
                record = {
                    "method": method,
                    "path": path,
                    "query": {key: values[0] for key, values in parse_qs(query).items()},
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                }
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if raw:
                    record["json"] = json.loads(raw)
                with api._lock:
                    api.requests.append(record)

                route = api.routes.get((method, path))
                if route is None:
                    self._send_json(404, {"error": {"code": "not_found", "message": f"no route for {method} {path}"}})
                elif route["raw"] is not None:
                    self._send_stream(route)
                elif route["handler"] is not None:
                    status, body = route["handler"](record)
                    self._send_json(status, body)
                else:
                    self._send_json(route["status"], route["body"])

            def _send_json(self, status: int, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _send_stream(self, route):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                script = route["raw"].pop(0) if len(route["raw"]) > 1 else route["raw"][0]
                for delay, chunk in script:
                    if delay:
                        time.sleep(delay)
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                        return
                if route["drop"]:
                    self.close_connection = True
                else:
                    time.sleep(route["hold"])

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def log_message(self, *args):
                pass

        return Handler
