# Translator architecture review

A first-principles review of whether the translator's structure still fits the project, now
that the use case, the NEF and the Slice Manager (SM) are better understood than when the
translator was designed (March–June 2026).

| Step | Content | Status |
|------|---------|--------|
| 1 | Requirements: actors, use cases, requirements, constraints, assumptions, open decisions | Done (2026-10-15) |
| 2 | Current components against the requirements (keep / adapt / remove / add) | **Done (2026-10-15)** |
| 3 | Open decisions: options and architectural impact | Pending |
| 4 | Target architecture and migration plan | Pending |

Requirement IDs (`F*`, `N*`, `C*`, `A*`, `D*`) are referenced by the later steps.

**Status legend:** *Confirmed* (backed by a spec, the SM code or a stakeholder) ·
*To confirm* (a working assumption) · *Open* (a decision nobody has taken yet).

---

## Step 1 — Requirements

### 1. Why this review

The translator was built on assumptions that no longer hold:

- **It owns slices.** It creates one slice per `dnn`+`snssai` on the first subscription and
  deletes it on the last. Whether the translator or the SM should own slices is now an open
  question (D1).
- **A QoS change is a simple update.** The driving use case is scaling a camera's quality up
  and down, which is the PATCH path. That path is the weakest part of the current code, and
  the network procedures behind a scale action are not standardized.
- **The SM is stateless and fire-and-forget.** Since September 2026 the SM tracks every write
  in Postgres and exposes `GET /operations/{request_id}`; it also requires `n6_protection` on
  slice creation and offers new endpoints (in-place slice update, slice UE listing, UE
  lookup by IP).
- **Scale means bitrate.** The supervisor pointed out that scaling a camera is not only
  about bitrate (for example, energy consumption must also change). This is not defined yet.

---

### 2. Driving use case

A camera connected over 5G streams video. A network application (N_APP) decides when the
camera should go up or down in quality and asks the network, through the NEF, for the
matching QoS. The translator turns that request into Slice Manager actions on the Huawei
5G core and RAN, and reports the outcome back.

```text
N_APP (AF) ──29.122──► NEF ──(29.122?)──► Translator ──REST──► Slice Manager ──MML──► 5G core + RAN ──► camera (UE)
     ▲                                        │   ▲                                         │
     └──────── notification (appliedQosRef) ──┘   └──────── operation state (/operations) ──┘
```

---

### 3. Actors and interfaces

| Actor | Role | Interface with the translator | Status |
|-------|------|-------------------------------|--------|
| N_APP | Network application that drives the camera's scale up/down; plays the 3GPP AF role | Indirect, through the NEF; receives notifications | Role *Confirmed*; whether N_APP = AF *To confirm* (A4) |
| CAPIF | API discovery and authorization for the NEF's APIs | None today; may issue tokens the translator must accept or present | NEF integrates CAPIF: *Confirmed* |
| NEF (OneSource) | Exposes 3GPP Rel-17 northbound APIs, starting with `AsSessionWithQoS` | Calls the translator's northbound API | Format *To confirm* (A1) |
| Translator | Maps 3GPP session/QoS requests to SM operations; tracks outcomes; notifies | (this component) | — |
| Slice Manager | Executes slice and UE operations on the network; asynchronous; tracks operations | Translator → SM over HTTP (control-api) | *Confirmed* (SM `main` `43ba5ac`) |
| 5G network | Huawei core (UDM, SMF, AMF, UDG) and RAN; an Open5GS backend exists on an unmerged SM branch | Only through the SM | *Confirmed* |
| Camera | The UE; identified by IP towards the NEF and by IMSI towards the SM | None directly | *Confirmed* |

---

### 4. Use cases

| ID | Use case | Trigger | Expected outcome |
|----|----------|---------|------------------|
| UC1 | Start a QoS session | `POST /{scsAsId}/subscriptions` | The camera gets the requested QoS level; the session is stored; the application is notified of the result |
| UC2 | Scale up / down | `PATCH` (or `PUT`) changing `qosReference` (or `tscQosReq`) | The camera moves to the new QoS level without tearing the session down; the application is notified with `appliedQosRef` |
| UC3 | Read sessions | `GET` one or all | Answered from local state |
| UC4 | End a session | `DELETE` | Network resources for the camera are released; the session is removed |
| UC5 | Report outcomes | SM operation reaches a terminal state | `SUCCESSFUL_RESOURCES_ALLOCATION` / `FAILED_RESOURCES_ALLOCATION` sent to `notificationDestination` |
| UC6 | Network-initiated downgrade | Network can no longer guarantee the QoS | `QOS_NOT_GUARANTEED` with the applied alternative. **Out of scope for now** (needs SM-side QoS monitoring) |
| UC7 | Non-bitrate scale actions (e.g. energy) | Part of a scale request | **Undefined** (D2) |

