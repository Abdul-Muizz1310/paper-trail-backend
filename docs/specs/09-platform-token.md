# Spec 09 — Platform token: bastion's key and the frontend's own key

> **Superseded 2026-10-20** by [spec 10 — platform token contract v2](10-platform-contract-v2.md) on branch `contract-v2`: `iss`/`aud` checks, fail-closed 503 without a key, public receipt routes, `DEMO_MODE` tolerating only a missing token. Kept as history.

## Goal

`DEMO_MODE=false` with bastion's public key set makes the `X-Platform-Token`
middleware (`src/paper_trail/platform/platform_token.py`) demand a
bastion-minted EdDSA JWT on every non-exempt route. Only bastion could mint
one, so paper-trail-frontend, which calls this service directly, got 401 on
every data call.

The frontend now holds its own Ed25519 key, mints a 60-second token
server-side and proxies the browser's calls, including the debate event stream
(DR-0030). The middleware trusts **bastion's key and the frontend's key**, and
accepts a frontend-key token only when it is scoped to this service with the
frontend role. `POST /platform/debate` stays bastion-only.

## Contract

| | bastion token | frontend token |
|---|---|---|
| key | `BASTION_SIGNING_KEY_PUBLIC` (base64 SPKI DER) or the cached `BASTION_PUBLIC_KEY_URL` fetch | `PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC` (base64 SPKI DER), env only, no URL fetch |
| `alg` | `EdDSA` | `EdDSA` |
| `exp` | required, in the future (10 s leeway) | required, in the future (10 s leeway) |
| claims checked | none beyond `exp` | `service == "paper-trail"` and `role == "frontend"` |
| accepted by the middleware | yes | yes |
| accepted by `POST /platform/debate` (`Authorization: Bearer`) | yes | **no** |

Frontend tokens look like
`{"alg":"EdDSA","typ":"JWT","kid":"paper-trail-frontend-2026-10"}` /
`{"sub":"paper-trail-frontend","role":"frontend","service":"paper-trail","iat":…,"exp":iat+60,"jti":…}`.
`kid`, `sub`, `jti` are not checked. Both paths, and the `/platform/debate`
bearer check, allow a **10 s leeway** on `exp` and `iat` (see the 2026-10-16
note below).

## Invariants

1. `DEMO_MODE` on → the middleware passes every request (unchanged).
2. Exempt paths (`/health`, `/version`, `/metrics`, `/openapi.json`,
   `/platform/receipt-public-key`, `/docs*`, `/redoc*`) pass without a token
   (unchanged).
3. Neither key configured → the middleware fails open (unchanged, documented).
4. At least one key configured → the middleware enforces. A token is valid when
   it verifies against bastion's key, **or** against the frontend key with the
   frontend claims above; otherwise 401 `{"error": "invalid platform token"}`.
5. Only one key configured → only that key is trusted.
6. The frontend key value is read with a leading byte-order mark and edge
   whitespace stripped. Empty means absent. A value that is not base64 of an
   Ed25519 SPKI public key is **malformed**: one warning naming
   `PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC` (never the value), and the key is
   treated as absent. It never crashes the app or a request.
7. `POST /platform/debate` is bastion's integrated-demo entry point and spends
   the most per call. Its bearer check (`core/platform_auth.verify_platform_token`
   → `platform_token.verify_platform_jwt`) is a separate function from the
   middleware's and keeps trusting **bastion's key only**, failing closed when
   that key is absent. A frontend-key token passes the middleware on that path
   but is rejected by the endpoint with 401 `{"detail": "invalid platform token"}`.
8. bastion's key resolution (env, then cached URL, sync and async) is
   unchanged; the only change on that path is that `exp` is now required, in
   the middleware and in `verify_platform_jwt`. bastion's minter
   (`bastion/src/lib/gateway/jwt.ts`) always sets `exp` to 60 s.

**2026-10-16 — clock skew.** Tokens are minted on Vercel (frontend) or bastion
and verified on Render, and PyJWT rejects an `iat` in the future, so a minter
clock even a second ahead of this host's would 401 a fresh token. Every decode
(middleware, both minters, and `verify_platform_jwt`, which shares it) now uses
`leeway=10`: an `iat` up to 10 s ahead and an `exp` up to 10 s past are
accepted; beyond that the token is rejected. Against a 60 s lifetime this
widens the replay window by at most 10 s.

## Test cases

Middleware, pass:

- [x] Frontend token (own service, frontend role, live `exp`) → 200.
- [x] bastion token → 200 with both keys configured.
- [x] Only the frontend key configured → frontend token 200, missing token 401,
      bastion-signed token 401.
- [x] Frontend key with a BOM and a trailing CRLF → still parsed.
- [x] Neither key → fails open (existing).
- [x] `DEMO_MODE` → bypass, also with the frontend key configured (existing).
- [x] Exempt path → 200 without a token with the frontend key configured.

Middleware, failure:

- [x] Expired frontend token → 401.
- [x] Token signed by an unknown key → 401 with both keys configured.
- [x] Frontend-key token with `service: "magpie"` → 401.
- [x] Frontend-key token with `role: "admin"` → 401.
- [x] Frontend-key token without `exp` → 401.
- [x] bastion-key token without `exp` → 401.
- [x] Unsigned (`alg: none`) token with frontend claims → 401.
- [x] Malformed frontend key (not base64; base64 of non-key bytes; a P-256 key)
      → one warning naming the variable and not containing the value; bastion
      tokens still accepted; with no bastion key the middleware fails open.

Clock skew (middleware, both minters):

- [x] `iat` 5 s in the future → 200; `iat` 30 s in the future → 401.
- [x] `exp` 5 s in the past → 200; `exp` 30 s in the past → 401.

`/platform/debate`:

- [x] `verify_platform_token` rejects a frontend-key token with both keys
      configured, and fails closed with only the frontend key configured.
- [x] `verify_platform_token` rejects a bastion token without `exp`.
- [x] `verify_platform_token` with bastion tokens: `iat` 5 s ahead and `exp`
      5 s past accepted; `iat` 30 s ahead and `exp` 30 s past rejected.
- [x] Composed: with both keys configured, a frontend token in both
      `X-Platform-Token` and `Authorization` gets 401 from the endpoint; a
      bastion token in both gets 200.

## Rate limiting behind the proxy (unchanged)

`core/rate_limit.client_identifier` keys the paid-endpoint limiter on the
leftmost `X-Forwarded-For` entry, then `X-Real-IP`, then the socket peer. The
container runs uvicorn with `--proxy-headers --forwarded-allow-ips '*'`, so
`request.client.host` is also the leftmost `X-Forwarded-For` entry. The
frontend proxy sets `X-Forwarded-For` to the visitor address its platform
reported, so both see the visitor rather than the proxy. The limiter is a route
dependency, so it runs after this middleware: an untokened caller is rejected
before it can pick a bucket.
