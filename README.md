# 3GPP AsSessionWithQoS Translator

Middleware that bridges the **OneSource NEF** (Network Exposure Function) and the
**IT Aveiro Slice Manager (SM)**.

It exposes two northbound APIs over one translation core, and translates each operation into
one or more Slice Manager HTTP calls, which drive 5G network slicing at the Porto de Aveiro
testbed:

- **TS 29.514 `Npcf_PolicyAuthorization` (N5)**: the NEF calls the translator as its PCF.
  This is how the OneSource NEF reaches the core ([ADR 0001](docs/adr/0001-n5-interface-in-same-service.md)).
- **TS 29.122 `AsSessionWithQoS`**: the NEF-facing API an AF uses; kept for direct tests.

The Slice Manager pre-creates one slice per QoS profile (model B,
[ADR 0004](docs/adr/0004-slice-per-profile-working-assumption.md)). The translator never
creates or deletes slices: it associates each UE with the slice its `qosReference` selects,
moves it to another slice when the QoS changes, and dissociates it at the end. It keeps local
state (SQLite), enforces idempotency (29.122) and allows one QoS session per UE.

> **Documentation map**
> - This README, overview, API, quick start, configuration.
> - [`CONSIDERATIONS.md`](CONSIDERATIONS.md), architecture and design decisions (the *why*).
> - [`TODO.md`](TODO.md), roadmap, known limitations, and the handoff checklist.
> - [`infra/README.md`](infra/README.md), build, deploy to the VMs, and smoke test.
> - [`schemas/`](schemas/), the OpenAPI contract of the northbound API.

---

## Architecture

```text
OneSource NEF ──N5, TS 29.514 (HTTP/2 h2c)──► Translator ──HTTP──► Slice Manager control-api
AF / tests ─────TS 29.122─────────────────►  (this repo)          (Kafka → core/RAN workers → 5G core)
```

Both APIs map their requests onto one interface-neutral core (`app/impl/session_core.py`),
which owns every SM call. Design decisions are recorded in [`docs/adr/`](docs/adr/).

The translator **always talks to the SM over HTTP** (the `control-api`). Kafka is the SM's
internal detail; the SM accepts each write with `202 + request_id` and processes it
asynchronously, and the translator follows each request through the SM's
`GET /operations/{request_id}` and notifies the application with a 3GPP
`UserPlaneNotificationData`. See [`CONSIDERATIONS.md`](CONSIDERATIONS.md) for the full
picture.

> **SM compatibility:** targets the Slice Manager `main` as of Nov 2026 (commit `bfbaf29`):
> `/operations` is implemented and the UE move answers `202` + `request_id`. Against an older
> SM build (e.g. the June 2026 sandbox) set `SM_POLLING_ENABLED=false`. That SM now starts
> with CAPIF on by default, and the translator does not send CAPIF tokens yet (see
> [`TODO.md`](TODO.md)).

### QoS profiles

`app/config/qos_profiles.py` maps each `qosReference` to the pre-provisioned SM slice that
implements it: `slice_id`, S-NSSAI and DNN ([ADR 0005](docs/adr/0005-qos-profile-catalog-under-model-b.md)).
The entries `qos_ref_1/2/3` → `placeholder-slice-1/2/3` (S-NSSAI `1-000001`, DNN `internet`)
are **placeholders** until the SM team sends its slice list.

How requests are handled under model B ([ADR 0006](docs/adr/0006-model-b-request-handling.md)):

- A `qosReference` is required. A missing or unknown one is rejected.
- An S-NSSAI or DNN in the request must match the profile's slice, or the request is
  rejected. This rule is provisional.
- One session per UE, across both APIs: a create for a UE that already has a session is
  rejected with `403`.
- A QoS change that selects another slice moves the UE (`change_slice`), and the move is
  tracked like a create. If it fails, the session goes back to its previous slice, and the
  application is notified when it subscribed to the resource-allocation events. A change that
  keeps the slice makes no SM call.
- Per-request bitrates and delays (29.122 `tscQosReq`, N5 `marBw*`/`mirBw*`) are parsed but not
  applied yet: decision D3 of the architecture review.

---

## API

Interactive docs: `http://<host>:8081/docs`. One port (8081) serves HTTP/1.1 and HTTP/2
cleartext ([ADR 0002](docs/adr/0002-http2-cleartext-with-hypercorn.md)).

### N5 — TS 29.514 `Npcf_PolicyAuthorization`

Base path: `/npcf-policyauthorization/v1` · minimal scope ([ADR 0003](docs/adr/0003-minimal-n5-scope.md))

| Method | Path | Slice Manager operations |
|--------|------|--------------------------|
| `POST`  | `/app-sessions`                        | `associate_slice`; `201` + absolute `Location` |
| `PATCH` | `/app-sessions/{appSessionId}`         | `change_slice` (only when `medComponents` select another slice); `200` + the context |
| `POST`  | `/app-sessions/{appSessionId}/delete`  | `dissociate_slice`; `204` |

Mapping:

- `ascReqData.ueIpv4`/`ueIpv6` → IMSI (subscriber map).
- `medComponents[].qosReference` → QoS profile, and so the slice. One per session: different
  references are rejected.
- `sliceInfo` and `dnn` are checked against the profile's slice.

Notifications follow the Open5GS PCF the NEF was built against
([ADR 0007](docs/adr/0007-n5-behaves-like-open5gs-pcf.md)):