---

### 5. Functional requirements

| ID | Requirement | Priority | Source | Status |
|----|-------------|----------|--------|--------|
| F1 | Expose 3GPP TS 29.122 `AsSessionWithQoS` v1 (Rel-17): POST/GET on `/{scsAsId}/subscriptions`, GET/PUT/PATCH/DELETE on `/{scsAsId}/subscriptions/{subscriptionId}` | Must | 29.122 V17.9.0; OneSource email (2026-03-09) | *Confirmed*; NEF→translator format *To confirm* (A1) |
| F2 | Accept PATCH as `application/merge-patch+json`, with RFC 7396 semantics (`null` removes a field) | Must | 29.122 | *Confirmed* |
| F3 | Resolve the UE's IP address to the IMSI the SM requires | Must | SM API (`/core/ues/{imsi}/...`) | Method *Open* (D4) |
| F4 | Map a requested QoS level (`qosReference`) to an SM action | Must | Use case | Mapping rule *Open* (D1; assumption A2) |
| F5 | Apply a QoS change to an existing session without tearing it down (scale up/down) | Must | Use case; supervisor | Semantics *Open* (D1, D2) |
| F6 | Track every SM write until it is `completed` or `failed`, including writes that return no `request_id` | Must | SM is asynchronous; the UE move returns no `request_id` | *Confirmed* (SM behaviour) |
| F7 | Notify the application with `UserPlaneNotificationData` (`SUCCESSFUL_RESOURCES_ALLOCATION` / `FAILED_RESOURCES_ALLOCATION`, `appliedQosRef`) for the events it subscribed to | Must | 29.122 | Path (direct or via NEF) *To confirm* (A6) |
| F8 | Reject what cannot be honoured explicitly, in the 3GPP error format (`ProblemDetails`): unknown `qosReference`, unknown UE, unsupported fields | Should | 29.122 | *Confirmed* |
| F9 | Make creation idempotent: a retried request must not create duplicate network state | Should | Robustness | *Confirmed* |
| F10 | Persist sessions so reads, updates and deletes work across restarts | Must | Robustness | *Confirmed* |
| F11 | On DELETE, release the camera's network resources (always the UE association; the slice only if the translator owns slices) | Must | Use case | Scope depends on D1 |
| F12 | Support explicit bitrates (`tscQosReq`) in create and PATCH | Could | 29.122 | Handling *Open* (D3) |
| F13 | Allow a scale request to trigger more than a bitrate change (e.g. energy-related actions) without redesign | Should | Supervisor (2026-10-15) | *Open* (D2) |
| F14 | Network-initiated downgrade (`altQoSReferences`, `QOS_NOT_GUARANTEED`) | Won't (now) | 29.122 | Needs SM monitoring |
| F15 | Allow at most one active session per UE (camera): the SM applies QoS per UE, so a second session for the same UE conflicts with the first and must be rejected | Must | SM per-UE model (C2) | *Confirmed* |

---

### 6. Non-functional requirements

| ID | Requirement | Status |
|----|-------------|--------|
| N1 | **Responsiveness.** Requests return quickly and do not wait for network provisioning (seconds to tens of seconds in the SM); the outcome is reported asynchronously. The acceptable end-to-end time for a scale action is not defined. | Target latency *Open* |
| N2 | **Reliability.** Tolerate SM unavailability (retry, fail fast when the SM is down), leave no orphaned network state after a partial failure, and resume tracking in-flight operations after a restart. | *Confirmed* |
| N3 | **Consistency.** The translator's view of which slice each camera is in must match the SM; it should be able to reconcile with the SM (`GET /core/ues/{imsi}/slice-associations`, `GET /core/slices/{id}/ues`). | *Confirmed* |
| N4 | **Observability.** Correlated, structured logs per request; operation state queryable; ideally joins the SM's OpenTelemetry stack. | *Confirmed* |
| N5 | **Security.** Accept or present OAuth2 tokens if the NEF (CAPIF) or the SM enforce them. CAPIF is off by default in the SM; unknown on the NEF side. | *To confirm* |
| N6 | **Deployability.** One container, configured through environment variables, deployable to a Proxmox VM with Docker Compose. | *Confirmed* |
| N7 | **Maintainability.** Translation (3GPP ↔ SM data) kept separate from policy (what a request means in network actions), because the policy is still undecided and not standardized. Documented for handoff. | *Confirmed* |
| N8 | **Testability.** Testable without hardware: contract tests against the SM OpenAPI, a mock SM, and the sandbox once it is updated. | *Confirmed* |
| N9 | **Scale.** A handful of cameras in the testbed. The SM allows at most 9 slices. | *Confirmed* |

