# 0005. Where the translator gets its QoS profile catalog under model B

- **Status**: Accepted
- **Date**: 2026-11-20
- **Decided by**: Bruno, 2026-11-20

## Context

Model B is confirmed ([0004](0004-slice-per-profile-working-assumption.md), update of 2026-11-20). The SM pre-creates the slices, and the translator only associates, moves and dissociates UEs. For each `qosReference` the NEF sends, the translator needs the target slice's `slice_id`, S-NSSAI and DNN. Those three values select the slice in the SM's associate and move requests (slice-manager `schemas/http/openapi.yaml:1699`, `:1752`).

- **Verified (current code)**: the catalog is a static Python dict, `QOS_PROFILES` in `app/config/qos_profiles.py`, with placeholder entries `qos_ref_1/2/3`. Each entry holds the parameters needed to *create* a slice (SST, SD, latency, reliability, priority, mobility and so on). It has no `slice_id` and no DNN. The core derives the slice id from SST, SD and DNN (`app/impl/session_core.py`, `derive_slice_id`).
- **Verified (current code)**: an unknown `qosReference` answers 400 on 29.122 and 403 on N5 ([0003](0003-minimal-n5-scope.md)).
- **Verified (SM API)**: `GET /core/slices` "Lists slice profiles discovered from core network state", and several entries may share one DNN and S-NSSAI (`openapi.yaml:321-345`, `:1407`, `:1668`).
- **Verified (SM code, `main` bfbaf29)**:
  - The listing reads the UDM DNN QoS templates (`LST DNNQOSTPL`). It keeps only the templates named with the SM's key `{dnn}__{sst}__{sd}__{slice_id}__{bbus}`, which means only slices created through the SM (`modules/core_worker/slices_get.py:22-26`, `:47-64`; `modules/control_api/app/impl/core.py:84-88`).
  - It returns `slice_id`, `dnn`, `sst`, `sd` and `coverage_area`, with no QoS values. The 5QI is read but not exposed (`modules/control_api/app/impl/core.py:117-124`).
  - It does not filter by use case.
- **Verified (SM code)**: associate takes the DNN and S-NSSAI from the slice and ignores the values in the request (`modules/core_worker/core_consumer.py:780-786`). In practice the SM needs only the `slice_id`. The schema still requires `dnn` and `snssai` (`openapi.yaml:1699`).
- **Verified (SM config)**: CAPIF is now on by default when the SM is started with its Makefile (`infra/capif.env:6`). Every SM call, a listing included, then needs a bearer token (`modules/control_api/app/security_api.py:47-66`).
- **Verified (earlier note)**: the no-op sandbox returns only canned demo data for slice reads (one `sandbox-demo` slice), see TODO.md "No-op sandbox read model".
- The `qosReference` names reach OneSource and the N_APP team. **Inference**: they should stay stable even if the SM team renames or recreates a slice.

## Options

### A. Static map in the translator: `qosReference` → (`slice_id`, S-NSSAI, DNN)

Replace the slice-creation parameters in `app/config/qos_profiles.py` with the three values that select an existing slice. The `qosReference` names are the translator's, and the map is filled from the list the SM team provides.

- Pros:
  - The translator only exposes the slices meant for this use case. The map is an explicit allowlist.
  - Public names stay stable when SM slice ids change: only the map changes.
  - No extra SM call at runtime, and it behaves the same against the no-op sandbox and the real SM.
  - Smallest change to the current code.
- Cons:
  - The data exists in two places (the translator's map and the SM's core state) and can drift.
  - A drift only shows up when a request fails: the SM answers 404 for an unknown target slice (`openapi.yaml:672-676`).
  - Adding a profile needs a translator change and a redeploy.

### B. Discover the catalog from the SM, with `qosReference` = SM `slice_id`

The translator reads `GET /core/slices` (at startup or per request) and accepts any listed `slice_id` as a `qosReference`.

- Pros:
  - One source of truth, so there is no drift.
  - New slices become usable without touching the translator.
- Cons:
  - Every slice created through the SM becomes requestable through the NEF, including slices that are not meant for this use case. Slices made outside the SM are not listed, and could not be associated anyway.
  - The public names are the SM's internal ids, so renaming a slice breaks the N_APP and NEF configuration.
  - Adds a runtime dependency on a read endpoint. Against the no-op sandbox only `sandbox-demo` exists.

### C. Static map as in A, checked against `GET /core/slices` at startup

- Pros:
  - The allowlist and stable names of A.
  - Drift is reported at startup instead of at the first failed request.
- Cons:
  - Both costs: the map, plus an SM call and a policy for a mismatch (refuse to start, or only log).
  - Against the no-op sandbox the check always reports a mismatch.

## Recommendation

A, with the map reduced to `qosReference` → `slice_id` plus the DNN and S-NSSAI the SM schema requires (the SM reads only the `slice_id`). The slice list is small, the SM team fixes it in advance, and the public names have to stay stable. C can be added later on top of A without changing the map or the interface. B fits better if the SM team later exposes only the slices meant for this use case (for example a dedicated catalog endpoint, as the SM team planned on 2026-06-27, TODO.md "QoS profiles → SM catalog").

## Decision

Option A, chosen by Bruno on 2026-11-20 after the review of SM `main` bfbaf29. The decision had been on hold that morning until that review was done.

## Consequences

- `app/config/qos_profiles.py` becomes a map from `qosReference` to the existing slice that implements it: `slice_id`, SST, SD and DNN. The DNN and S-NSSAI are sent because the SM schema requires them, although the SM uses only the `slice_id`. The slice-creation parameters are removed together with the model A code ([0004](0004-slice-per-profile-working-assumption.md), update of 2026-11-20).
- The entries stay placeholders until the SM team sends its slice list.
- A `qosReference` that is not in the map is rejected, as today: 400 on 29.122, 403 on N5.
- A slice that changes in the SM without a matching change of the map shows up as a failed SM operation (404 from the SM, or a failed `/operations` status). A startup check against `GET /core/slices` (option C) can be added later without changing the map.