- When the SM reports that a create failed, the translator POSTs a `TerminationInfo` to
  `{ascReqData.notifUri}/terminate`, then forgets the session.
- Nothing else is sent: no `EventsNotification`, and no notification after a success or a
  QoS change.

Other methods (e.g. `GET`) answer `405`.

### TS 29.122 `AsSessionWithQoS`

Base path: `/3gpp-as-session-with-qos/v1`

| Method | Path | Slice Manager operations |
|--------|------|--------------------------|
| `POST`   | `/{scsAsId}/subscriptions`                  | `associate_slice` |
| `GET`    | `/{scsAsId}/subscriptions`                  | read from local store |
| `GET`    | `/{scsAsId}/subscriptions/{subscriptionId}` | read from local store |
| `PUT`    | `/{scsAsId}/subscriptions/{subscriptionId}` | `change_slice` (only when the profile selects another slice) |
| `PATCH`  | `/{scsAsId}/subscriptions/{subscriptionId}` | `change_slice` (only when QoS fields changed and select another slice) |
| `DELETE` | `/{scsAsId}/subscriptions/{subscriptionId}` | `dissociate_slice`; idempotent on SM 404 |
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
python -m pytest -q          # 100 tests
```

```bash
# Run the app against the SM sandbox (requires VPN/route to the testbed)
SM_BASE_URL=http://10.16.255.55:8000 LOG_JSON=false \
  python -m hypercorn app.main:app --bind 127.0.0.1:8081 --reload
```

```bash
# N5 create, over HTTP/2 cleartext as the NEF sends it (10.0.0.1 is a mapped testbed UE)
curl -s --http2-prior-knowledge -i -X POST http://localhost:8081/npcf-policyauthorization/v1/app-sessions \
  -H 'Content-Type: application/json' \
  -d '{"ascReqData":{"notifUri":"http://example/pa","suppFeat":"0","ueIpv4":"10.0.0.1","dnn":"internet",
       "medComponents":{"1":{"medCompN":1,"qosReference":"qos_ref_1"}}}}'
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
| `SM_TIMEOUT` | `15.0` | SM call timeout (s); keeps answers under the NEF's 25 s limit ([`CONSIDERATIONS.md`](CONSIDERATIONS.md) §4.6) |
| `SM_HEALTH_TIMEOUT` | `5.0` | Health-check SM reachability timeout (s) |
| `SM_POLLING_ENABLED` | `true` | Async polling of SM `GET /operations/{id}` until `completed`/`failed`, then NEF callback. Set `false` against an SM older than Sept 2026 (endpoint returns 500); the synchronous `202` is then treated as terminal. See [`CONSIDERATIONS.md`](CONSIDERATIONS.md) §4.7. |
| `SM_POLL_INITIAL_INTERVAL` / `SM_POLL_MAX_INTERVAL` / `SM_POLL_TIMEOUT` | `2.0` / `30.0` / `300.0` | Polling backoff + deadline (only used when polling is enabled) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_JSON` | `true` | `false` for human-readable local output |
| `CB_FAILURE_THRESHOLD` | `5` | Consecutive SM failures (network errors, timeouts, `5xx`; not `4xx`) before the circuit breaker opens |
| `CB_RECOVERY_TIMEOUT` | `30.0` | Seconds before the breaker probes the SM again |
| `RETRY_MAX_ATTEMPTS` / `RETRY_MIN_WAIT` / `RETRY_MAX_WAIT` | `1` / `0.5` / `3.0` | SM call retry policy (full-jitter backoff); `1` = no retry ([`CONSIDERATIONS.md`](CONSIDERATIONS.md) §4.6) |
| `TRANSLATOR_DB_PATH` | `./translator.db` | SQLite state file |

---

## Project structure

```text
translator/
├── app/
│   ├── main.py              FastAPI app, lifespan, middleware wiring
│   ├── logging_config.py    JSON formatter for Loki ingestion
│   ├── apis/                3GPP route handlers (29.122, N5) + ProblemDetails errors
│   ├── impl/                session_core (shared core), translator_service (29.122),
│   │                        policy_authorization_service (N5), sm_client, sm_poller, notifier
│   ├── models/nef/          TS 29.122 types (Snssai, TscQosRequirement, Subscription)
│   ├── models/pcf/          TS 29.514 types (AppSessionContext, MediaComponent, TerminationInfo)
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
| `400` | 29.122: malformed body (listed in `invalidParams`), missing or unknown `qosReference`, `snssai`/`dnn` not matching the profile's slice, unmapped UE IP, a PUT that changes the UE, a PATCH that leaves the subscription invalid |
| `403` | The UE already has a QoS session (both APIs; 29.122 may move to `409`, ADR 0006). N5 only: missing, unknown or conflicting `qosReference`, or `sliceInfo`/`dnn` not matching the profile's slice (as the Open5GS PCF answers) |
| `404` | Subscription / application session not found; N5 only: unmapped UE address |
| `405` | N5 only: method outside the minimal scope (e.g. `GET /app-sessions/{id}`) |
| `502` | SM returned an error or the network call failed after retries |
| `503` | Circuit breaker OPEN — SM considered down |

Errors are 3GPP `ProblemDetails` (`application/problem+json`) with `title`, `status`,
`detail` and, for malformed requests, `invalidParams`.
