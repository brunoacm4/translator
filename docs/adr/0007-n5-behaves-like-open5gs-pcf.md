# 0007. On N5 the translator behaves like the Open5GS 2.8.0 PCF

- **Status**: Accepted for option 4A; options 1 to 3 deferred
- **Date**: 2026-11-21
- **Decided by**: Bruno, 2026-11-21

## Context

The OneSource NEF was built and tested against the Open5GS 2.8.0 PCF ([0001](0001-n5-interface-in-same-service.md)). A translator that behaves like that PCF is accepted by the NEF without knowing the exact requests the NEF sends. No NEF page documents those requests:

- **Verified (2026-11-21)**: the NGW documentation covers only the northbound APIs (`nef-docs/2026-11-21/openapi2.json`).
- **Verified (2026-11-21)**: the SGW documentation lists only the callbacks it receives from the core (`nef-docs/2026-11-21/openapi3.json`).

Under [0006](0006-model-b-request-handling.md), the translator is stricter than that PCF in four places. Verified in Open5GS v2.8.0 `src/pcf/`:

| Case | Open5GS PCF | Translator (0006) |
|---|---|---|
| No `qosReference` | Picks a default by media type: AUDIO, VIDEO, CONTROL. Rejects only if the media type is missing too (`npcf-handler.c:24-56`). | 403 |
| `sliceInfo` / `dnn` in the request | Not read by the app-session create handler (`npcf-handler.c`, no `AscReqData->slice_info` or `->dnn` read) | 403 if they differ from the profile's slice |
| Second app session for the UE | Added to the session's app list with no duplicate check (`npcf-handler.c:873`) | 403 |
| Notifications to the NEF | Only the termination, to `{ascReqData.notifUri}/terminate`, when the UE's PDU session is released (`npcf-handler.c:625-627`, `naf-build.c:33`). Never an `EventsNotification`. | `EventsNotification` to `{evSubsc.notifUri}/notify` for resource allocation success or failure; no termination |

Facts on the NEF side (**Verified**, SGW OpenAPI):

- The termination arrives at `POST /npcf-policyauthorization/v1/notify/terminate`, "sent to ascReqData.notifUri + /terminate".
- Events arrive at `POST /npcf-policyauthorization/v1/notify`, and "evSubsc.notifUri points here".
  - The spec sends events to `{evSubsc.notifUri}/notify` (TS 29.514 V17.6.0 OpenAPI, callback at line 416).
  - If the NEF puts the `.../notify` address in `evSubsc.notifUri`, a spec-following PCF would call `.../notify/notify`, which the SGW does not have (**Unknown**: depends on the real request).

A limit applies: the SM gives a UE one association, and so one slice, at a time ([0006](0006-model-b-request-handling.md), Context). Open5GS can hold several app sessions per UE because it applies QoS per flow.

This ADR covers the N5 API only. The 29.122 API, used for direct tests, keeps [0006](0006-model-b-request-handling.md).

## Options

### 1. Request without `qosReference`

