# Translator - roadmap & handoff

Status and next steps. For the *why* see [`CONSIDERATIONS.md`](CONSIDERATIONS.md); for deploy
see [`infra/README.md`](infra/README.md).

## Current status

Functional and validated end-to-end against the no-op SM sandbox: create / get / list / put /
patch / delete all pass; DELETE returns `204` (idempotent on SM 404). 16 unit + integration
tests green. Async polling is intentionally disabled (`SM_POLLING_ENABLED=false`) until the SM
exposes `/operations`.

---

## Handoff checklist

1. **Prerequisites** - Python 3.11; access to the testbed VPN; the `nef-translator-dev-0` and
   `sandbox` Docker contexts configured (`docker context ls`).
2. **Set up & run the tests** - follow [`README.md`](README.md) "Quick start";
   `python -m pytest -q` → 16 passing.
3. **Bring up the SM sandbox** - [`infra/README.md`](infra/README.md) "Deploy the Slice
   Manager sandbox" (remember the empty `.env` gotcha).
4. **Deploy the translator to its VM** - [`infra/README.md`](infra/README.md) "Deploy the
   translator", then `curl /health` and open `/docs`.
5. **Smoke test** - run the Postman create→delete flow against the VM.
6. **Read** [`CONSIDERATIONS.md`](CONSIDERATIONS.md) §7 (Known limitations) and §8 (Open
   questions) before changing behaviour.

---

## Next steps
- **QoS profiles → SM catalog** (decided 2026-06-27 by the SM team): the SM will own a fixed
  set of QoS profiles and expose a discovery endpoint; the NEF selects from that subset.
  New work — (SM side) build the endpoint; (translator side) fetch the available profiles from
  it and map `qosReference` to them, replacing the placeholder `app/config/qos_profiles.py`
  (`qos_ref_1/2/3` are test values). See CONSIDERATIONS.md §4.4. Also remove the dead
  `default_5qi` field while at it.
- **Auth** - verify whether the SM/NEF enforce OAuth2 bearer tokens (CAPIF) and add token
  handling to `sm_client` if so.
- **Enable polling** once the SM ships `/operations`: set `SM_POLLING_ENABLED=true` and
  validate the NEF completion callback (`notificationDestination`).
- **Idempotency TTL** - purge stale idempotency keys after a configurable window.

## Later (before production)

- **OpenTelemetry** - plug into the SM's existing OTel → Jaeger/Prometheus/Grafana pipeline.
- **Persistence at scale** - evaluate PostgreSQL vs. SQLite for multi-instance deployment.
- **Run as part of the full stack** - integrate into a combined compose/k8s deployment.

---

## Blockers to track (Slice Manager side)

- **`GET /operations/{request_id}` returns 500** - no `BaseOPSApi` implementation in the SM
  (sandbox or upstream). Blocks async completion tracking; keeps polling disabled.
- **No-op sandbox read model** - only canned demo data (`sandbox-demo` slice; cells
  151/152/153). `delete_slice` 404s for real slices (handled, see CONSIDERATIONS §4.8); full
  E2E semantics need a non-no-op SM.