---

### 7. Constraints

| ID | Constraint |
|----|------------|
| C1 | The SM repositories are read-only for this work: any adaptation happens in the translator. |
| C2 | SM contract (`main` `43ba5ac`): writes return `202` + `request_id`, tracked at `/operations`; the UE move returns `200` + association (no `request_id`); `create_slice` requires `coverage_area` and `n6_protection`; `slice_id` ≥ 3 characters, `sd` six digits, `snssai` `N-NNNNNN`; at most 9 slices; a UE move only works within the same `dnn`+`snssai`. |
| C3 | NEF: 3GPP Rel-17, 29.122. A development instance (NEF-Dev) exists; its port, base path and authentication are unknown. The trial NEF must not be experimented on. |
| C4 | No end-to-end test environment today: the SM sandbox dates from June 2026, there is no hardware access, and the Open5GS backend is on an unmerged SM branch. |
| C5 | Current stack: Python 3.11, FastAPI, SQLite, a single process. Can be revisited where a requirement demands it. |
| C6 | The translator acts on the network only: it cannot change the camera's own configuration (resolution, frame rate, encoding). |

---

### 8. Assumptions to validate

| ID | Assumption | Ask |
|----|------------|-----|
| A1 | The NEF forwards `AsSessionWithQoS` requests to the translator in 29.122 format, rather than speaking N5 (`Npcf_PolicyAuthorization`, TS 29.514) as a standard NEF would towards a PCF. | OneSource / SM team |
| A2 | Each `qosReference` corresponds to one SM `slice_id`; the SM already describes `slice_id` as the "QoS profile selector". | SM team |
| A3 | A UE move between slices takes effect on the camera's active session, not only after it reconnects. | SM team |
| A4 | The N_APP plays the AF role (there is no separate AF component). | SM team (N_APP endpoints) |
| A5 | Camera IP addresses are known in advance, or resolvable through the SMF (`user-plane-info?ip=`). | SM team |
| A6 | Notifications go from the translator straight to the application's `notificationDestination`, not back through the NEF. | OneSource |

**Update 2026-11-13.** The NEF chart installed on NEF-Dev on 2026-11-12 contradicts **A1**: the NEF calls a statically configured PCF over N5 with HTTP/2 cleartext, against an Open5GS 2.8.0 core ([ADR 0001](adr/0001-n5-interface-in-same-service.md)). **A6** changes with it: on N5 the translator notifies the NEF at `{evSubsc.notifUri}/notify`.

**Update 2026-11-20.** D1 is confirmed as model B: the SM pre-creates the slices ([ADR 0004](adr/0004-slice-per-profile-working-assumption.md)). The N_APP will be changed to match the NEF.

---

### 9. Open decisions

Analysed in step 3.

| ID | Decision | Options | Owner | Current lean |
|----|----------|---------|-------|--------------|
| D1 | Who owns slice lifecycle | (A) the translator creates and deletes slices · (B) the SM pre-provisions one slice per QoS profile and the translator only associates and moves UEs · (C) the SM creates and manages slices dynamically behind a new intent-style endpoint ("give this UE this QoS"); needs new SM work | Supervisor + SM team | **Decided: B**, confirmed on 2026-11-20 ([ADR 0004](adr/0004-slice-per-profile-working-assumption.md)) |
| D2 | What a scale action changes | Bitrate only · plus network energy parameters (e.g. DRX) · plus device configuration (outside the translator) | Supervisor / project | — |
| D3 | How to handle `tscQosReq` | Nearest profile · in-place slice update (affects every UE on the slice) · reject | Translator, after D1 | — |
| D4 | IMSI resolution | Static map · SM lookup by IP · both (map as override) | Translator + SM team | Both |
| D5 | NEF → translator interface | 29.122 (as built) · N5 | OneSource (A1) | **Decided: N5**, in the same service over a shared core, 29.122 kept ([ADR 0001](adr/0001-n5-interface-in-same-service.md)) |
| D6 | Persistence | SQLite · PostgreSQL | Translator | SQLite until multi-instance |
| D7 | Where the translation lives | Separate service (as built) · module of the SM (the SM translates N5 itself) | Supervisor + SM team | Open; the shared core (ADR 0001) keeps either path possible |

