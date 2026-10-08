"""Dependency-free HTTP service for exercising Kubernetes reliability controls.

This is a local teaching service. It does not call external systems or collect data.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
DEFAULT_ITERATIONS = 5_000
MAX_ITERATIONS = 100_000


class AppState:
    def __init__(self, drain_file: str, startup_delay_seconds: float = 0.0) -> None:
        self.started_at = time.monotonic()
        self.drain_file = Path(drain_file)
        self.startup_delay_seconds = startup_delay_seconds
        self.draining = threading.Event()
        self.lock = threading.Lock()
        self.work_counts = {200: 0, 503: 0}
        self.work_duration_count = 0
        self.work_duration_sum = 0.0
        self.work_duration_buckets = {bucket: 0 for bucket in BUCKETS}

    def started(self) -> bool:
        return time.monotonic() - self.started_at >= self.startup_delay_seconds

    def ready(self) -> bool:
        return self.started() and not self.draining.is_set() and not self.drain_file.exists()

    def record_work(self, code: int, seconds: float) -> None:
        with self.lock:
            self.work_counts[code] += 1
            self.work_duration_count += 1
            self.work_duration_sum += seconds
            for bucket in BUCKETS:
                if seconds <= bucket:
                    self.work_duration_buckets[bucket] += 1

    def metrics(self) -> str:
        with self.lock:
            counts = self.work_counts.copy()
            duration_count = self.work_duration_count
            duration_sum = self.work_duration_sum
            buckets = self.work_duration_buckets.copy()
        lines = [
            "# HELP krl_ready Whether this pod is ready to receive traffic.",
            "# TYPE krl_ready gauge",
            f"krl_ready {int(self.ready())}",
            "# HELP krl_work_requests_total Completed work requests by status code.",
            "# TYPE krl_work_requests_total counter",
        ]
        for code, count in sorted(counts.items()):
            lines.append(f'krl_work_requests_total{{code="{code}"}} {count}')
        lines += [
            "# HELP krl_work_duration_seconds Work computation duration.",
            "# TYPE krl_work_duration_seconds histogram",
        ]
        for bucket, count in buckets.items():
            lines.append(f'krl_work_duration_seconds_bucket{{le="{bucket:g}"}} {count}')
        lines += [
            f'krl_work_duration_seconds_bucket{{le="+Inf"}} {duration_count}',
            f"krl_work_duration_seconds_sum {duration_sum:.9f}",
            f"krl_work_duration_seconds_count {duration_count}",
            "",
        ]
        return "\n".join(lines)


def make_handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ResilienceLab/1.0"

        def log_message(self, format: str, *args: object) -> None:
            print(json.dumps({"event": "http", "message": format % args}), flush=True)

        def respond(self, code: int, payload: dict[str, object]) -> None:
            body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            url = urlsplit(self.path)
            if url.path == "/livez":
                self.respond(HTTPStatus.OK, {"status": "live"})
                return
            if url.path == "/startupz":
                started = state.started()
                self.respond(HTTPStatus.OK if started else HTTPStatus.SERVICE_UNAVAILABLE,
                             {"status": "started" if started else "starting"})
                return
            if url.path == "/readyz":
                ready = state.ready()
                self.respond(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                             {"status": "ready" if ready else "unready"})
                return
            if url.path == "/metrics":
                body = state.metrics().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/work":
                self.handle_work(url.query)
                return
            self.respond(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def handle_work(self, query: str) -> None:
            params = parse_qs(query, keep_blank_values=True)
            try:
                raw_iterations = params.get("iterations", [str(DEFAULT_ITERATIONS)])
                raw_fail = params.get("fail", ["0"])
                if len(raw_iterations) != 1 or len(raw_fail) != 1:
                    raise ValueError("parameter repeated")
                iterations = int(raw_iterations[0])
                if not 0 <= iterations <= MAX_ITERATIONS or raw_fail[0] not in ("0", "1"):
                    raise ValueError("parameter outside allowed range")
            except ValueError:
                self.respond(HTTPStatus.BAD_REQUEST, {"error": "invalid_parameters"})
                return

            began = time.monotonic()
            digest = b"resilience-lab"
            for _ in range(iterations):
                digest = hashlib.sha256(digest).digest()
            failed = raw_fail[0] == "1"
            code = HTTPStatus.SERVICE_UNAVAILABLE if failed else HTTPStatus.OK
            duration = time.monotonic() - began
            state.record_work(code, duration)
            if failed:
                self.respond(code, {"error": "synthetic_failure", "synthetic": True})
            else:
                self.respond(code, {"checksum": digest.hex()[:16], "iterations": iterations})

    return Handler


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    if not 0 <= port <= 65535:
        raise ValueError("PORT must be between 0 and 65535")
    startup_delay = float(os.environ.get("STARTUP_DELAY_SECONDS", "0"))
    if startup_delay < 0:
        raise ValueError("STARTUP_DELAY_SECONDS must be nonnegative")
    state = AppState(os.environ.get("DRAIN_FILE", "/tmp/krl-unready"), startup_delay)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state))
    server.daemon_threads = False

    def begin_shutdown(_signum: int, _frame: object) -> None:
        state.draining.set()
        # shutdown() must run outside serve_forever()'s thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, begin_shutdown)
    signal.signal(signal.SIGINT, begin_shutdown)
    print(json.dumps({"event": "listening", "port": server.server_port}), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()  # Wait for in-flight request threads to complete.
        print(json.dumps({"event": "stopped"}), flush=True)


if __name__ == "__main__":
    main()
