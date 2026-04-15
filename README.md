# 3GPP AsSessionWithQoS Translator

Middleware that bridges the **OneSource NEF** (Network Exposure Function) and the **IT Aveiro Slice Manager (SM)**.

It exposes the exact 3GPP TS 29.122 `AsSessionWithQoS` northbound API and translates each subscription operation into one or more Slice Manager HTTP commands, which in turn drive real 5G network slicing on Huawei equipment at Porto de Aveiro.

---

## Architecture

```
OneSource NEF
     │
     │  3GPP TS 29.122
     │  POST /{scsAsId}/subscriptions
     ▼
┌────────────────────────────────────────────────────────────┐
│                      Translator                            │
│                                                            │
│  FastAPI app  ──►  translator_service.py                  │
│                         │                                  │
│                         ├── resolve_qos_profile()          │
│                         ├── resolve_imsi()                 │
│                         ├── parse_bitrate_to_kbps()        │
│                         └── sm_client.py (retry + CB)      │
└────────────────────────────────────────────────────────────┘
     │
     │  HTTP POST (plain dict payloads)
     │  SM_BASE_URL (default: localhost:8080)
     ▼
┌────────────────────────────────────────────────────────────┐
│               IT Aveiro Slice Manager                      │
│                                                            │
│  control-api (FastAPI)                                     │
│       │  Kafka topics                                      │
│       ▼                                                    │
│  core-worker ──► Selenium ──► Huawei 5G Equipment         │
└────────────────────────────────────────────────────────────┘
```

The translator **always talks to the SM via HTTP** (to the `control-api`). Kafka is the SM's internal implementation detail — bypassing it would skip validation, DB logging, OTel tracing, and idempotency handling.

---

## API

Base path: `/3gpp-as-session-with-qos/v1`

| Method | Path | SM operations |
|--------|------|---------------|
| `POST`   | `/{scsAsId}/subscriptions`                  | `create_slice` + `associate_slice` |
| `GET`    | `/{scsAsId}/subscriptions`                  | read from SQLite-backed store |
| `GET`    | `/{scsAsId}/subscriptions/{subscriptionId}` | read from SQLite-backed store |
| `PUT`    | `/{scsAsId}/subscriptions/{subscriptionId}` | `change_slice` |
| `PATCH`  | `/{scsAsId}/subscriptions/{subscriptionId}` | `change_slice` (only when QoS fields changed) |
| `DELETE` | `/{scsAsId}/subscriptions/{subscriptionId}` | `delete_slice` |

Additional:
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | SM reachability + circuit breaker state + subscription count |

Interactive docs: `http://localhost:8081/docs`

---

## Translation Logic

### 1. QoS Reference → SM Parameters

The NEF sends a `qosReference` string. The translator maps it to a `QoSProfile`:

| `qosReference` | Slice type | SST | Latency | Reliability |
|---------------|------------|-----|---------|-------------|
| `qos_ref_1` | eMBB  | 1 | 20 ms   | 99.9% |
| `qos_ref_2` | URLLC | 2 | 5 ms    | 99.999% |
| `qos_ref_3` | MIoT  | 3 | 100 ms  | 99.0% |

Edit [`app/config/qos_profiles.py`](app/config/qos_profiles.py) to add profiles or change values.

### 2. SNSSAI

Priority order:
1. Explicit `snssai` in the NEF request body (`snssai.sst` + `snssai.sd`)
2. QoS profile defaults (`sst` + `sd` from the matched profile)
3. Hardcoded fallback: `sst=1 sd=000001`

### 3. UE Identification (IP → IMSI)

The 3GPP spec identifies UEs by IP address (`ueIpv4Addr` / `ueIpv6Addr`), not by IMSI. The SM requires IMSI. The translator resolves this via a static map:

```python
# app/config/subscriber_map.py
IPV4_TO_IMSI = {
    "10.0.0.1": "268019012345678",
    "10.0.0.2": "268019012345679",
}
```

**Before testing with real UEs:** replace the placeholder IPs and IMSIs with actual testbed values.

### 4. BitRate Strings → KBPS

The 3GPP `tscQosReq` uses human-readable strings like `"10 Mbps"`. The SM expects integer KBPS. `parse_bitrate_to_kbps()` handles: `bps`, `Kbps`, `Mbps`, `Gbps`, `Tbps`.

### 5. Create + Associate Rollback

Creating a slice is a two-step SM operation:

```
create_slice  →  associate_slice
```

If `associate_slice` fails after `create_slice` succeeds, the translator automatically calls `delete_slice` to clean up the orphaned slice — preventing resource leaks in the SM.

---

## Resilience

### Circuit Breaker

Prevents cascading failures during SM downtime (e.g. maintenance at Porto de Aveiro).

```
State machine:
  CLOSED   → OPEN      : 5 consecutive SM failures
  OPEN     → HALF_OPEN : after 30s (controlled by CB_RECOVERY_TIMEOUT)
  HALF_OPEN → CLOSED   : probe call succeeds
  HALF_OPEN → OPEN     : probe call fails again
```

When OPEN, all SM calls return HTTP **503** immediately (no network I/O). The circuit state is visible in the `/health` response.

### Retry with Exponential Backoff

Transient failures (timeout, connection refused) are retried once before returning an error. Uses full-jitter backoff to avoid thundering-herd on simultaneous retries:

```
wait = random.uniform(0, min(MAX_WAIT, MIN_WAIT × 2^(attempt-1)))
```

HTTP 4xx/5xx responses from the SM are **not** retried — they represent logical errors that won't improve on retry.

### Correlation ID

