"""Render Kustomize and assert the reliability controls are wired together."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "k8s")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    objects = [item for item in yaml.safe_load_all(rendered) if item]
    by_kind = {item["kind"]: item for item in objects}
    expected = {"Namespace", "Deployment", "Service", "HorizontalPodAutoscaler", "PodDisruptionBudget", "NetworkPolicy"}
    require(set(by_kind) == expected, f"expected {expected}, got {set(by_kind)}")
    for item in objects:
        if item["kind"] != "Namespace":
            require(item["metadata"].get("namespace") == "resilience-lab", f"wrong namespace for {item['kind']}")

    deployment = by_kind["Deployment"]["spec"]
    require(deployment["replicas"] == 2, "baseline replica count")
    require(deployment["strategy"]["rollingUpdate"]["maxUnavailable"] == 0, "rolling update availability")
    pod = deployment["template"]["spec"]
    container = pod["containers"][0]
    require(pod["terminationGracePeriodSeconds"] >= 20, "termination grace too short")
    require(container["imagePullPolicy"] == "Never", "local image should not pull")
    require(container["securityContext"]["readOnlyRootFilesystem"], "root filesystem must be read-only")
    require(container["resources"]["requests"].get("cpu"), "CPU request required for HPA")
    for probe, path in (("startupProbe", "/startupz"), ("livenessProbe", "/livez"), ("readinessProbe", "/readyz")):
        require(container[probe]["httpGet"]["path"] == path, f"{probe} path")
    require("krl-unready" in " ".join(container["lifecycle"]["preStop"]["exec"]["command"]), "drain hook")

    labels = deployment["template"]["metadata"]["labels"]
    require(all(labels.get(k) == v for k, v in by_kind["Service"]["spec"]["selector"].items()), "Service selector mismatch")
    require(all(labels.get(k) == v for k, v in by_kind["PodDisruptionBudget"]["spec"]["selector"]["matchLabels"].items()), "PDB selector mismatch")
    hpa = by_kind["HorizontalPodAutoscaler"]["spec"]
    require(hpa["scaleTargetRef"]["name"] == by_kind["Deployment"]["metadata"]["name"], "HPA target mismatch")
    require(hpa["minReplicas"] >= 2 and hpa["maxReplicas"] > hpa["minReplicas"], "HPA replica bounds")
    require(by_kind["NetworkPolicy"]["spec"]["podSelector"]["matchLabels"].items() <= labels.items(), "NetworkPolicy selector mismatch")

    load = yaml.safe_load((ROOT / "k8s" / "load-job.yaml").read_text())
    require(load["kind"] == "Job", "optional load generator is a Job")
    require(load["spec"]["template"]["metadata"]["labels"].get("resilience-lab-access") == "true", "load Job must be admitted by NetworkPolicy")
    print(f"OK: rendered {len(objects)} resources; probes, selectors, HPA, PDB, drain hook, and load access checked")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, subprocess.CalledProcessError, FileNotFoundError, yaml.YAMLError) as error:
        print(f"manifest check failed: {error}", file=sys.stderr)
        raise SystemExit(1)
