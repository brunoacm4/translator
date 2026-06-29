# Translator -  design considerations

The *why* behind the translator's architecture and behaviour. For the API and how to run it,
see [`README.md`](README.md); for deployment, [`infra/README.md`](infra/README.md); for
open work, [`TODO.md`](TODO.md).

---

## 1. Purpose and role

The translator is a middleware between a 3GPP **NEF** and the IT Aveiro **Slice Manager (SM)**.
Northbound it speaks the standardised 3GPP TS 29.122 `AsSessionWithQoS` API; southbound it
speaks the SM's proprietary REST contract. It owns the impedance-matching between the two:
identifier resolution, QoS mapping, payload construction, idempotency, slice lifecycle, and
local state.

The SM is **asynchronous and event-driven**: a write (`create_slice`, `associate_slice`, …)
returns `202 + request_id` immediately, publishes a Protobuf message to **Kafka**, and a
`core-worker` consumes it and provisions the network, recording progress in a `requests`
table. The translator was built to follow that contract end to end (see §4.7 on polling).

---

## 2. Layered architecture

| Layer | Location | Responsibility |
|-------|----------|----------------|
| API / routes | `app/apis/` | 3GPP endpoints, input validation, auto-discovery of the implementation |
| Orchestration | `app/impl/translator_service.py` | Translation logic: resolve data, build payloads, coordinate SM calls, idempotency, rollback |
| SM client | `app/impl/sm_client.py` | Async HTTP client (shared connection pool, retry, circuit breaker) |
| Async polling | `app/impl/sm_poller.py` | Background task tracking SM operations to a terminal state (dormant by default - §4.7) |
| Models | `app/models/` | Pydantic 3GPP types + the local operation model |
| Configuration | `app/config/` | Settings, QoS profiles, IMSI map, testbed defaults |
| Persistence | `app/db/`, `app/store/` | SQLite: subscriptions, operations, idempotency, slice registry |
| Resilience | `app/resilience/` | Retry-with-backoff and circuit breaker |
| Observability | `app/middleware/`, `app/logging_config.py` | Correlation-ID propagation, structured JSON logs |

Routes are decoupled from the implementation by **auto-registration**: any `BaseTranslatorApi`
subclass registers itself on import (`pkgutil`/`importlib`), so the router resolves the
implementation without a hard import.

---

## 3. Create flow (the richest path)

`POST /subscriptions` exercises every decision below.

```text
1. Idempotency    fingerprint = SHA-256(scsAsId + body); reserve it.
                  Already seen → return 202 with the original operation.
2. QoS profile    resolve qosReference → SM parameters (SST/SD/latency/…).
3. IMSI           resolve from the UE IP (subscriber_map).
4. SNSSAI + DNN   body > QoS profile > testbed defaults.
5. Slice ID       deterministic: s{sst}d{sd}-{dnn}.
6. Slice registry get_or_create under a per-(snssai,dnn) lock → new or reuse.
7. SM create      only when the slice is new (409 "exists" → reuse).
8. SM associate   associate the UE; ROLLBACK the create on failure.
9. Persist        store the subscription; ref_count++.
10. Finalise      mark the operation terminal (or start polling -  §4.7).
11. Respond       201 + the full resource + Location header.
```

```mermaid
sequenceDiagram
    participant NEF
    participant T as Translator
    participant DB as SQLite
    participant SM as Slice Manager
    NEF->>T: POST /subscriptions (AsSessionWithQoS)
    T->>DB: reserve idempotency + create operation (pending)
    T->>T: resolve QoS / IMSI / SNSSAI / DNN / slice_id
    T->>DB: get_or_create slice (registry)
    alt new slice
        T->>SM: POST /core/slices (create_slice)
        SM-->>T: 202 + request_id
    end
    T->>SM: POST /core/ues/{imsi}/slice-associations (associate)
    SM-->>T: 202 + request_id
    T->>DB: store subscription; mark operation completed
    T-->>NEF: 201 Created (+ Location)
```

---

## 4. Design decisions

### 4.1 Idempotency
A repeated `POST` (same `Idempotency-Key` **or** same payload fingerprint) does not create
duplicate resources, it returns the original operation with `202`. Protects against client
and network retries. *(`app/utils/idempotency.py`, table `idempotency_keys`.)*

### 4.2 Slice registry with deduplication and reference counting
Several UEs can share one logical slice (same `snssai`+`dnn`). The `slice_registry` table
tracks a reference count: the slice is created in the SM only on the first subscription and is
deleted only when the last subscription using it is removed. A per-`(snssai,dnn)`
`asyncio.Lock` prevents concurrent-create races. *(`app/store/repositories.py`.)*

### 4.3 Deterministic slice IDs
Slice IDs are derived as **`s{sst}d{sd}-{dnn}`** (e.g. `s1d000001-internet`). The SM uses this
as the slice name in hardware, so it must be stable across calls for the same logical slice.

### 4.4 QoS profiles - SM-owned catalog
`qosReference` strings currently map to SM parameters (SST, SD, latency, reliability,
priority, mobility) in [`app/config/qos_profiles.py`](app/config/qos_profiles.py):
`qos_ref_1` (eMBB), `qos_ref_2` (URLLC), `qos_ref_3` (MIoT).

**Decision (SM team, 2026-06-27):** the Slice Manager will own a fixed set of QoS profiles;
the NEF and other external entities select from that pre-defined subset (no free-form
dynamism). The SM will expose an endpoint listing the available profiles so the translator
can discover them. This is **new work on both sides** and does not exist yet.

