# Translator -  design considerations

The *why* behind the translator's architecture and behaviour. For the API and how to run it,
see [`README.md`](README.md); for deployment, [`infra/README.md`](infra/README.md); for
open work, [`TODO.md`](TODO.md).

---

## 1. Purpose and role

The translator is a middleware between a 3GPP **NEF** and the IT Aveiro **Slice Manager (SM)**.
Northbound it speaks two standardised 3GPP APIs over one translation core
([ADR 0001](docs/adr/0001-n5-interface-in-same-service.md)):

- TS 29.514 `Npcf_PolicyAuthorization` (N5): the OneSource NEF calls the translator as its PCF;
- TS 29.122 `AsSessionWithQoS`: kept for direct tests.

Southbound it speaks the SM's proprietary REST contract. It owns the impedance-matching
between the two: identifier resolution, QoS profile selection, payload construction,
idempotency, the one-session-per-UE rule, and local state.

**Model B** ([ADR 0004](docs/adr/0004-slice-per-profile-working-assumption.md), confirmed
2026-11-20): the SM pre-creates one slice per QoS profile. The translator never creates or
deletes slices. It associates a UE with the slice its `qosReference` selects, moves it to
another slice on a QoS change, and dissociates it at the end.

The SM is **asynchronous and event-driven**: a write (`associate_slice`, `change_slice`, …)
returns `202 + request_id` immediately, publishes a Protobuf message to **Kafka**, and the
`core-worker` consumes it and provisions the network, recording per-worker progress in a
Postgres `requests` table exposed at `GET /operations/{request_id}`. The translator follows
that contract end to end (§4.7).

**Target SM version:** `slice-manager` `main` as of 2026-11-04 (`bfbaf29`). The SM sandbox
fork was last updated 2026-06-16 and predates `/operations` (§4.7).

---

## 2. Layered architecture

| Layer | Location | Responsibility |
|-------|----------|----------------|
| API / routes | `app/apis/` | 3GPP endpoints, input validation, auto-discovery of the implementation |
| Northbound adapters | `app/impl/translator_service.py` (29.122), `app/impl/policy_authorization_service.py` (N5) | Map each API onto the core: resolve data, idempotency (29.122), one session per UE, store |
| Translation core | `app/impl/session_core.py` | Profile → slice resolution, SM payloads, every SM call, operation tracking |
| SM client | `app/impl/sm_client.py` | Async HTTP client (shared connection pool, retry, circuit breaker) |
| Async polling | `app/impl/sm_poller.py` | Background task tracking SM operations to a terminal state; reverts a failed QoS change (§4.7, §4.9) |
| Models | `app/models/` | Pydantic 3GPP types + the local operation model |
| Configuration | `app/config/` | Settings, QoS profiles, IMSI map, testbed defaults |
| Persistence | `app/db/`, `app/store/` | SQLite: sessions (subscriptions table), operations, idempotency |
| Resilience | `app/resilience/` | Retry-with-backoff and circuit breaker |
| Observability | `app/middleware/`, `app/logging_config.py` | Correlation-ID propagation, structured JSON logs |

Routes are decoupled from the implementation by **auto-registration**: any `BaseTranslatorApi`
subclass registers itself on import (`pkgutil`/`importlib`), so the router resolves the
implementation without a hard import.

---

## 3. Create flow (the richest path)

`POST /subscriptions` (29.122) exercises every decision below; the N5 `POST /app-sessions`
follows the same steps without idempotency.

```text
1. Idempotency    fingerprint = SHA-256(scsAsId + body); reserve it.
                  Already seen → return 202 with the original operation.
2. QoS profile    qosReference → the profile's pre-provisioned slice
                  (slice_id, S-NSSAI, DNN). Required; an S-NSSAI / DNN in the
                  request must match that slice (ADR 0006).
3. IMSI           resolve from the UE IP (subscriber_map).
4. One per UE     under a per-UE lock: reject if the UE already has a session
                  in either API (ADR 0006).
5. SM associate   associate the UE with the profile's slice.
6. Persist        store the session with its slice.
7. Finalise       poll the SM request until terminal, then notify the
                  application (§4.7, §4.13).
8. Respond        201 + the full resource + Location header (does not wait for 7).
```