---

### 10. Out of scope (for now)

- Device-side changes (camera resolution, frame rate, energy) — owned by the N_APP.
- Network-initiated downgrade (F14).
- Per-flow QoS (`flowInfo`): the SM applies QoS per UE, not per flow.
- Any change to the Slice Manager.

---

### 11. Sources

- 3GPP TS 29.122 V17.9.0, `3gpp-as-session-with-qos` API 1.2.3 (Rel-17).
- OneSource email, 2026-03-09: the NEF follows Rel-17; the QoS API is the first focus.
- Slice Manager `main` `43ba5ac` (2026-09-28): `schemas/http/openapi.yaml`, `SLICE_CONSIDERATIONS.md`, control-api and core-worker code.
- Supervisor feedback, 2026-10-14 and 2026-10-15: scale procedures are not standardized; scaling is more than bitrate; ask the SM team for the N_APP endpoints.

---

## Step 2 — Current components against the requirements

Each component of `app/` (3,875 lines, `develop` as of 2026-10-15, including the uncommitted
SM-compatibility changes) is checked against the step 1 IDs and given a verdict:
**Keep** (fits as is), **Adapt** (right place, wrong or missing behaviour), **Replace**
(responsibility stays, implementation goes), **Remove**, or **Add** (missing).
Verdicts that depend on an open decision say so.

### 12. Component assessment

