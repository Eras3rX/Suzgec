"""Временный сервер для JS-тестов: раздаёт файлы проекта на :8001 и проксирует /api/* на сервер Süzgəc (:8000).

    .venv\Scripts\python.exe tests/js/serve.py . 8001
"""
import http.server
import sys
import urllib.error
import urllib.request

ROOT = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8001


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):  # тишина
        pass

    def _proxy(self):
        body = None
        if self.command == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        req = urllib.request.Request(
            "http://127.0.0.1:8000" + self.path,
            data=body,
            method=self.command,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=20)
            status = resp.status
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
        data = resp.read()
        self.send_response(status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self._proxy() if self.path.startswith("/api/") else self.send_error(404)

    def do_GET(self):
        self._proxy() if self.path.startswith("/api/") else super().do_GET()


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