```mermaid
sequenceDiagram
    participant NEF
    participant T as Translator
    participant DB as SQLite
    participant SM as Slice Manager
    NEF->>T: POST /subscriptions (AsSessionWithQoS)
    T->>DB: reserve idempotency + create operation (pending)
    T->>T: resolve profile → slice, IMSI
    T->>DB: no other session for this UE?
    T->>SM: POST /core/ues/{imsi}/slice-associations (associate)
    SM-->>T: 202 + request_id
    T->>DB: store session; operation → sm_provisioning
    T-->>NEF: 201 Created (+ Location)
    loop until the request is completed/failed
        T->>SM: GET /operations/{request_id}
    end
    T->>DB: operation → completed / failed
    T-->>NEF: POST notificationDestination (callback)
```

---

## 4. Design decisions

### 4.1 Idempotency
A repeated `POST` (same `Idempotency-Key` **or** same payload fingerprint) does not create
duplicate resources, it returns the original operation with `202`. Protects against client
and network retries. The reservation only lasts while the outcome stands: it is released
when the create fails and when the subscription is deleted, so an identical request
afterwards (a camera reconnecting with the same body) creates a new subscription.
*(`app/utils/idempotency.py`, table `idempotency_keys`.)*

### 4.2 Pre-provisioned slices (model B)
The SM owns the slices: it creates one per QoS profile in advance, and several may share one
S-NSSAI and DNN, each `slice_id` selecting a different DNN QoS template. The translator only
writes UE associations: associate on create, move on a QoS change, dissociate on delete.
There is no slice registry, no reference counting and no rollback of slice creation (removed
2026-11-20, [ADR 0004](docs/adr/0004-slice-per-profile-working-assumption.md)).

### 4.3 One session per UE
The SM applies QoS per UE and treats only a UE's first association as the current one when it
moves the UE. A UE (IMSI) may therefore have at most one session, across both APIs. A create
for a UE that already has one is rejected with `403`. 29.122 may move to `409` if its spec
allows it; N5 lists no `409` for the create. A per-UE `asyncio.Lock`, shared by the whole
process, serialises the check and the create. *([ADR 0006](docs/adr/0006-model-b-request-handling.md), decision 3.)*

### 4.4 QoS profiles - static map to SM slices
`qosReference` strings map to the pre-provisioned SM slice that implements them (`slice_id`,
S-NSSAI, DNN) in [`app/config/qos_profiles.py`](app/config/qos_profiles.py)
([ADR 0005](docs/adr/0005-qos-profile-catalog-under-model-b.md)). The SM only reads the
`slice_id`, and takes the DNN and S-NSSAI from the slice itself; its schema still requires
both. The entries `qos_ref_1/2/3` are **placeholders** until the SM team sends its slice list.

A static map was chosen over discovering the slices through `GET /core/slices` for three
reasons:

- It exposes only the slices meant for this use case. The SM lists every slice it created.
- The public names stay stable when SM ids change.
- It needs no extra SM call.

Discovery, or a startup check against the SM list, can be added later.

**Request rules** ([ADR 0006](docs/adr/0006-model-b-request-handling.md)):

- The `qosReference` is required.
- An S-NSSAI or DNN in the request that differs from the profile's slice is rejected. This
  rule is provisional: those members may describe the PDU session rather than choose a
  slice.
- 29.122 `tscQosReq` and N5 `marBw*`/`mirBw*` are parsed but not applied. How to apply them
  is decision D3 of the architecture review. The SM now offers a per-UE AMBR through
  `PATCH /core/ues/{ue_id}/qos`.

### 4.5 SQLite persistence
The translator keeps its own state (sessions, operations, idempotency).
This serves reads without hitting the SM, survives restarts, and tracks async operations. A
single embedded file, no external dependency, right for a testbed MVP. *(`app/db/schema.py`.)*

