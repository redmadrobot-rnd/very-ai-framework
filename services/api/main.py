import os
from http.server import BaseHTTPRequestHandler, HTTPServer

# конфиг из окружения (app.env, разный для dev/prod)
GREETING = os.environ.get("GREETING", "api: ok")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(GREETING.encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
