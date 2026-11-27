# Schemas -  API contracts

Versioned contract for the translator's **northbound** API.

| File | Description |
|------|-------------|
| [`http/openapi.yaml`](http/openapi.yaml) | OpenAPI 3.1 spec of both northbound APIs: TS 29.514 `Npcf_PolicyAuthorization` (N5) at `/npcf-policyauthorization/v1`, and TS 29.122 `AsSessionWithQoS` at `/3gpp-as-session-with-qos/v1`. Both `PATCH` bodies are `application/merge-patch+json`, although the generated spec lists `application/json`. |

The translator's **southbound** contract (the Slice Manager REST API it consumes) is owned
by the SM team and lives in `slice-manager/schemas/http/openapi.yaml`.

## Regenerating `http/openapi.yaml`

The spec is generated from the live FastAPI app (`app.main:app`), so it never drifts from the
code. It is committed as an artifact for reviewers and tooling. Regenerate after changing
routes or models:

```bash
# 1. export the schema from the app (needs the project venv)
.venv/bin/python -c "from app.main import app; import json; \
  open('/tmp/openapi.json','w').write(json.dumps(app.openapi(), indent=2))"

# 2. convert JSON -> YAML (any python with PyYAML, e.g. the system one), keeping the header
/usr/bin/python3 -c "import json, yaml; \
  spec = json.load(open('/tmp/openapi.json')); \
  header = ''.join(open('schemas/http/openapi.yaml').readlines()[:3]); \
  open('schemas/http/openapi.yaml','w').write(header + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100))"
```

Or simply browse the interactive docs while the app runs: `http://<host>:8081/docs` (Swagger
UI) and `http://<host>:8081/openapi.json` (raw schema).