| Component | Lines | What it does today | Serves | Verdict | Why / what changes |
|-----------|------:|--------------------|--------|---------|--------------------|
| `apis/translator_api.py`, `apis/translator_api_base.py` | 356 | 29.122 routes; auto-registers the implementation through `__init_subclass__` + `pkgutil` | F1, F2, F8 | **Adapt** | Errors are FastAPI `{"detail": …}`, not `ProblemDetails` (F8). `ip-addrs` is parsed as a comma-separated string; the spec sends a JSON array (F1). The translator's own `/operations` lives inside the 3GPP namespace. `_impl()` builds a **new service instance per request** (issue I2). The auto-registration indirection adds nothing with a single implementation. |
| `models/nef/*` | 344 | Pydantic models of the Rel-17 resources | F1, F7 | **Keep**, small adapt | Fields match Rel-17. `UserPlaneNotificationData` already exists but is unused (F7). The PATCH model cannot tell "absent" from `null` (F2). |
| `models/operation.py` | 39 | Translator operation status | N4 | **Keep** | — |
| `models/slice_manager/` | 8 | Empty placeholder | — | **Remove** | No content. |
| `impl/translator_service.py` | 769 | Everything per request: idempotency, QoS and IMSI resolution, slice policy (registry, create-on-first, delete-on-last, deterministic IDs, rollback), SM payloads, operation status, polling launch, persistence | F3–F11, F15 | **Replace (split)** | Mixes translation, policy, tracking and persistence (against N7). Embeds model A of D1. The PATCH/PUT path is wrong (issue I3). No guard against a second session for the same UE (F15). Split into: a thin session orchestrator, pure 3GPP → SM mappers, a slice policy behind an interface (A/B/C), an operation tracker and a notifier. |
| `impl/sm_client.py` | 241 | Async HTTP client, shared pool, retry + circuit breaker, 404-tolerant deletes | F6, N2 | **Keep**, extend | Add the reads the new design needs: slices (B: profile lookup), UE associations (read-back and reconciliation, F6/N3), `user-plane-info?ip=` (D4). |
| `impl/sm_poller.py` | 262 | Polls `/operations` for all SM requests of an operation; resumes after restart; sends the callback | F6, F7, N2 | **Adapt** | Becomes an operation tracker with two strategies: `/operations` polling, and read-back for the UE move (no `request_id`). The callback payload is not 3GPP (issue I5) and moves to a notifier. PATCH and DELETE are tracked for logging only today. |
| `resilience/*` | 332 | Retry with full-jitter backoff; circuit breaker | N2 | **Keep** | — |
| `middleware/correlation_id.py`, `logging_config.py`, `/health` | 156 + | Correlation IDs, JSON logs, SM reachability + breaker state | N4 | **Keep** | Later: join the SM's OpenTelemetry stack. |
| `config/settings.py` | 166 | Environment configuration | N6 | **Keep** | The SM-create defaults (`n6_protection`, `coverage_area`) are only needed under model A. `sm_default_ran` is unused. |
| `config/qos_profiles.py` | 96 | Maps `qosReference` to full SM slice parameters; each profile has a **different SST** | F4, F5 | **Replace** | Different SSTs make a UE move between profiles impossible (C2). Under B it becomes a `qosReference` → `slice_id` mapping, or a lookup in `GET /core/slices`; under C the reference is forwarded. `default_5qi` is dead. |
| `config/subscriber_map.py` | 75 | Static UE IP → IMSI map with test IPs (`10.0.0.x`) | F3 | **Adapt** | Put behind an IMSI resolver (static map, SM lookup by IP, or both — D4). The MSISDN map is unused. |
| `config/testbed_defaults.py` | 23 | Default DNN and AMBR for the associate call | F4 | **Adapt** | Under B the DNN and S-NSSAI come from the target slice; only the AMBR defaults may remain. |
| `db/*` | 158 | SQLite connection (WAL) and schema with online migrations | F10, N2 | **Keep** (D6) | — |
| `store/repositories.py` — subscriptions | (445 in total) | Session records with IMSI and slice | F10, F15 | **Adapt** | The current slice is never updated on PATCH; no one-session-per-UE constraint (F15). |
| `store/repositories.py` — operations | | Operation records; resume list | F6, N2 | **Adapt** | Only creates have an operation record; scale (PATCH) and DELETE need one too, to be tracked and notified. |
| `store/repositories.py` — idempotency | | Dedup by `Idempotency-Key` or payload fingerprint | F9 | **Adapt** | Keys are never released (issue I1). Fingerprint dedup is not 3GPP behaviour and blocks legitimate repeats. |
| `store/repositories.py` — slice registry | | Slice dedup + reference counting | D1 (A) | **Remove under B/C**, keep under A | Only meaningful while the translator owns slices. |
| `store/subscription_store.py` | 135 | Wrapper over `SubscriptionRepository` "to keep the legacy interface" | — | **Adapt** (merge) | A redundant layer; fold it into the repository. |
| `utils/converters.py` | 80 | 3GPP `BitRate` → kbps; `mbps_to_kbps` | F12 | **Keep** / **Remove** part | `parse_bitrate_to_kbps` serves F12. `mbps_to_kbps` is unused and multiplies by 1024 (1 Mbps = 1000 kbps). |
| `utils/idempotency.py` | 31 | Header extraction, fingerprint | F9 | **Keep** | — |
| `tests/` | 23 tests | Unit + integration with the SM client mocked | N8 | **Adapt**, extend | Several tests lock in current behaviour that must change (PATCH payloads, slice creation). Add contract tests against the SM OpenAPI and a mock SM. |
| `Dockerfile`, `infra/`, `pyproject.toml` | — | One container, compose, deploy runbook | N6 | **Keep** | The local `.venv` is Python 3.10; the project requires 3.11. |

### 13. Missing pieces (Add)

| Piece | Requirement | Note |
|-------|-------------|------|
| Slice policy interface with A / B / C implementations | D1, N7, F4, F5, F11 | The one place that decides what a QoS request means in SM actions. |
| Scale action hook | F13, D2 | Lets a scale request trigger more than a slice move later (e.g. energy-related parameters) without touching the rest. |
| Read-back confirmation for the UE move | F6 | Poll `GET /core/ues/{imsi}/slice-associations` until the target slice shows up. |
| 3GPP notifier (`UserPlaneNotificationData`) | F7 | `SUCCESSFUL_RESOURCES_ALLOCATION` / `FAILED_RESOURCES_ALLOCATION` with `appliedQosRef`, only for subscribed events. |
| `ProblemDetails` error handler | F8 | One exception handler for every 4xx/5xx. |
| One-session-per-UE guard | F15 | Reject (or define a rule for) a second session for the same IMSI. |
| IMSI resolver with SM lookup | F3, D4 | Static map as override, `user-plane-info?ip=` as fallback. |
| Reconciliation with the SM | N3 | Optional: compare local sessions with SM associations at startup or on demand. |
| Token handling | N5 | Once the NEF/SM authentication requirements are known. |
| Contract tests + mock SM | N8 | Testing without hardware until the sandbox is refreshed. |

