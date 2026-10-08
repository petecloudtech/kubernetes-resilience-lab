"""Bounded, synthetic HTTP load generator using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen


def worker(url: str, deadline: float) -> tuple[dict[int, int], int, list[float]]:
    codes: dict[int, int] = {}
    network_errors = 0
    durations: list[float] = []
    while time.monotonic() < deadline:
        began = time.monotonic()
        try:
            with urlopen(url, timeout=5) as response:
                response.read()
                code = response.status
        except HTTPError as error:
            code = error.code
            error.close()
        except (URLError, TimeoutError, OSError):
            network_errors += 1
            continue
        durations.append(time.monotonic() - began)
        codes[code] = codes.get(code, 0) + 1
    return codes, network_errors, durations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Base /work URL")
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=50_000)
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in ("http", "https"):
        parser.error("URL must use http or https")
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be 1–600")
    if not 1 <= args.workers <= 64:
        parser.error("--workers must be 1–64")
    if not 0 <= args.iterations <= 100_000:
        parser.error("--iterations must be 0–100000")

    separator = "&" if "?" in args.url else "?"
    url = args.url + separator + urlencode({"iterations": args.iterations})
    started = time.monotonic()
    deadline = started + args.seconds
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda _: worker(url, deadline), range(args.workers)))
    counts: dict[int, int] = {}
    network_errors = 0
    durations: list[float] = []
    for worker_counts, worker_errors, worker_durations in results:
        network_errors += worker_errors
        durations.extend(worker_durations)
        for code, count in worker_counts.items():
            counts[code] = counts.get(code, 0) + count
    sorted_durations = sorted(durations)
    p95_ms = round(sorted_durations[int((len(sorted_durations) - 1) * 0.95)] * 1000, 2) if sorted_durations else None
    summary = {
        "duration_seconds": round(time.monotonic() - started, 2),
        "http_codes": {str(code): count for code, count in sorted(counts.items())},
        "network_errors": network_errors,
        "requests": sum(counts.values()),
        "mean_latency_ms": round(statistics.mean(durations) * 1000, 2) if durations else None,
        "p95_latency_ms": p95_ms,
        "synthetic": True,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if network_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