### 4.6 Resilience -  retry + circuit breaker
Every SM call is wrapped with **retry + exponential backoff with full jitter** (transient
network errors) and a **circuit breaker** that, after `CB_FAILURE_THRESHOLD` consecutive
failures, opens and fails fast with `503` instead of piling up timeouts. The breaker state is
exposed in `/health`. *(`app/resilience/`.)*

Only network errors, timeouts and SM `5xx` answers count as failures. An SM `4xx` means the SM
is up and rejected one request, for example for an unknown slice, so it counts as a success.
Until 2026-11-27 every error counted, so five rejected requests opened the circuit for all
requests. While half-open, calls go through and the first outcome closes or reopens the
circuit.

**Time budget toward the NEF (decided by Bruno on 2026-11-27).** The NEF waits at most 25 s
for the PCF, which is the translator here. Both its northbound and its southbound gateway
default to `request_timeout` 25 s (NEF chart `templates/northbound-gateway/configmap.yaml:33`,
`templates/southbound-gateway/configmap.yaml:44`). The translator answers the NEF only after
the SM's answer, so the SM call has to fit in that time.

- **Settings**: `SM_TIMEOUT=15` and `RETRY_MAX_ATTEMPTS=1`. The worst case is 15 s. Before
  this it was about 60 s: 30 s × 2 attempts.
- **Why 15 s**: the SM answers `202` as soon as Kafka confirms the message. It waits at most
  10 s for that confirmation, then answers with an error (slice-manager
  `modules/control_api/app/impl/kafka_publish.py:33,69-73`). 15 s waits for the SM's own
  answer and still leaves margin for the NEF's internal hops.
- **Why no retry**: after a read timeout the POST may already have reached the SM, so a
  retry could associate or move the UE twice. A connection blip now fails at once with `502`,
  and the application can repeat the request.
- **What happens when the NEF gives up first** (**Inference**): the NEF reports a failure
  while the translator may still complete the operation. A repeated create for that UE is
  then refused with `403`, because of one session per UE (ADR 0006).
- **Possible later step**: a total deadline per request, retrying only errors where the
  request never left the translator (connection refused). That needs code and an ADR.

### 4.7 Async polling of SM operations
After a write, the SM returns `202 + state="published"`: the message reached Kafka, nothing
has been provisioned yet. Since Sept 2026 the SM exposes `GET /operations/{request_id}`,
backed by its `requests` table: `pending → published → processing → completed | failed`,
with a per-worker `components` breakdown (`core`, plus `ran` for slice create/update/delete)
that carries the `error` of a failed worker.

With `SM_POLLING_ENABLED=true` (**default**) the translator polls the SM request of the
operation (the associate of a create, the move of a QoS change) with exponential backoff.
Only `completed` and `failed` are terminal. The operation becomes `completed` once all its
requests completed and `failed` as soon as one fails, copying the component errors into
`operations.error`. Then the application is notified (§4.13). In-flight operations are
resumed at startup (`sm_request_id` keeps the comma-separated IDs).

PUT/PATCH (and the N5 modify) are tracked the same way since the SM's UE move answers
`202 + request_id` (SM `main` `bfbaf29`), see §4.9. DELETE requests are polled for logging only
(no local operation record).

Against an older SM (the June 2026 sandbox returns `500 "Not implemented"`), set
`SM_POLLING_ENABLED=false`: the synchronous `202` is then treated as the terminal result
and the operation is marked `completed` immediately.

### 4.8 Idempotent DELETE (404 tolerance)
The translator treats a 404 on `dissociate_slice` as **success**: removing an
already-absent association is the desired end state. This is correct idempotent-DELETE
semantics and works against the real SM and the no-op sandbox. A failed dissociate is logged
and the session is still forgotten. *(`sm_client._request(tolerate_not_found=True)`.)*

### 4.9 QoS change: move, track, revert
A PUT/PATCH (29.122) or a modify of `medComponents` (N5) resolves the new profile. The steps
are:

1. **Same slice**: no SM call.
2. **Another slice**:
   - The translator calls the SM move (`PATCH /core/ues/{imsi}/slice-associations`) with the
     target `slice_id`, S-NSSAI and DNN.
   - It stores the new slice with the session.
   - It records an operation that keeps the **previous** slice, and polls it like a create.
