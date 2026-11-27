# 0001. Add an N5 interface in the same service, over a shared translation core

- **Status**: Accepted
- **Date**: 2026-11-13
- **Decided by**: Bruno

## Context

The translator exposes TS 29.122 AsSessionWithQoS (`app/main.py`, prefix `/3gpp-as-session-with-qos/v1`). It was built on assumption A1 of the architecture review ([architecture-review.md §8](../architecture-review.md)): the NEF forwards 29.122 requests to it unchanged.

The OneSource NEF reinstalled on NEF-Dev on 2026-11-12 (Helm chart `onemnef`) contradicts that assumption:

- **Verified (chart `values.yaml`, `southboundGateway`)**: the southbound gateway calls core NFs directly. The PCF address is static (`pcf_host`, `pcf_port`). The BSF and SMF entries were removed. NRF discovery and registration are off (`use_nrf: false`, `register_with_nrf: false`). Calls use plain HTTP with HTTP/2 ("Open5GS SBI is HTTP/2 only").
- **Verified (chart `values.yaml`, `imf.assurance`)**: the chart states that "Open5GS 2.8.0's PCF ignores the AF's event subscriptions and route requests". The NEF is therefore built against the Open5GS 2.8.0 PCF.
- **Verified (Open5GS v2.8.0)**: that PCF serves Npcf_PolicyAuthorization app sessions (`src/pcf/sm-sm.c:131-190`).
- **Inference**: for AsSessionWithQoS, the NEF sends Npcf_PolicyAuthorization (N5) requests to whatever `pcf_host:pcf_port` points at. To connect the NEF to the SM, the translator must answer N5 as if it were the PCF. This settles D5 of the review.
- The translation may later move into the SM (D7, open).

## Options

### A. Same application: new N5 router over a shared translation core

Extract the entry-independent logic from `TranslatorService` (`app/impl/translator_service.py`) into a core:
- resolving the UE and the QoS profile;
- the SM calls;
- the store, poller and notifier.

The 29.122 router and a new N5 router both call that core.

- Pros:
  - The working 29.122 API and its 48 tests stay.
  - One container, one port.
  - The core can move into the SM if D7 goes that way.
  - Survives a change of plan (the NEF forwarding 29.122, or the N_APP calling the translator directly).
- Cons:
  - Needs a refactor before the N5 work starts.
  - Two northbound models to maintain.
- Evidence: the current service mixes 29.122 handling with SM orchestration (`app/impl/translator_service.py:1-17`).

### B. Replace the 29.122 API with N5

- Pros:
  - Least code.
  - Fastest path to N5 alone.
- Cons:
  - Discards the 29.122 models, router and tests.
  - Has to be redone if the NEF or N_APP plan changes.
- Evidence: none against it, except plan uncertainty (A1 was already wrong once).

### C. Separate N5 service in front of the current translator

- Pros:
  - The current translator stays untouched.
- Cons:
  - Two deployments.
  - Double ID mapping (appSessionId to subscriptionId).
  - An extra hop and extra failure points.
  - Harder to merge into the SM later.
- Evidence: n/a.

## Recommendation

A. It keeps everything that already works and isolates the translation logic from both the interface and the deployment.

## Decision

Option A, chosen by Bruno on 2026-11-13. Reasons: it keeps the 29.122 API and its tests, uses a single deployment, and keeps the core reusable if the SM absorbs the translation.

## Consequences

- Refactor first. Extract a core with entry-agnostic operations (create session, modify session, delete session, status) and move the 29.122-specific mapping into its router layer. Existing tests must stay green.
- Add an N5 router, its models and an `appSessionId` mapping in the store.
- The notifier needs an N5 variant alongside the 29.122 `UserPlaneNotificationData`.
- Architecture review: D5 is decided (N5), and D7 is still open.
- Related: [0002](0002-http2-cleartext-with-hypercorn.md), [0003](0003-minimal-n5-scope.md), [0004](0004-slice-per-profile-working-assumption.md).
