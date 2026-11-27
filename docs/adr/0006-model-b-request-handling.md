# 0006. How the northbound APIs behave under model B

- **Status**: Accepted
- **Date**: 2026-11-20
- **Decided by**: Bruno, 2026-11-20

## Context

Under model B the SM pre-creates the slices, and the translator only associates, moves and dissociates UEs ([0004](0004-slice-per-profile-working-assumption.md)). The `qosReference` selects the slice through a static map ([0005](0005-qos-profile-catalog-under-model-b.md)). Four behaviours of the 29.122 and N5 APIs follow from that and had to be chosen.

Facts they rest on:

- **Verified (SM code, `main` bfbaf29)**: the SM treats only a UE's first association as the current one when it moves the UE (slice-manager `modules/core_worker/core_consumer.py:1038`). Two sessions for one UE therefore conflict (review requirement F15).
- **Verified (SM code)**: associate takes the DNN and S-NSSAI from the slice and ignores those in the request (`modules/core_worker/core_consumer.py:780-786`).
- **Verified (SM API)**: a move is accepted with 202 and a `request_id`, and finishes asynchronously (slice-manager `schemas/http/openapi.yaml:641-692`).
- **Verified (TS 29.514 V17.6.0 OpenAPI, API 1.2.1)**: `POST /app-sessions` lists 201, 303, 400, 401, 403, 404, 411, 413, 415, 429, 500 and 503, and no 409 (`refs/open5gs-v2.8.0/lib/sbi/support/r17-20260316-openapitools-7.20.0/standard/TS29514_Npcf_PolicyAuthorization.yaml`).
- **Unknown**: whether TS 29.122 `AsSessionWithQoS` allows 409 on a create. Neither its OpenAPI nor its text is available locally.

## Decisions

### 1. A request without `qosReference` is rejected

The options were to reject it, or to apply a configured default profile. The choice is to reject.

- Without a profile there is no slice to associate the UE with.
- The status is the same as for an unknown profile: 400 on 29.122 and 403 on N5. On N5 a request without any `qosReference` in its media components is rejected.

### 2. An S-NSSAI or DNN that differs from the profile's slice is rejected (provisional)

The options were to ignore the values and log them, or to reject the request. The choice is to reject.

- **Comparison rules**:
  - 29.122 `snssai` and `dnn`, and N5 `sliceInfo` and `dnn`, are compared with the slice of the selected profile.
  - The SST must match. The SD must match when the request gives one; hex case is ignored. The DNN must match exactly.
- **Status**: 400 on 29.122 and 403 on N5, as for an unknown profile.
- **Why it is provisional**: Bruno expects this may have to change. **Background knowledge, not verified in the spec text**: in both APIs these members describe the UE's PDU session rather than choose a slice. If the NEF fills them with values that do not match the profile, valid requests will be refused, and the rule should then become "ignore and log".

### 3. A second session for the same UE is rejected: 409 where the spec allows it, otherwise 403

A UE (IMSI) may have at most one session across both APIs. A create for a UE that already has one is rejected.

- **N5**: 403, because the operation does not list 409 (see Context).
- **29.122**: 403 for now. It becomes 409 if the TS 29.122 OpenAPI turns out to allow 409 on the create (to be checked).
- A per-UE lock serialises the check and the create inside the process, so two concurrent creates for one UE cannot both pass. The lock is in-process only, like the single SQLite instance (review D6).
- A repeated N5 create, such as a NEF retry after its 25 s timeout, is also rejected, with 403. The spec's 303 for a repeated create is not implemented yet ([0003](0003-minimal-n5-scope.md)).

### 4. A QoS change is tracked to its end, notified, and reverted on failure

The options were to track it, notify the outcome and revert the stored slice on failure, or only to log the outcome. The choice is to track, notify and revert.

- **No change**: when the new profile maps to the UE's current slice, the SM is not called.
- **SM accepts the move**: the translator stores the new slice and records an operation with the previous slice. It then polls the SM request, as it does for a create.
- **Move finishes**:
  - The translator sends the same resource-allocation notification as for a create, only if the application subscribed to the event. On 29.122 that is `SUCCESSFUL_RESOURCES_ALLOCATION` or `FAILED_RESOURCES_ALLOCATION` in `UserPlaneNotificationData`. On N5 it was the same events in `EventsNotification`, until [0007](0007-n5-behaves-like-open5gs-pcf.md) (2026-11-21) replaced N5 notifications with a termination request after a failed create only. A QoS change on N5 is now never notified.
  - On failure the stored slice goes back to the previous one, unless another change has replaced it in the meantime. The stored request keeps the values the application asked for.
- **SM rejects the move outright**: the PUT or PATCH fails (502, or 503 when the circuit breaker is open), and nothing is stored.
- **Polling disabled**: the accepted move is the final result, as for a create, and no notification is sent.

## Consequences

- The model A code is removed: slice creation and deletion, the slice registry, the per-slice locks, the rollback paths and the create payload builder ([0004](0004-slice-per-profile-working-assumption.md), update of 2026-11-20).
- Delete only dissociates the UE.
- The operations table gains a `previous_sm_slice_id` column, added in place like the earlier columns.
- The per-request values that only made sense when the translator created slices are still parsed but no longer applied:
  - 29.122 `tscQosReq` (delay, priority, GBR and MBR);
  - N5 `marBw*` and `mirBw*`.

  Applying them is decision D3 of the architecture review. The SM now offers a per-UE AMBR for it (`PATCH /core/ues/{ue_id}/qos`).
- Open: whether 29.122 allows 409 (decision 3), and whether the NEF's `sliceInfo` and `dnn` match the profiles (decision 2).