Every request gets a UUID assigned at entry (or reads `X-Correlation-ID` from the caller). All log lines for that request carry the same `correlation_id` field, making Grafana/Loki queries trivial:

```
{job="translator"} | json | correlation_id="abc-123"
```

---

## Configuration

All settings are in [`app/config/settings.py`](app/config/settings.py) and can be overridden via environment variables or a `.env` file.

Copy `.env.example` to `.env` for local development:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `SM_BASE_URL` | `http://localhost:8080` | Slice Manager URL |
| `SM_DEFAULT_RAN` | unset | Optional RAN identifier included in `create_slice` payloads |
| `SM_TIMEOUT` | `30.0` | SM call timeout (s) — Selenium takes ~10–30s |
| `SM_HEALTH_TIMEOUT` | `5.0` | Health check timeout (s) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_JSON` | `true` | `false` for human-readable local output |
| `CB_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `CB_RECOVERY_TIMEOUT` | `30.0` | Seconds before recovery probe |
| `RETRY_MAX_ATTEMPTS` | `2` | Total attempts (1 = no retry) |
| `RETRY_MIN_WAIT` | `0.5` | Base backoff (s) |
| `RETRY_MAX_WAIT` | `3.0` | Max backoff (s) |

---

## Getting Started (Local Development)

### Prerequisites

- Python 3.10+
- The SM mock server (bundled at `mocks/sm_mock_server.py`)

### Setup

```bash
cd translator

# Create venv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Copy config
cp .env.example .env
# Edit .env: set LOG_JSON=false for readable output
```

### Run

```bash
# Terminal 1 — SM mock (simulates the real Slice Manager)
python -m uvicorn mocks.sm_mock_server:app --port 9090

# Terminal 2 — Translator
SM_BASE_URL=http://localhost:9090 LOG_JSON=false \
  python -m uvicorn app.main:app --port 8081 --reload
```

Open `http://localhost:8081/docs` for the interactive API.

### Quick Test

```bash
# Create a subscription
curl -s -X POST http://localhost:8081/3gpp-as-session-with-qos/v1/myApp/subscriptions \
  -H 'Content-Type: application/json' \
  -d '{
    "notificationDestination": "https://example.com/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.1"
  }' | python3 -m json.tool

# Health check (includes circuit breaker state)
curl -s http://localhost:8081/health | python3 -m json.tool

# Inspect what the SM mock received
curl -s http://localhost:9090/debug/slices | python3 -m json.tool
```

---

## Project Structure

```
translator/
├── app/
│   ├── main.py                     FastAPI app, lifespan, middleware wiring
│   ├── logging_config.py           JSON formatter for Loki ingestion
│   ├── apis/
│   │   ├── translator_api.py       6 CRUD route handlers
│   │   └── translator_api_base.py  Abstract base class (auto-registration)
│   ├── impl/
│   │   ├── translator_service.py   Core translation logic
│   │   └── sm_client.py            SM HTTP client (retry + circuit breaker)
│   ├── models/
│   │   └── nef/
│   │       ├── common.py           3GPP types: Snssai, TscQosRequirement, …
│   │       └── subscription.py     AsSessionWithQoSSubscription
│   ├── store/
│   │   └── subscription_store.py   SQLite-backed subscription CRUD store
│   ├── config/
│   │   ├── settings.py             Pydantic Settings (env vars / .env)
│   │   ├── qos_profiles.py         qosReference → SM parameter mapping
│   │   ├── subscriber_map.py       UE IP → IMSI static resolver
│   │   └── testbed_defaults.py     Fixed values for Porto de Aveiro
│   ├── middleware/
│   │   └── correlation_id.py       Request tracing middleware
│   ├── resilience/
│   │   ├── circuit_breaker.py      3-state async circuit breaker
│   │   └── retry.py                Exponential backoff with full jitter
│   └── utils/
│       └── converters.py           mbps_to_kbps, parse_bitrate_to_kbps
├── mocks/
│   └── sm_mock_server.py           Local SM mock for development
├── .env.example                    Configuration template
└── pyproject.toml                  Dependencies
```

---

## What's Next (Tier 2 — requires VPN to testbed)

1. **Test against real SM** — set `SM_BASE_URL` to the real SM IP and run the E2E test suite
2. **Update `subscriber_map.py`** — replace placeholder IPs with real testbed UE addresses and IMSIs
3. **Confirm `qosReference` values** — check what strings the OneSource NEF actually sends (may differ from `qos_ref_1/2/3`)
4. **Auth handling** — verify if the SM or NEF enforces OAuth2 bearer tokens

## What's Next (Tier 3 — before Porto de Aveiro production)

5. **OpenTelemetry integration** — plug into the SM's existing OTel Collector → Jaeger / Prometheus / Grafana pipeline
6. **Add to `docker-compose.yml`** — deploy as part of the full SM stack instead of running manually
7. **Persistent subscription store** — evaluate whether SQLite remains enough or if PostgreSQL is needed for shared/multi-instance deployment
8. **Idempotency** — extend the current deduplication layer with operational policies for replay windows and conflict reporting
9. **Notification callbacks** — forward `UserPlaneNotificationData` to `notificationDestination` when SM reports QoS events

---

## HTTP Status Codes

| Code | Meaning |
|------|---------|
| `201` | Subscription created |
| `200` | Read / update successful |
| `204` | Delete successful |
| `400` | Bad request (unknown `qosReference`, unknown UE IP, missing required field) |
| `404` | Subscription not found |
| `422` | Pydantic validation error (malformed JSON body) |
| `502` | SM returned an error or the network call failed after retries |
| `503` | Circuit breaker is OPEN — SM is currently considered down |
