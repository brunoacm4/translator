# 0002. Serve HTTP/2 cleartext with Hypercorn

- **Status**: Accepted
- **Date**: 2026-11-13
- **Decided by**: Bruno

## Context

- **Verified (NEF chart `values.yaml`, `southboundGateway`)**: the NEF calls the PCF with `protocol: http` and `http2: True`, commented "Open5GS SBI is HTTP/2 only". The translator, acting as the PCF ([0001](0001-n5-interface-in-same-service.md)), must therefore accept HTTP/2 over plain TCP (h2c).
- **Verified**: the translator runs `uvicorn==0.13.4` (`Dockerfile:20`, `pyproject.toml:11`).
- **Inference (background knowledge, not checked in this session)**: uvicorn speaks HTTP/1.1 only.
- **Unknown**: whether the NEF opens h2c with prior knowledge or with an HTTP/1.1 Upgrade.

## Options

### A. Replace uvicorn with Hypercorn

An ASGI server, so the FastAPI app is unchanged.

- Pros:
  - One dependency swap.
  - No extra component.
  - Keeps serving HTTP/1.1 for 29.122 clients and `/health`.
- Cons:
  - New dependency.
  - h2c support must be verified (see Consequences).
- Evidence: background knowledge only, to verify.

### B. Reverse proxy (nginx or envoy) converting h2c to HTTP/1.1

- Pros:
  - The application server is unchanged.
- Cons:
  - An extra process or container to configure and operate.
  - An extra hop.
- Evidence: n/a.

### C. Keep uvicorn for 29.122 and run a second server for N5

- Pros:
  - The 29.122 path is untouched.
- Cons:
  - Two servers and two ports in one service.
  - More complex startup and health checks.
- Evidence: n/a.

## Recommendation

A, as the smallest change that covers both interfaces.

## Decision

Option A, chosen by Bruno on 2026-11-13.

## Consequences

- Swap the dependency in `pyproject.toml` and the `Dockerfile` (`CMD` on port 8081).
- Before switching, verify from Hypercorn's documentation that it serves h2c, both with prior knowledge and with Upgrade.
- Test with an HTTP/2 prior-knowledge client.
- Confirm with OneSource, or from the Open5GS SBI client code, which h2c mode the NEF uses.

### Done on 2026-11-13

- `hypercorn==0.18.0` replaces uvicorn in `pyproject.toml` and the `Dockerfile` (`CMD hypercorn app.main:app --bind 0.0.0.0:8081`).
- **Verified (Hypercorn 0.18.0 source)**:
  - prior knowledge is supported: a `PRI * HTTP/2.0` preface switches the connection to HTTP/2 (`hypercorn/protocol/h11.py:320-323`, `hypercorn/protocol/__init__.py:67-68`);
  - `Upgrade: h2c` is honoured only on requests without a body; a POST with a body and `Upgrade: h2c` is answered in HTTP/1.1 (`hypercorn/protocol/h11.py:306-319`).
- **Verified locally with curl 7.81 (nghttp2)** against the app on `127.0.0.1`:
  - `--http2-prior-knowledge` POST to the N5 paths answers over HTTP/2;
  - a plain HTTP/1.1 GET to the 29.122 paths still works;
  - `--http2` (Upgrade) GET upgrades to HTTP/2.
- **Still unknown**: which h2c mode the NEF uses. With Upgrade on a POST, the NEF would get an HTTP/1.1 answer.