3. **Move finishes**:
   - On 29.122 the application gets the resource-allocation notification, if it subscribed
     to it. On N5 nothing is sent, as with the Open5GS PCF
     ([ADR 0007](docs/adr/0007-n5-behaves-like-open5gs-pcf.md)).
   - On failure the poller puts the session back on the previous slice, unless a later change
     already moved it. The stored request keeps the values the application asked for.
4. **SM rejects the move outright**: the request fails with `502`/`503` and nothing is
   stored.

*([ADR 0006](docs/adr/0006-model-b-request-handling.md), decision 4.)*

### 4.10 Observability
A correlation-ID middleware tags every log line for a request; logs are structured JSON
(configurable). `/health` reports SM reachability, breaker state, and subscription count for
dashboards.

### 4.11 (removed) N6 protection and coverage area defaults
They only fed slice creation, which the translator no longer does under model B. The
`SM_DEFAULT_COVERAGE_AREA` and `SM_DEFAULT_N6_PROTECTION` settings are gone (2026-11-20).

### 4.12 The UE IP is not sent as a static IP
3GPP `ueIpv4Addr` identifies the UE by the address it **already** has. The SM's
`static_ipv4_address` instead **pins** a static IP in UDM, and that IP must fall inside the
DNN's UDG pool (`10.16.9.x` in the testbed); the associate path does not validate it. Sending
the NEF's address there (or the old `10.0.0.1` fallback) would rewrite the UE's addressing as
a side effect. The translator therefore uses the UE IP only to resolve the IMSI (§3 step 3)
and does not send `static_ipv4_address`. It also no longer sends `default_association`,
which is not part of the SM's `UeSliceAssociationCreateRequest` and was silently dropped.


### 4.13 Notifications (3GPP format)
When polling ends (a create or a QoS change), the translator POSTs a
`UserPlaneNotificationData` to the subscription's `notificationDestination`: `SUCCESSFUL_RESOURCES_ALLOCATION` with `appliedQosRef` (the
subscription's `qosReference`), or `FAILED_RESOURCES_ALLOCATION`. As TS 29.122 requires, an
event is only sent when the subscription listed it in `events`. `transaction` is the
subscription's `self` link. Delivery is best-effort and never changes the operation state.
With polling disabled nothing is sent, since the outcome is unknown. N5 sessions follow
the Open5GS PCF instead ([ADR 0007](docs/adr/0007-n5-behaves-like-open5gs-pcf.md)): only a
failed create is notified, with a `TerminationInfo` to `{ascReqData.notifUri}/terminate`,
and the session is then forgotten. *(`app/impl/notifier.py`.)*

### 4.14 Errors, PATCH and PUT semantics
- **Errors** are 3GPP `ProblemDetails` (`application/problem+json`). Request validation
  failures are `400` with `invalidParams`; the 3GPP API defines no `422`.
  *(`app/apis/problem_details.py`.)*
- **Rejections are logged** (decided by Bruno on 2026-11-27). Every `4xx` on either API
  produces one `WARNING` line with the method, the path, the status and the reason: the error
  detail, or the invalid parameter names and messages. It includes requests to unknown paths.
  - **Never logged**: the request body, because it holds personal data such as the UE address
    or SUPI. For the same reason, the unknown-UE error no longer quotes the UE address.
  - **To see the bodies during the first NEF tests**: capture the traffic on the translator VM
    instead ([`infra/README.md`](infra/README.md), "Capture the NEF's N5 requests"). Logging
    bodies was judged a temporary need, so no code does it.
- **PATCH** applies an RFC 7396 JSON merge patch: a member set to `null` is removed. A patch
  that leaves the subscription invalid (e.g. removing `notificationDestination`) is rejected
  with `400`. *(`app/utils/merge_patch.py`.)*
- **PUT** cannot change the UE: the session lives on that UE's IMSI at the SM (and the 3GPP
  PATCH body has no UE address fields for the same reason). A PUT whose UE address resolves
  to another IMSI is rejected with `400`; delete the subscription and create a new one.

