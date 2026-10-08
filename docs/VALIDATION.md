# Validation record

Project preparation date: 2026-10-08. The commands below were run in the provided macOS workspace.

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | 5 tests passed: health and drain, startup gating, work and synthetic failure, invalid inputs, unknown path. Tests use in-memory HTTP requests because the workspace sandbox disallows binding a localhost port. |
| `kubectl kustomize k8s` via `tools/check_manifests.py` | Passed using a Python environment with PyYAML 6.0.3; rendered 6 objects and checked probes, selectors, HPA target, PDB, drain hook, and labeled load Job access. The default Python in the preparation workspace lacks PyYAML, so first install `requirements-dev.txt` in a virtual environment as shown in the README. |
| Docker build and smoke test | Not run locally: the Docker CLI is present, but the workspace cannot access its daemon socket. Configured in GitHub Actions CI for future verification. |
| kind deployment, HPA behavior, and NetworkPolicy enforcement | Not run locally: kind is unavailable and the Docker daemon is inaccessible. These require a cluster and a network-policy-capable CNI. |

The [sample metrics](../examples/synthetic-metrics.prom) were produced by calling `AppState.record_work` with three specified, synthetic durations. They are a format example, **not measured cluster traffic**.
