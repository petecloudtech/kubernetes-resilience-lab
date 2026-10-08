"""Tests use an ephemeral localhost server; no cluster or network is required."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from app.service import AppState, make_handler


class FakeSocket:
    """Run BaseHTTPRequestHandler without binding a port in unit tests."""

    def __init__(self, path: str) -> None:
        self.input = io.BytesIO(f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
        self.output = io.BytesIO()

    def makefile(self, mode: str, _buffer_size: int) -> io.BytesIO:
        if mode != "rb":
            raise ValueError(mode)
        return self.input

    def sendall(self, data: bytes) -> None:
        self.output.write(data)


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.drain_file = Path(self.directory.name) / "unready"
        self.state = AppState(str(self.drain_file))

    def get(self, path: str) -> tuple[int, str]:
        socket = FakeSocket(path)
        make_handler(self.state)(socket, ("127.0.0.1", 12345), object())
        header, body = socket.output.getvalue().split(b"\r\n\r\n", 1)
        code = int(header.split(b" ", 2)[1])
        return code, body.decode("utf-8")

    def test_health_routes_and_drain(self) -> None:
        self.assertEqual(self.get("/livez")[0], 200)
        self.assertEqual(self.get("/startupz")[0], 200)
        self.assertEqual(self.get("/readyz")[0], 200)
        self.drain_file.touch()
        self.assertEqual(self.get("/readyz")[0], 503)
        self.assertEqual(self.get("/livez")[0], 200)
        self.drain_file.unlink()
        self.assertEqual(self.get("/readyz")[0], 200)
        self.state.draining.set()
        self.assertEqual(self.get("/readyz")[0], 503)

    def test_startup_delay_gates_startup_and_readiness(self) -> None:
        self.state.startup_delay_seconds = 60
        self.assertEqual(self.get("/startupz")[0], 503)
        self.assertEqual(self.get("/readyz")[0], 503)
        self.assertEqual(self.get("/livez")[0], 200)

    def test_work_success_and_synthetic_failure(self) -> None:
        code, body = self.get("/work?iterations=10")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["iterations"], 10)
        code, body = self.get("/work?fail=1&iterations=0")
        self.assertEqual(code, 503)
        self.assertTrue(json.loads(body)["synthetic"])
        _, metrics = self.get("/metrics")
        self.assertIn('krl_work_requests_total{code="200"} 1', metrics)
        self.assertIn('krl_work_requests_total{code="503"} 1', metrics)
        self.assertIn('krl_work_duration_seconds_bucket{le="+Inf"} 2', metrics)

    def test_invalid_inputs_do_not_record_work(self) -> None:
        for query in ("iterations=-1", "iterations=100001", "iterations=x", "fail=yes", "iterations=1&iterations=2"):
            with self.subTest(query=query):
                self.assertEqual(self.get("/work?" + query)[0], 400)
        self.assertIn('krl_work_requests_total{code="200"} 0', self.get("/metrics")[1])

    def test_unknown_path(self) -> None:
        self.assertEqual(self.get("/missing")[0], 404)


if __name__ == "__main__":
    unittest.main()