### 14. Issues found while reviewing

Defects in the current code, independent of the open decisions.

| ID | Issue | Severity | Effect | Status |
|----|-------|----------|--------|--------|
| I1 | Idempotency keys are never released | High | After a DELETE, re-creating an identical session (a camera reconnecting with the same request) returns `202` with the old operation and creates nothing. | Fixed 2026-10-15 |
| I2 | A new service instance per request | Medium | The per-slice `asyncio.Lock`s are never shared, so concurrent create/delete on the same slice are unprotected. Disappears under model B/C. | Fixed 2026-11-20: the per-slice locks went with model A; the new per-UE locks are process-wide ([ADR 0006](adr/0006-model-b-request-handling.md)) |
| I3 | PATCH/PUT QoS change sends the old `slice_id` with the new `snssai` | High | The scale use case does not work against the real SM; target slice never created; registry and stored slice not updated. | Fixed 2026-11-20: the change moves the UE to the new profile's pre-provisioned slice, stores it, tracks the move and reverts on failure ([ADR 0006](adr/0006-model-b-request-handling.md)) |
| I4 | PUT with a different `ueIpv4Addr` keeps the old IMSI at the SM | Medium | The stored session and the network disagree. | Fixed 2026-10-15 |
| I5 | Notification payload is not 3GPP | High | An N_APP implementing 29.122 cannot parse the callback. | Fixed 2026-10-15 |
| I6 | Errors are not `ProblemDetails` | Low | Non-standard error bodies for the NEF. | Fixed 2026-10-15 |
| I7 | `ip-addrs` parsed as comma-separated | Low | Filtering differs from the spec. | Fixed 2026-10-15 |
| I8 | Merge-patch `null` ignored | Low | Fields cannot be removed with PATCH. | Fixed 2026-10-15 |
| I9 | Dead code (`mbps_to_kbps`, MSISDN map, `default_5qi`, `sm_default_ran`, `models/slice_manager`) | Low | Noise; `mbps_to_kbps` would be wrong if used. | Fixed 2026-10-15 |

### 15. Step 2 conclusion

- **The skeleton holds.** The layering (29.122 API → service → SM client), resilience,
  observability, SQLite persistence and packaging all still fit the requirements.
- **The core does not.** `translator_service.py` and `qos_profiles.py` hard-code one answer
  (model A) to a question that is now open (D1), and the scale path built on top of it is wrong
  (I3). The fix is a re-cut, not a rewrite: separate translation from policy, so that A, B or
  C can be plugged in.
- **Several defects are independent of the open decisions** (I1, I4, I5, I6–I9). They were
  fixed on `develop` on 2026-10-15:
  - I1: the idempotency reservation is released when a create fails and when the
    subscription is deleted (CONSIDERATIONS §4.1);
  - I4: a PUT that changes the UE is rejected with `400` (§4.14);
  - I5: notifications are `UserPlaneNotificationData`, sent only for subscribed events (§4.13);
  - I6: errors are `ProblemDetails`; validation errors are `400` with `invalidParams`, and the
    published OpenAPI says so (§4.14);
  - I7: `ip-addrs` accepts the 29.122 JSON array (comma-separated still accepted);
  - I8: PATCH is an RFC 7396 merge patch; a patch that leaves the subscription invalid is `400`;
  - I9: dead code removed.

  The test suite went from 23 to 48 tests. I2 and I3 wait for D1.

  **Update 2026-11-20.** D1 is decided (model B), and model B is implemented on `develop`. I2
  and I3 are fixed, and F15 is enforced: one session per UE across both APIs. The behaviour is
  in [ADR 0005](adr/0005-qos-profile-catalog-under-model-b.md) and
  [ADR 0006](adr/0006-model-b-request-handling.md). The test suite is at 83 tests, and at 85
  after [ADR 0007](adr/0007-n5-behaves-like-open5gs-pcf.md) (2026-11-21).
- **Step 3** analyses the open decisions (D1–D6) and how the target structure accommodates
  each; **step 4** turns that into a target architecture and an incremental migration plan.