Why SM-owned makes sense: the 3GPP `tscQosReq` only carries 6 fields (GBR/MBR DL+UL, 5GS
delay budget, priority), while the SM `Slice` model accepts ~30 (reliability, mobility,
delay_tolerance, deterministic comm, scheduling, resource-block ratios, packet sizes,
per-slice bitrates, terminal density, session/UE caps). Most QoS knobs have no 3GPP source,
so a shared catalog owned by the SM is cleaner than the translator inventing values.

Until that endpoint exists, the local `qos_profiles.py` dict is a **temporary placeholder**
with test values. Note `default_5qi` in those profiles is currently dead code (never sent;
the SM `create_slice` has no 5QI field).

### 4.5 SQLite persistence
The translator keeps its own state (subscriptions, operations, idempotency, slice registry).
This serves reads without hitting the SM, survives restarts, and tracks async operations. A
single embedded file, no external dependency, right for a testbed MVP. *(`app/db/schema.py`.)*

### 4.6 Resilience -  retry + circuit breaker
Every SM call is wrapped with **retry + exponential backoff with full jitter** (transient
network errors) and a **circuit breaker** that, after `CB_FAILURE_THRESHOLD` consecutive
failures, opens and fails fast with `503` instead of piling up timeouts. The breaker state is
exposed in `/health`. *(`app/resilience/`.)*

### 4.7 Async polling -  disabled by default
After a write, the SM returns `202 + state="published"` (accepted/queued), not "provisioned".
The translator can poll `GET /operations/{request_id}` until terminal and then fire a NEF
callback. **But the SM does not implement that endpoint**, it returns `500 "Not implemented"`
(no `BaseOPSApi` subclass), in both the sandbox and the upstream SM. So polling is gated
behind `SM_POLLING_ENABLED` (**default `false`**): the synchronous `202` is treated as the
terminal result and the operation is marked `completed` immediately. The polling machinery
(`sm_poller.py`, startup resume, `sm_client.get_request_status`) is kept intact and dormant;
flip the flag to re-enable it once the SM exposes the endpoint. *(See [`TODO.md`](TODO.md).)*

### 4.8 Idempotent DELETE (404 tolerance)
The SM's `delete_slice` is the only write that consults the read model, it does a gRPC
`GetSlice` first and returns **404** when the slice is unknown. Against the no-op sandbox
(whose read side only has canned demo data) that 404 is expected for any real slice. The
translator therefore treats a 404 on `delete_slice` / `dissociate_slice` as **success**:
deleting an already-absent resource is the desired end state. This is correct idempotent-DELETE
semantics and works against the real SM too. *(`sm_client._request(tolerate_not_found=True)`.)*

### 4.9 Create + associate rollback
Creating a slice is two SM steps. If `associate_slice` fails after `create_slice` succeeded
**and** the slice was created in this operation, the translator calls `delete_slice` to remove
the orphan and reverts the local registry - no leaked resources. A `409 Conflict` on create
(slice already exists) is treated as reuse, not an error.

### 4.10 Observability
A correlation-ID middleware tags every log line for a request; logs are structured JSON
(configurable). `/health` reports SM reachability, breaker state, and subscription count for
dashboards.

---

## 5. Operation state machine

The translator tracks its **own** operation lifecycle (distinct from the SM's internal one):

```text
pending ──► published ──► completed            (polling disabled, default)
                      └──► sm_provisioning ──► completed / failed   (polling enabled)
   └────────────────────────────────────────► failed   (on any error)
```

- **pending** -  created locally, before calling the SM.
- **published** -  slice ID determined and registered.
- **completed / failed** -  terminal. With polling off, `completed` is set as soon as the SM
  accepts the writes; with polling on, the poller sets it from the SM's terminal state.

---

## 6. Data model (SQLite)

| Table | Key | Purpose |
|-------|-----|---------|
| `subscriptions` | (`scs_as_id`, `subscription_id`) | The 3GPP resource + link to slice & IMSI |
| `operations` | `operation_id` | Per-operation async tracking and status |
| `idempotency_keys` | (`scs_as_id`, `payload_fingerprint`) | Deduplication of repeated requests |
| `slice_registry` | (`snssai`, `dnn`) | Slice dedup + reference count |

Online, safe migrations: `init_db` adds missing columns (`sm_request_id`, `notification_url`)
without breaking existing databases.

---

## 7. Known limitations

- **SM `/operations/{request_id}` is unimplemented** (500) -  async completion cannot be
  confirmed; polling stays disabled. The state *is* persisted in the SM's `requests` table;
  only the HTTP read-back is missing.
- **No-op sandbox** -  the SM sandbox stubs the session gRPC servers (writes acked, reads
  return canned demo data: a `sandbox-demo` slice, cells 151/152/153). Full E2E semantics need
  a non-no-op SM. This is why `delete_slice` 404s (§4.8).
- **Local `.venv` is Python 3.10** while `pyproject.toml` requires `>=3.11` (the Docker image
  is 3.11). Recreate the venv on 3.11 for local work.

---

## 8. Open questions for the teams

1. **QoS profile ownership** -  the SM owns the catalog and will expose
   a discovery endpoint; the NEF selects from that subset (§4.4). Pending translator work:
   fetch the available profiles from that endpoint instead of the local placeholder dict.
2. **Polling re-enable** -  once the SM ships `/operations`, flip `SM_POLLING_ENABLED` and
   decide the NEF callback contract (`notificationDestination`).
3. **Persistence at scale** -  keep SQLite or move to PostgreSQL for multi-instance deployment.
