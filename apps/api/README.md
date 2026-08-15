# jobcopilot-api

FastAPI backend. See `docs/TECH_DESIGN.md` for architecture.

## Local dev

```bash
uv sync
uv run uvicorn jobcopilot_api.main:app --reload --port 8000
curl http://localhost:8000/v1/health
```