---

## 5. Operation state machine

The translator tracks its **own** operation lifecycle (distinct from the SM's internal one):

```text
pending ──► published ──► sm_provisioning ──► completed / failed   (polling enabled, default)
                      └──► completed                               (polling disabled)
   └────────────────────────────────────────► failed   (on any error)
```

- **pending** -  created locally, before calling the SM.
- **published** -  profile and slice resolved (local meaning; not the SM's `published`,
  which only says the message reached Kafka). A QoS change starts at **pending** and goes
  straight to **sm_provisioning** once the SM accepts the move.
- **sm_provisioning** -  the SM accepted every write; the poller follows them (§4.7).
- **completed / failed** -  terminal. With polling on, the poller sets it from the SM's
  terminal states; with polling off, `completed` is set as soon as the SM accepts the writes.

---

## 6. Data model (SQLite)

| Table | Key | Purpose |
|-------|-----|---------|
| `subscriptions` | (`scs_as_id`, `subscription_id`) | 29.122 subscriptions and N5 sessions (owner `#npcf-policyauthorization`) + slice & IMSI; indexed by IMSI for the one-per-UE rule |
| `operations` | `operation_id` | Per-operation async tracking and status; `previous_sm_slice_id` for QoS changes |
| `idempotency_keys` | (`scs_as_id`, `payload_fingerprint`) | Deduplication of repeated requests |

Online, safe migrations: `init_db` adds missing columns (`sm_request_id`, `notification_url`,
`previous_sm_slice_id`) without breaking existing databases. An old database keeps its unused
`slice_registry` table; nothing reads it.

---

## 7. Known limitations

- **Placeholder catalog.** `qos_ref_1/2/3` map to `placeholder-slice-1/2/3` until the SM team
  sends the slices it provisions (§4.4).
- **Per-request QoS not applied.** `tscQosReq` and the N5 bitrates are parsed but ignored
  (decision D3, §4.4).
- **No CAPIF token.** SM `main` `bfbaf29` starts with CAPIF on by default, and then every SM
  endpoint needs a bearer JWT. The translator sends none, so it would get `401`.
- **Active sessions.** The SM move changes only UDM/SMF subscription data. Whether the camera
  gets the new QoS without reconnecting is unknown (to ask the SM team).
- **SM sandbox is outdated.** The no-op sandbox fork (2026-06-16) predates `/operations`, and
  its stubbed read model only knows a canned `sandbox-demo` slice. The workers therefore fail
  associate/change for other slices after the HTTP `202`. It validates the HTTP layer only;
  run it with `SM_POLLING_ENABLED=false`.
- **Static subscriber map.** UE IP → IMSI comes from `subscriber_map.py` (test entries
  `10.0.0.1/2`). The SM now offers `GET /core/ues/{imsi}/user-plane-info?ip=` (looks the PDU
  session up by IP and returns the IMSI), a possible dynamic replacement; to discuss.
- **Local `.venv` is Python 3.10** while `pyproject.toml` requires `>=3.11` (the Docker image
  is 3.11). Recreate the venv on 3.11 for local work.

---

## 8. Open questions for the teams

1. **Slice list** (SM team): the slices provisioned for this use case. For each: `slice_id`,
   S-NSSAI, DNN and the QoS it gives. They fill the `qosReference` map (§4.4), whose names are
   then shared with OneSource and the N_APP team.
2. **Active session** (SM team): does a UE move take effect on the camera's active PDU
   session, or only after it reconnects?
3. **CAPIF** (SM team): does the testbed SM run with CAPIF on, and how is an invoker
   account for the translator issued?
4. **IMSI resolution** -  may the translator use `user-plane-info?ip=` to resolve UE IP →
   IMSI (§7)?
5. **NEF callback contract** -  the translator sends only the termination request, as the
   Open5GS PCF does (ADR 0007). The NEF's southbound gateway has that route
   (`/npcf-policyauthorization/v1/notify/terminate`). It remains to see how the NEF reacts to
   it.
6. **Persistence at scale** -  keep SQLite or move to PostgreSQL for multi-instance deployment.
