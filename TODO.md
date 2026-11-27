# Translator - roadmap & handoff

Status and next steps. For the *why* see [`CONSIDERATIONS.md`](CONSIDERATIONS.md); for deploy
see [`infra/README.md`](infra/README.md).

## Current status

**20 November 2026 - model B.** The SM pre-creates one slice per QoS profile (confirmed
2026-11-20). The translator no longer creates or deletes slices. It associates each UE with
the slice its `qosReference` selects, moves it on a QoS change and dissociates it on delete
([ADR 0004](docs/adr/0004-slice-per-profile-working-assumption.md) update,
[0005](docs/adr/0005-qos-profile-catalog-under-model-b.md),
[0006](docs/adr/0006-model-b-request-handling.md)).

- A `qosReference` is required, and the request's S-NSSAI/DNN must match its slice.
- One session per UE across both APIs (`403`).
- QoS changes are tracked to completion, notified, and reverted on failure.
- The slice map holds placeholders until the SM team sends its list.
- 95 unit + integration tests green (2026-11-27, with the circuit breaker fix).
- Each answer to the NEF fits its 25 s limit: one SM attempt of 15 s (CONSIDERATIONS §4.6).
- Rejected requests are logged with their reason, never with their body (CONSIDERATIONS §4.14).

**November 2026 - N5 interface added.** The NEF installed on NEF-Dev on 2026-11-12 calls a
statically configured PCF over N5 (TS 29.514) with HTTP/2 cleartext, against an Open5GS 2.8.0
core, instead of forwarding TS 29.122. The translator now answers N5 as that PCF
(`/npcf-policyauthorization/v1`: create, modify, delete, resource allocation notification)
next to the unchanged 29.122 API, both over one shared core (`app/impl/session_core.py`), and
runs on Hypercorn for HTTP/2 cleartext. Decisions in [`docs/adr/`](docs/adr/) 0001–0004.
N5 is tested with mocks only; nothing has been called from the NEF yet.

**October 2026 - aligned with the Slice Manager `main` of 2026-09-28 (`43ba5ac`).** The create
payload carries the now-mandatory `n6_protection` (default `rule_any`) and `coverage_area`;
the associate payload no longer sends `static_ipv4_address` / `default_association`; async
polling of the SM `/operations` endpoint is **on by default** and follows both the create
and the associate request of each operation. Issues I1 and I4–I9 from the architecture
review ([`docs/architecture-review.md`](docs/architecture-review.md)) are fixed. 48 unit +
integration tests green.

**Not yet validated against a running SM of that version**: the only SM deployment available
for tests is the no-op sandbox, whose code dates from 2026-06-16 (no `/operations`, no
`n6_protection` requirement). Against it, run with `SM_POLLING_ENABLED=false`.

Previously (June 2026) validated end-to-end against that sandbox: create / get / list / put /
patch / delete all pass at the HTTP level; DELETE returns `204` (idempotent on SM 404).

---

## Handoff checklist

1. **Prerequisites** - Python 3.11; access to the testbed VPN; the `nef-translator-dev-0` and
   `sandbox` Docker contexts configured (`docker context ls`).
2. **Set up & run the tests** - follow [`README.md`](README.md) "Quick start";
   `python -m pytest -q` → 95 passing.
3. **Bring up the SM sandbox** - [`infra/README.md`](infra/README.md) "Deploy the Slice
   Manager sandbox" (remember the empty `.env` gotcha, and that it predates the current SM).
4. **Deploy the translator to its VM** - [`infra/README.md`](infra/README.md) "Deploy the
   translator", then `curl /health` and open `/docs`.
5. **Smoke test** - run the Postman create→delete flow against the VM.
6. **Read** [`CONSIDERATIONS.md`](CONSIDERATIONS.md) §7 (Known limitations) and §8 (Open
   questions) before changing behaviour.

---

## Next steps

- **N5 against the real NEF** - needs OneSource: point `southboundGateway.pcf_host`/`pcf_port`
  of NEF-Dev at the translator, confirm the cluster reaches `10.16.255.x`, the request bodies
  the NEF actually sends (create / modify / delete), the h2c mode (prior knowledge, or
  `Upgrade: h2c`, which Hypercorn only honours on requests without a body), the reachable
  address of the southbound gateway (seen 2026-11-21: `sgw` LoadBalancer on 10.255.13.14:8500,
  configured as `sgw.onemnef.com:8500`; the compose maps that name to 10.255.13.14 since
  2026-11-27, but that the translator VM reaches it is not checked yet), and how the NEF reacts
  to the termination request (ADR 0007). During the first tests, capture the traffic on the
  translator VM to see the request bodies ([`infra/README.md`](infra/README.md), "Capture the
  NEF's N5 requests").
- **Revisit ADR 0007 options 1-3** (deferred) once real NEF requests are seen. In each case
  Open5GS is more lenient than the translator:
  - no `qosReference`: Open5GS uses a default per media type;
  - `sliceInfo`/`dnn`: Open5GS ignores them;
  - a second app session per UE: Open5GS accepts it.
- **Fill the QoS profile map** (ADR 0005) - replace the placeholders in
  `app/config/qos_profiles.py` with the SM team's slices (`slice_id`, S-NSSAI, DNN, and the
  QoS each gives). Agree the `qosReference` names with them, then tell OneSource and the N_APP
  team (the N_APP will be changed to match the NEF, 2026-11-20).
