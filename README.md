# 3GPP AsSessionWithQoS Translator

Middleware that bridges the **OneSource NEF** (Network Exposure Function) and the
**IT Aveiro Slice Manager (SM)**.

It exposes the 3GPP TS 29.122 `AsSessionWithQoS` northbound API and translates each
subscription operation into one or more Slice Manager HTTP calls, which drive 5G network
slicing at the Porto de Aveiro testbed. It keeps local state (SQLite), enforces idempotency,
and tracks slice lifecycle (dedup + reference counting).

> **Documentation map**
> - This README, overview, API, quick start, configuration.
> - [`CONSIDERATIONS.md`](CONSIDERATIONS.md), architecture and design decisions (the *why*).
> - [`TODO.md`](TODO.md), roadmap, known limitations, and the handoff checklist.
> - [`infra/README.md`](infra/README.md), build, deploy to the VMs, and smoke test.
> - [`schemas/`](schemas/), the OpenAPI contract of the northbound API.

---

## Architecture

```text
OneSource NEF ──3GPP TS 29.122──► Translator ──HTTP──► Slice Manager control-api
                                  (this repo)          (Kafka → core/RAN workers → 5G core)
```

The translator **always talks to the SM over HTTP** (the `control-api`). Kafka is the SM's
internal detail; the SM accepts each write with `202 + request_id` and processes it
asynchronously. See [`CONSIDERATIONS.md`](CONSIDERATIONS.md) for the full picture.

---

## API

Base path: `/3gpp-as-session-with-qos/v1` · Interactive docs: `http://<host>:8081/docs`

| Method | Path | Slice Manager operations |
|--------|------|--------------------------|
| `POST`   | `/{scsAsId}/subscriptions`                  | `create_slice` (if new SNSSAI+DNN) + `associate_slice` |
| `GET`    | `/{scsAsId}/subscriptions`                  | read from local store |
| `GET`    | `/{scsAsId}/subscriptions/{subscriptionId}` | read from local store |
| `PUT`    | `/{scsAsId}/subscriptions/{subscriptionId}` | `change_slice` |
| `PATCH`  | `/{scsAsId}/subscriptions/{subscriptionId}` | `change_slice` (only when QoS fields changed) |
| `DELETE` | `/{scsAsId}/subscriptions/{subscriptionId}` | `dissociate_slice` + `delete_slice` (when ref_count=0); idempotent on SM 404 |
| `GET`    | `/operations/{operationId}`                 | read operation status from local store |
| `GET`    | `/health` | SM reachability + circuit-breaker state + subscription count |

---

## Quick start (local)

Set up the project and run the tests locally. The full create flow needs a reachable Slice
Manager -  point `SM_BASE_URL` at the sandbox VM (see [`infra/README.md`](infra/README.md)).

**Prerequisites:** Python **3.11** (`pyproject.toml` requires `>=3.11`; the committed
`.venv` is 3.10 and should be recreated).

```bash
cd translator

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env          # then set LOG_JSON=false for readable logs
```

```bash
# Tests need no Slice Manager (the SM client is mocked in tests)
python -m pytest -q          # 16 tests
```

```bash
# Run the app against the SM sandbox (requires VPN/route to the testbed)
SM_BASE_URL=http://10.16.255.55:8000 LOG_JSON=false \
  python -m uvicorn app.main:app --port 8081 --reload
```

```bash
# Create a subscription (10.0.0.1 is a mapped testbed UE)
curl -s -X POST http://localhost:8081/3gpp-as-session-with-qos/v1/myApp/subscriptions \
  -H 'Content-Type: application/json' \
  -d '{"notificationDestination":"http://example/cb","qosReference":"qos_ref_1","ueIpv4Addr":"10.0.0.1","dnn":"internet"}' \
  | python3 -m json.tool

curl -s http://localhost:8081/health | python3 -m json.tool
```

Deploying to the testbed VMs (remote Docker contexts) is documented in
[`infra/README.md`](infra/README.md).

---

## Configuration

All settings live in [`app/config/settings.py`](app/config/settings.py) and are overridable
via environment variables or a `.env` file (`cp .env.example .env`).

| Variable | Default | Description |
|----------|---------|-------------|
| `SM_BASE_URL` | `http://localhost:8080` | Slice Manager control-api URL (testbed sandbox: `http://10.16.255.55:8000`) |
| `SM_DEFAULT_COVERAGE_AREA` | unset | JSON array passed to `create_slice`, e.g. `["it"]` (lowercase -  schema-validated) |
| `SM_DEFAULT_RAN` | unset | Optional `ran` identifier for `create_slice` |
| `SM_TIMEOUT` | `30.0` | SM call timeout (s) |
| `SM_HEALTH_TIMEOUT` | `5.0` | Health-check SM reachability timeout (s) |
| `SM_POLLING_ENABLED` | `false` | Async polling of SM `GET /operations/{id}`. Off by default -  the SM does not implement that endpoint yet (returns 500); the synchronous `202` is treated as terminal. See [`CONSIDERATIONS.md`](CONSIDERATIONS.md). |
| `SM_POLL_INITIAL_INTERVAL` / `SM_POLL_MAX_INTERVAL` / `SM_POLL_TIMEOUT` | `2.0` / `30.0` / `300.0` | Polling backoff + deadline (only used when polling is enabled) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_JSON` | `true` | `false` for human-readable local output |
| `CB_FAILURE_THRESHOLD` | `5` | Failures before the circuit breaker opens |
| `CB_RECOVERY_TIMEOUT` | `30.0` | Seconds before the breaker probes the SM again |
| `RETRY_MAX_ATTEMPTS` / `RETRY_MIN_WAIT` / `RETRY_MAX_WAIT` | `2` / `0.5` / `3.0` | SM call retry policy (full-jitter backoff) |
| `TRANSLATOR_DB_PATH` | `./translator.db` | SQLite state file |

---

## Project structure

```text
translator/
├── app/
│   ├── main.py              FastAPI app, lifespan, middleware wiring
│   ├── logging_config.py    JSON formatter for Loki ingestion
│   ├── apis/                3GPP route handlers + auto-registering base class
│   ├── impl/                translation logic: translator_service, sm_client, sm_poller
│   ├── models/nef/          3GPP types (Snssai, TscQosRequirement, Subscription)
│   ├── config/              settings, qos_profiles, subscriber_map, testbed_defaults
│   ├── db/ · store/         SQLite connection/schema + repositories (DAOs)
│   ├── resilience/          circuit breaker + retry-with-backoff
│   ├── middleware/          correlation-id request tracing
│   └── utils/               bitrate converters, idempotency fingerprint
├── infra/                   docker-compose + deploy runbook (see infra/README.md)
├── schemas/                 northbound OpenAPI contract
├── tests/                   unit + integration (pytest)
├── Dockerfile · pyproject.toml
├── README.md · CONSIDERATIONS.md · TODO.md
└── .env.example
```

---

## HTTP status codes

| Code | Meaning |
|------|---------|
| `201` | Subscription created |
| `200` | Read / update successful |
| `204` | Delete successful |
| `400` | Bad request (unknown `qosReference`, unmapped UE IP, missing required field) |
| `404` | Subscription not found |
| `422` | Validation error (malformed body) |
| `502` | SM returned an error or the network call failed after retries |
| `503` | Circuit breaker OPEN — SM considered down |
