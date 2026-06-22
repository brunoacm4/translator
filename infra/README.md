# Infra - deployment & operations

Everything needed to build, deploy and smoke-test the translator. The translator runs as a
single container; the Slice Manager (SM) runs as a separate multi-container stack on its own
VM.

## Topology

Deployment is done from a workstation via **remote Docker contexts over SSH** - `docker`
builds and runs on the target VM, not locally.

| Docker context | SSH endpoint | Runs | Reachable at |
|----------------|--------------|------|--------------|
| `nef-translator-dev-0` | `ssh://atnoguser@10.16.255.53` | translator (this repo) | `http://10.16.255.53:8081` |
| `sandbox` | `ssh://atnoguser@10.16.255.55` | slice-manager-sandbox | `http://10.16.255.55:8000` |
| `default` | local docker | - | - |

List contexts with `docker context ls`. The translator's `SM_BASE_URL` in
[`docker-compose.yml`](docker-compose.yml) points at the sandbox VM (`http://10.16.255.55:8000`).

## Deploy the translator (VM 10.16.255.53)

```bash
cd translator-develop
docker context use nef-translator-dev-0
docker compose -f infra/docker-compose.yml up -d --build
```

Notes:
- The build context is the **repo root** (`bolsa_IT/`), selected via `context: ../..` in the
  compose file, because the shared root `.dockerignore` whitelists the worktree sources
  (`translator-develop/app`, `translator-develop/pyproject.toml`). Do not move the Dockerfile
  COPY paths without updating that whitelist.
- `container_name` is fixed (`nef-translator`). If a stale container blocks recreation:
  `docker rm -f nef-translator` then re-run `up`.
- Start from a clean local DB with `docker compose -f infra/docker-compose.yml down -v`
  (the `-v` wipes the `translator-data` SQLite volume).
- A `failed to prepare extraction snapshot ... parent snapshot does not exist` build error is
  a containerd/cache glitch on the VM, not a code problem. Recover with:
  `docker compose -f infra/docker-compose.yml build --no-cache` (then `up`); if it persists,
  `docker builder prune -af` first.

Health check:
```bash
curl -s http://10.16.255.53:8081/health | python3 -m json.tool
# expect: "status":"healthy", "sm_reachable":true, "circuit_breaker":{"state":"closed"}
```

## Deploy the Slice Manager sandbox (VM 10.16.255.55)

The SM lives in the read-only `slice-manager-sandbox` repo. It is a **no-op build**: it runs
the full pipeline (Control API → Kafka → core/RAN workers → gRPC sessions) but the session
gRPC servers are stubbed (writes acked, reads return canned demo data). Good for exercising
the translator without touching real hardware.

```bash
cd ../slice-manager-sandbox
docker context use sandbox
make down            # stop the previous stack
make build-all       # rebuild all images (VERSION 0.1.0, CAPIF disabled by default)
make up              # start: kafka, control-api (:8000), core/ran workers + sessions
docker ps
```

**Gotcha - empty `.env` files:** the sandbox `infra/docker-compose.yml` declares `env_file`
entries for the session modules, and those `.env` files are gitignored (absent on a fresh
clone), so `make up` fails with `env file .../modules/ran_sessions/.env not found`. Fix:

```bash
touch modules/control_api/.env modules/core_sessions/.env modules/core_worker/.env \
      modules/ran_sessions/.env modules/ran_worker/.env
```

These are local env setup (gitignored), not source edits to the SM.

## Smoke test (Postman / curl)

Base URL `http://10.16.255.53:8081`. Valid testbed UEs (see
[`app/config/subscriber_map.py`](../app/config/subscriber_map.py)): `10.0.0.1`, `10.0.0.2`.

```
POST   /3gpp-as-session-with-qos/v1/myApp/subscriptions      → 201 + Location
GET    /3gpp-as-session-with-qos/v1/myApp/subscriptions      → 200
GET    /3gpp-as-session-with-qos/v1/myApp/subscriptions/{id} → 200
PATCH  /3gpp-as-session-with-qos/v1/myApp/subscriptions/{id} → 200   {"qosReference":"qos_ref_2"}
DELETE /3gpp-as-session-with-qos/v1/myApp/subscriptions/{id} → 204
```

Create body:
```json
{
  "notificationDestination": "http://10.0.0.99/callback",
  "qosReference": "qos_ref_1",
  "ueIpv4Addr": "10.0.0.1",
  "dnn": "internet"
}
```

After the test, switch back to the local context so you don't run docker commands against a
VM by accident:
```bash
docker context use default
```

## Local development

Set up the project and run the tests without any VM - see the root [README](../README.md)
"Quick start (local)". The tests mock the SM client, so they need no running Slice Manager;
the full create flow needs the SM sandbox reachable (point `SM_BASE_URL` at
`http://10.16.255.55:8000`).
