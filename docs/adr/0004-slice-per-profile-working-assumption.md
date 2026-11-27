# 0004. Working assumption: the SM pre-provisions one slice per QoS profile

- **Status**: Accepted (confirmed on 2026-11-20, see the update at the end)
- **Date**: 2026-11-13
- **Decided by**: Bruno, as a working assumption; confirmed on 2026-11-20.

## Context

D1 of the architecture review asks who owns the slice lifecycle ([architecture-review.md:155](../architecture-review.md)). The review recorded "B now; C if the SM team builds it".

- **Verified (current code)**: the code follows option A. It creates a slice when needed and deletes it with the last subscriber (`app/impl/translator_service.py:12-15`).
- **Verified (current code)**: the update path calls `change_slice` with the subscription's old slice id and the new S-NSSAI (`app/impl/translator_service.py:648`). The slice for the new profile is never created, so a QoS change fails against a real SM.
- The N5 modify operation (the camera scale-up/down, [0003](0003-minimal-n5-scope.md)) depends on this choice.
- **Verified**: the SM API already has UE slice-association resources (`/core/ues/{ue_id}/slice-associations` and `/core/ues/{ue_id}/slice-associations/{slice_id}`, slice-manager `schemas/http/openapi.yaml:508`, `:649`).

## Options

### A. The translator creates and deletes slices

The current code.

- Pros: no work for the SM team.
- Cons:
  - Creates network slices in the core per request, which is slow and risky.
  - The modify path is broken today and needs a create, move and cleanup sequence.

### B. The SM pre-provisions one slice per QoS profile

The translator only associates and moves UEs.

- Pros:
  - Simple and fast.
  - Create, modify and delete become associate, change association and dissociate.
- Cons:
  - Profiles are fixed in advance.
  - The SM team has to create and maintain those slices.

### C. The SM offers an intent-style endpoint ("give UE X QoS Y") and manages slices itself

- Pros:
  - The cleanest split.
  - Fits a future in which the SM absorbs the translation (D7).
- Cons: needs new SM work that does not exist yet.

## Recommendation

B now, and C if the SM team builds it.

## Decision

Option B as the translator's working assumption, chosen by Bruno on 2026-11-13, so that the N5 work can proceed. If the owners choose C, a new ADR supersedes this one.

## Consequences

- Each `qosReference` the translator accepts names one QoS profile, and each profile maps to one pre-provisioned SM slice. The profile names are the translator's to define (it plays the PCF). They are agreed with the SM team, then communicated to OneSource and the N_APP team.
- The translator stops creating and deleting slices. Configuration needs a profile-to-slice-id map.
- The modify bug at `app/impl/translator_service.py:648` disappears with the redesign: modify becomes a change of association between two existing slices.
- To ask the SM team:
  - confirmation of B;
  - the slices they will provision and their ids;
  - the semantics of `priority_label` (0-100).

## Update 2026-11-20: confirmed

Bruno reported on 2026-11-20 that model B is confirmed: the SM will have the slices pre-created. The N_APP will also be changed to match the NEF. **Inference**: the N_APP then reaches the translator only through the NEF, which speaks N5 to the translator, so the `qosReference` names the N_APP sends must be the translator's profile names.

What SM `main` bfbaf29 (2026-11-04) offers for model B (**Verified**, slice-manager `schemas/http/openapi.yaml`):

- `GET /core/slices` lists the slice profiles found in the core (`slice_id`, `sst`, `sd`, `dnn`, and so on). Several slices may share one `dnn` and S-NSSAI, and each `slice_id` then selects a different DNN QoS template (`:321-345`, `:1668`).
- Associate (`POST /core/ues/{ue_id}/slice-associations`) needs `dnn`, `snssai` and `slice_id`, and optionally takes a per-UE AMBR (`:1699`).
- Move (`PATCH /core/ues/{ue_id}/slice-associations`) needs the target `dnn`, `snssai` and `slice_id`. The OpenAPI says "The server resolves the UE's current association within the same DNN/S-NSSAI service context" (`:641-692`, `:1752`). It now answers 202 with a `request_id`.
  - **Verified (SM worker code)**: the move is not limited to the same DNN and S-NSSAI.
    - When DNN and S-NSSAI stay the same, it changes the UE's DNN QoS template in place.
    - When either changes, it removes the old UE subscription data and adds the new one (slice-manager `modules/core_worker/udm.py:272-342`).
    - This contradicts the SM's own `SLICE_CONSIDERATIONS.md:350-356` and corrects an inference made earlier the same day.
  - **Verified**: the worker treats only the UE's first association as the current one (`modules/core_worker/core_consumer.py:1038`). The translator should keep one association per UE (review F15).
  - **Verified**: no move command touches an active PDU session; only UDM and SMF subscription data change. Whether the move takes effect without reconnecting stays **Unknown** (A3).
- Associate takes the DNN and S-NSSAI from the slice itself and ignores the values in the request (`modules/core_worker/core_consumer.py:780-786`). The slice must exist in the UDM under the SM's naming scheme, or the operation fails.
- New since 43ba5ac: `GET`/`PATCH /core/ues/{ue_id}/qos` changes the per-UE AMBR without touching the associations (`:770-847`, `:1860`). This bears on D3 (per-request bitrates).

Still to ask the SM team: the slices they will provision (ids, S-NSSAI, DNN and the QoS of each); whether a move takes effect on an active session (review assumption A3); the semantics of `priority_label`.

Also decided by Bruno on 2026-11-20: when model B is implemented, the model A code is removed rather than kept behind a setting. This covers slice creation and deletion, the slice registry with its reference counting, the per-slice locks, the rollback paths and the create payload builder. Git history keeps it if it is ever needed again. Where the profile-to-slice catalog comes from is a separate decision ([0005](0005-qos-profile-catalog-under-model-b.md)).

Implemented on `develop` on 2026-11-20 together with [0005](0005-qos-profile-catalog-under-model-b.md) and [0006](0006-model-b-request-handling.md).