- **Check 409 in TS 29.122** (ADR 0006, decision 3) - a second session for a UE answers `403`.
  Switch the 29.122 create to `409` if its OpenAPI allows it (`UE_SESSION_EXISTS_STATUS` in
  `app/impl/translator_service.py`). N5 stays `403`.
- **Revisit the S-NSSAI/DNN rule** (ADR 0006, decision 2, provisional) - once the real NEF
  requests are known, switch to "ignore and log" if the NEF fills `sliceInfo`/`dnn` with
  values that do not match the profiles.
- **Per-request QoS (D3)** - apply 29.122 `tscQosReq` and N5 `marBw*`/`mirBw*`, or keep them
  ignored. The SM's `PATCH /core/ues/{ue_id}/qos` sets a per-UE AMBR (a maximum only, no GBR).
- **N5 beyond the minimal scope** (ADR 0003) - `303` for a repeated create (idempotency),
  `GET /app-sessions/{id}`, the `events-subscription` sub-resource, the `/terminate`
  notification, QoS-update events on modify, and reading the procedures in the TS 29.514
  text itself (only its OpenAPI, V17.6.0, has been used so far).
- **Validate against an SM `main` (Nov 2026+) deployment** - ideally a no-op build of the
  current SM (to request from the SM team): create → `/operations` reaches `completed` for
  the associate, a QoS change reaches `completed` for the move, NEF callback fires,
  dissociate works.
- **Active session** - ask the SM team whether a UE move takes effect on the camera's active
  PDU session or only after it reconnects (review A3).
- **NEF callback contract** - the translator sends the 3GPP `UserPlaneNotificationData`
  (CONSIDERATIONS §4.13); confirm with the N_APP that this is what it expects.
- **IMSI resolution** - evaluate the SM's `user-plane-info?ip=` lookup as a replacement for
  the static `subscriber_map.py` (CONSIDERATIONS §7).
- **Auth (CAPIF)** - since SM `main` bfbaf29 CAPIF is on by default when the SM is started
  with its Makefile (`infra/capif.env`); every SM endpoint then needs `Authorization: Bearer
  <JWT>` (RS256, signature and expiry checked). The translator sends no token. It has to be
  onboarded as a CAPIF invoker of the SM API (SM `infra/capif/README.md`) and send the token.
  Ask the SM team whether the testbed SM runs with CAPIF on and how an invoker account is issued.
- **Idempotency window** - keys are released when a create fails or the subscription is
  deleted; optionally also limit how long a replay of a live subscription is answered.

## Later (before production)

- **OpenTelemetry** - plug into the SM's OTel collector → Jaeger/Prometheus/Grafana/Loki stack
  (now part of the SM compose).
- **Persistence at scale** - evaluate PostgreSQL vs. SQLite for multi-instance deployment.
- **Run as part of the full stack** - integrate into a combined compose/k8s deployment.

---

## Blockers to track (Slice Manager side)

- **No sandbox of the current SM** - the no-op sandbox fork was last updated 2026-06-16;
  the current contract (`/operations`, the move answering `202 + request_id`) cannot be
  exercised without real hardware until it is refreshed.
- ~~**UE move returns no `request_id`**~~ - fixed in SM `main` bfbaf29: the move answers `202`
  with a `request_id` (seen 2026-11-20); the translator tracks it (ADR 0006).
- **No-op sandbox read model** - only canned demo data (`sandbox-demo` slice; cells
  151/152/153). Worker steps fail for other slices after the HTTP `202`.
- **SM doc vs code on the UE move** - the SM's `SLICE_CONSIDERATIONS.md` §5.7 says a move
  stays within one DNN + S-NSSAI, but the worker also moves across them (remove + add,
  `modules/core_worker/udm.py:272-342`). Worth telling the SM team.
- **Possible SM bug: listing a slice's UEs** - `GET /core/slices/{id}/ues` reads the SMF
  group named after the DNN (`modules/core_worker/ue_get.py:166`). Associate now writes the
  group named after the slice (`modules/core_worker/smf.py:380`), so the list may come back
  empty. Not verified at runtime.
