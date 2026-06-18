import importlib.util
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

# поднимает реальный сервер — относим к heavy: пропускается на feature push,
# гоняется на PR
pytestmark = pytest.mark.heavy


def _load_handler():
    path = Path(__file__).resolve().parents[2] / "services" / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("api_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Handler


def test_root_returns_ok():
    server = HTTPServer(("127.0.0.1", 0), _load_handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert r.status == 200
            assert r.read() == b"api: ok"
    finally:
        server.shutdown()