- **A. Default profile by media type, like Open5GS.** VIDEO, AUDIO and CONTROL each map to a configured profile (placeholders until the SM team's slice list). A request with no media type is rejected with 403.
  - Pros: accepts what the NEF already sends to Open5GS.
  - Cons: three default profiles to agree with the SM team.
- **B. Keep rejecting (0006).**
  - Pros: no defaults to agree.
  - Cons: requests that Open5GS accepts fail.

### 2. `sliceInfo` / `dnn` that differ from the profile's slice

- **A. Ignore and log, like Open5GS.**
  - Pros: no valid request is refused.
  - Cons: a mismatch goes unnoticed except in the log.
- **B. Keep rejecting (0006, provisional).**
  - Pros: mismatches are visible.
  - Cons: requests that Open5GS accepts fail.

### 3. Second session for the same UE

- **A. Accept it when it selects the UE's current slice; reject it with 403 when it selects another slice.**
  - The SM is only called for the first session.
  - The UE is dissociated when its last session is deleted. This needs a per-UE session count.
  - Pros: closest to Open5GS that one slice per UE allows. Covers a NEF retry of the same create.
  - Cons: more logic: count sessions per UE; decide whether a QoS change of one session may move a UE shared by others (proposal: reject with 403).
- **B. Accept every session; the newest one decides the slice.**
  - Pros: never rejects.
  - Cons: one session silently changes the QoS of another, and deleting one session leaves the slice of the other undefined.
- **C. Keep rejecting every second session (0006).**
  - Pros: simplest.
  - Cons: a NEF retry or a second flow fails where Open5GS accepts it.

### 4. Notifications to the NEF

- **A. Like Open5GS: only the termination.**
  - Send `{ascReqData.notifUri}/terminate` when the SM reports that the create failed, since the session never got its resources.
  - A failed QoS change only restores the previous slice (0006, decision 4), with no notification.
  - Stop sending `EventsNotification`.
  - Pros: uses the route the NEF certainly has; removes the `.../notify/notify` risk.
  - Cons: the NEF learns of a success only from the 201; a failed QoS change is invisible to it.
- **B. Termination as in A, plus the current `EventsNotification` when subscribed.**
  - Pros: more information, as the spec allows.
  - Cons: the NEF path for events was never exercised against Open5GS, and its URL may not match.
- **C. Keep the current behaviour (events only).**
  - Pros: no work.
  - Cons: the NEF never gets the one notification it is known to expect.

## Recommendation

1A, 2A, 3A and 4A: behave like the Open5GS PCF wherever the SM allows it.

The real requests are then needed only to validate the behaviour, not to build it. What would change the recommendation:

- for 4, the NEF documenting that it processes `EventsNotification`;
- for 3, the SM supporting several associations per UE.

## Decision

Taken by Bruno on 2026-11-21.

- **Notifications: option 4A.** Like the Open5GS PCF, the translator sends only the termination request, when the SM reports that a create failed. It no longer sends `EventsNotification`.
- **Options 1 to 3 are deferred.** Bruno's reason: these cases will not affect the large majority of requests, so they should not hold up a first phase. Until the real NEF requests show a problem, the N5 API keeps the behaviour of [0006](0006-model-b-request-handling.md):
  - a request without `qosReference` is rejected;
  - a `sliceInfo` or `dnn` that does not match the profile's slice is rejected;
  - a second session for the same UE is rejected.

## Consequences

Implemented on `develop` on 2026-11-21 (`app/impl/policy_authorization_service.py` `build_termination_request`, `app/impl/notifier.py`).

- **When the SM reports that an N5 create failed**:
  - The translator POSTs a `TerminationInfo` to `{ascReqData.notifUri}/terminate`, which is the route the NEF's southbound gateway serves.
  - It then forgets the session. The Open5GS PCF drops app sessions together with the PDU session (`src/pcf/context.c:652-673`), so a later delete from the NEF answers 404, as it would there.
- **Body of the termination request**: the Open5GS PCF sends a termination request with no body (`src/pcf/naf-build.c:22-46`). The translator sends the spec's `TerminationInfo` (`termCause`, `resUri`), which the NEF's route also accepts, since it requires a JSON object.
  - `termCause` is `INSUFFICIENT_QOS_FLOW_RESOURCES`. It was chosen from the enum names in the OpenAPI, because the spec text that defines each cause has not been read.
- **No notification is sent** after a successful create or QoS change, or after a failed QoS change. A failed QoS change still restores the previous slice ([0006](0006-model-b-request-handling.md), decision 4).
- **`EventsNotification` is no longer sent on N5.** This removes the `.../notify/notify` risk.
- **29.122 is unchanged**: it still sends `UserPlaneNotificationData`.
- **Still to do**: a termination when the UE's session ends on the network side, which Open5GS sends when the PDU session is released. The SM does not report that today.
