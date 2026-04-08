# Spec 99 — Platform integration endpoint

## Goal

A single synchronous endpoint `POST /platform/debate` used by the bastion control plane's "Run Integrated Demo" workflow. Runs a capped (max 3 rounds) debate and returns the verdict plus a transcript URL. Guarded by an `X-Platform-Token` JWT unless `DEMO_MODE=true`.

## Contract

**Request**
```json
POST /platform/debate
Content-Type: application/json
X-Platform-Token: <Ed25519-signed JWT or anything in demo mode>

{ "claim": "string ≤2000 chars", "max_rounds": 3 }
```

**Response 200**
```json
{
  "debate_id": "UUID",
  "transcript_url": "https://paper-trail-backend.onrender.com/debates/{id}/transcript.md",
  "verdict": "TRUE|FALSE|INCONCLUSIVE",
  "confidence": 0.87,
  "rounds_run": 2
}
```

## Invariants

- `max_rounds` is clamped to `min(max_rounds or 3, 3)` — bastion calls must stay fast.
- In `DEMO_MODE=false`, a missing or invalid `X-Platform-Token` returns 401.
- In `DEMO_MODE=true`, any value (or no header) is accepted; logs emit `demo_mode_accepted=true`.
- The endpoint blocks until the graph terminates (no SSE; synchronous).
- The `transcript_url` is absolute (reads `PUBLIC_BASE_URL` env var).

## Test cases

1. Demo mode + valid claim → 200 with all expected fields.
2. Demo mode + empty claim → 422.
3. Demo mode + `max_rounds=10` → served with max_rounds clamped to 3 (assert via `rounds_run ≤ 3`).
4. Production mode + missing token → 401.
5. Production mode + malformed token → 401.
6. Production mode + valid token → 200.
7. `transcript_url` round-trips: GET on it returns markdown.
8. Request includes `X-Request-Id` propagation.
9. Graph failure mid-debate → 500 with a clean error envelope, status row set to `error`.

## Acceptance

- Endpoint wired in `api/routers/platform.py`.
- JWT validation in `core/platform_auth.py` (stub verifier OK for v0.1; real Ed25519 from bastion's public key in a later phase).
- One integration test per case above.
