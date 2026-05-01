# @jobcopilot/schemas

Shared TypeScript types generated from the FastAPI OpenAPI spec.

```bash
# 1. start the API (in another terminal)
uv run --project apps/api uvicorn jobcopilot_api.main:app --port 8000

# 2. regenerate
pnpm gen:api

# 3. verify no drift in CI
git diff --exit-code packages/schemas/src/api.ts
```

`src/api.ts` is **auto-generated**. Do not edit by hand.
