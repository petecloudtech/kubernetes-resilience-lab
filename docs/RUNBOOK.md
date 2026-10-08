# Local reliability runbook

These exercises use the `resilience-lab` namespace and the synthetic service in this repository. They are for local practice only. Confirm you are using a disposable cluster before deleting Jobs, Pods, or a namespace.

## First checks

```sh
kubectl config current-context
kubectl -n resilience-lab get deployment,pods,service,hpa,pdb
kubectl -n resilience-lab rollout status deployment/resilience-lab --timeout=60s
kubectl -n resilience-lab describe deployment resilience-lab
kubectl -n resilience-lab get events --sort-by=.lastTimestamp
```

If Pods stay Pending, inspect scheduling events and node capacity. If Pods show `ImagePullBackOff` or `ErrImageNeverPull`, rebuild `resilience-lab:local`, load it into the current kind cluster, and restart the rollout. If `/readyz` is 503, inspect the drain marker and startup delay.

## Drill 1: Pod becomes unready

Pick a current Pod:

```sh
kubectl -n resilience-lab get pods -l app.kubernetes.io/name=resilience-lab
```

Substitute its name for `<pod>`:

```sh
kubectl -n resilience-lab exec <pod> -- touch /tmp/krl-unready
kubectl -n resilience-lab get pods -w
kubectl -n resilience-lab get endpointslice -l kubernetes.io/service-name=resilience-lab
```

Expected: that Pod's readiness probe returns 503 and its endpoint stops receiving new Service traffic after endpoint propagation. Liveness remains 200, so the Pod should not restart merely because it is unready. Recover with:

```sh
kubectl -n resilience-lab exec <pod> -- rm /tmp/krl-unready
kubectl -n resilience-lab rollout status deployment/resilience-lab --timeout=60s
```

## Drill 2: Application 503 without a pod restart

Start `kubectl -n resilience-lab port-forward service/resilience-lab 8080:80` in one terminal. In another:

```sh
curl -i 'http://127.0.0.1:8080/work?fail=1&iterations=1000'
curl http://127.0.0.1:8080/metrics
curl -i http://127.0.0.1:8080/readyz
```

Expected: the request receives a synthetic 503; `krl_work_requests_total{code="503"}` increases; readiness stays 200. This demonstrates why an application failure counter and a liveness signal answer different questions. Re-running `/work` without `fail=1` recovers immediately.

## Drill 3: Rolling update and rollback

```sh
kubectl -n resilience-lab set env deployment/resilience-lab STARTUP_DELAY_SECONDS=5
kubectl -n resilience-lab rollout status deployment/resilience-lab --timeout=180s
kubectl -n resilience-lab get pods -w
```

Inspect the rollout if it stalls:

```sh
kubectl -n resilience-lab describe deployment resilience-lab
kubectl -n resilience-lab describe pod <new-pod>
kubectl -n resilience-lab logs <new-pod>
```

Roll back the practice change:

```sh
kubectl -n resilience-lab rollout undo deployment/resilience-lab
kubectl -n resilience-lab rollout status deployment/resilience-lab --timeout=180s
```

## Drill 4: CPU HPA

First check `kubectl top pods -n resilience-lab`. If metrics are unavailable, install or repair the cluster's resource metrics pipeline before interpreting HPA status.

```sh
kubectl -n resilience-lab apply -f k8s/load-job.yaml
kubectl -n resilience-lab logs -f job/resilience-lab-load
kubectl -n resilience-lab describe hpa resilience-lab
kubectl -n resilience-lab get hpa -w
```

The optional Job sends bounded CPU work to the Service from a labeled Pod allowed by the NetworkPolicy. Scaling is not deterministic on small local clusters. If utilization stays `<unknown>`, the issue is metrics collection, not an application SLO failure.

## Cleanup

```sh
kubectl delete -k k8s
kind delete cluster --name resilience-lab
```

The `kubectl delete -k` command deletes only objects in this lab's Kustomization, including its namespace. Use it only against the intended disposable context.
