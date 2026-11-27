# 0003. First N5 version covers create, modify, delete and notifications only

- **Status**: Accepted
- **Date**: 2026-11-13
- **Decided by**: Bruno

## Context

The NEF is built against the Open5GS 2.8.0 PCF ([0001](0001-n5-interface-in-same-service.md)). That PCF bounds what the NEF can rely on. Verified in Open5GS v2.8.0:

| Operation | Request | Response | Source |
|---|---|---|---|
| Create | `POST /npcf-policyauthorization/v1/app-sessions` | 201 with `Location` | `src/pcf/sm-sm.c:178-182`, `src/pcf/npcf-handler.c:1091-1096` |
| Modify | `PATCH .../app-sessions/{appSessionId}` | 200 | `src/pcf/sm-sm.c:163-167`, `src/pcf/npcf-handler.c:1538` |
| Delete | `POST .../app-sessions/{appSessionId}/delete` | 204 | `src/pcf/sm-sm.c:134-141`, `src/pcf/npcf-handler.c:1627` |

Other behaviour of that PCF:

- **UE identification**: by `ascReqData.ueIpv4` or `ueIpv6`; 404 when no session matches (`src/pcf/pcf-sm.c:298-327`).
- **Rejected with 400 when missing**: `ascReqData.suppFeat`, `notifUri` and `medComponents` on create, and `ascUpdateData.medComponents` on modify (validation in `src/pcf/npcf-handler.c`, create and update handlers).
- **QoS selection**: `medComponents[].qosReference` selects a locally configured QoS profile, and an unknown reference is rejected with 403 (`src/pcf/npcf-handler.c:24-41`, `:1139`). Without a reference, the media type (audio, video, control) selects a default (`src/pcf/npcf-handler.c:44-56`). Bandwidths `marBwUl/Dl` and `mirBwUl/Dl` are also read.
- **Notifications**: it sends a terminate notification to the AF (`src/pcf/sbi-path.c:687`). Per the NEF chart, it ignores the AF's event subscriptions.

The TS 29.514 text itself has not been read: it is not in the local spec library, and automated downloads from 3gpp.org are refused. The data types come from the OpenAPI of **TS 29.514 V17.6.0** (API 1.2.1), as shipped in Open5GS v2.8.0 (`lib/sbi/support/r17-20260316-openapitools-7.20.0/standard/TS29514_Npcf_PolicyAuthorization.yaml`). Open5GS renamed the `AfEvent` type to `NpcfAfEvent` in that file; the enum values are unchanged. That file also confirms:

- the termination callback goes to `{ascReqData.notifUri}/terminate`;
- the event callback goes to `{ascReqData.evSubsc.notifUri}/notify`, with an `EventsNotification` body (`evSubsUri` and `evNotifs` required);
- `notifUri` and `suppFeat` are required in `AppSessionContextReqData`, together with one UE address.

## Options

### A. Minimal: create, modify, delete and notifications to `notifUri`

- Pros:
  - Covers the camera scenario: start a session, scale it, stop it.
  - Matches what the NEF can expect from the PCF it was built for.
- Cons:
  - No events-subscription sub-resource.
  - Event notifications are limited to what the core can report.

### B. Fuller N5: also the events-subscription sub-resource and the remaining spec operations

- Pros:
  - Closer to the full spec.
- Cons:
  - More work for features the NEF may never call. Its target PCF ignores event subscriptions.

## Recommendation

A.

## Decision

Option A, chosen by Bruno on 2026-11-13.

## Consequences

- Implement the three operations with the status codes above, and answer 405 to other methods.
- Map `ueIpv4` (or `ueIpv6`) to the IMSI through the existing subscriber map, and `medComponents[].qosReference` to the translator's QoS profiles.
- Accept and store `suppFeat` and `notifUri`.
- Open:
  - which notification the NEF actually processes (`EventsNotification` to `{notifUri}/notify`, the terminate notification, or none);
  - the exact request bodies the NEF sends.

  Both are questions for OneSource, cross-checked with TS 29.514.

### Done on 2026-11-13

Implemented in `app/apis/policy_authorization_api.py`, `app/impl/policy_authorization_service.py` and `app/models/pcf/`, with tests in `tests/integration/test_policy_authorization.py` and `tests/unit/test_policy_authorization_mapping.py`. Implementation choices inside this decision:

- **Status codes**: an unknown UE answers 404 and an unknown `qosReference` answers 403, as the Open5GS PCF does.
- **One QoS profile per session**: media components with different `qosReference` values are rejected with 403.
- **Bitrates**: taken from the lowest-numbered component. `mirBw*` (minimum requested, mapped by Open5GS to `min_requested_bandwidth`, `src/pcf/npcf-handler.c:802-814`) becomes the per-UE GBR, and `marBw*` becomes the per-UE MBR.
- **Dual-stack requests** (IPv4 and IPv6) are accepted, although the spec's `oneOf` asks for exactly one address. Open5GS also accepts them.
- **Notification**: `SUCCESSFUL_RESOURCES_ALLOCATION` / `FAILED_RESOURCES_ALLOCATION` is sent as `EventsNotification` at the end of SM provisioning, only if subscribed in `evSubsc`. Since 2026-11-20 a modify that moves the UE to another slice sends it too ([0006](0006-model-b-request-handling.md)). **Replaced on 2026-11-21** by [0007](0007-n5-behaves-like-open5gs-pcf.md): like the Open5GS PCF, only a termination request is sent, after a failed create.
- **No idempotency on create yet** (the spec's 303).
