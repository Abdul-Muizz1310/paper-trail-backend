# Spec 10 — Platform token contract v2

## Goal

paper-trail's copy of the workspace contract `docs/specs/platform-contract-v2.md` (decision DR-0033,
which supersedes DR-0009 and extends DR-0008 and DR-0030). Every muizz-lab backend verifies the same
token the same way; this file pins what paper-trail's copy does, and its tests carry the contract's
conformance cases 1–14 verbatim. It supersedes the verification rules of
[spec 09](09-platform-token.md), which stays as history.

The service id is the constant `paper-trail` (`platform/token_verifier.SERVICE_ID`), not configuration.

## 1. Trusted keys

| Key | Configured by | The token must carry |
|---|---|---|
| bastion's | `BASTION_SIGNING_KEY_PUBLIC` (base64 SPKI DER), else fetched from `BASTION_PUBLIC_KEY_URL` (JSON field `publicKey`, cached 1 h) | `iss == "bastion"` |
| paper-trail-frontend's | `PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC` (base64 SPKI DER) | `iss == "paper-trail-frontend"` and `role == "frontend"` |

The key decides which `iss` is allowed, never the claim: a frontend-signed token claiming
`iss: "bastion"` is rejected, and so is the reverse. A key value has a byte-order mark and edge
whitespace stripped; empty means absent; a value that is not an Ed25519 SPKI public key logs one
WARNING naming the variable (never the value) and counts as absent.

## 2. Verification (pure: `platform/token_verifier.py`)

`verify_token(token, keys, now) -> Principal` or `TokenRejectedError(check)`. Checks, in order; the first
that fails names the rejection:

| # | Check | Rejection |
|---|---|---|
| 1 | a compact JWS whose header and payload are JSON objects | `malformed` |
| 2 | header `alg == "EdDSA"` (so `none` and `HS256` never reach a key) | `alg` |
| 3 | the signature verifies against one trusted key | `signature` |
| 4 | `iss` equals that key's issuer | `iss` |
| 5 | `role`, when present, is a string; frontend key: `role == "frontend"` | `role` |
| 6 | `aud` is a string equal to `paper-trail` (missing, another id, or a list → rejected) | `aud` |
| 7 | `exp` and `iat` are integers | `exp` / `iat` |
| 8 | `exp + 10 > now` and `iat - 10 <= now` (10 s leeway) | `exp` / `iat` |
| 9 | `exp - iat <= 300` | `lifetime` |
| 10 | `sub` is a non-empty string | `sub` |

`Principal = (iss, sub, role, aud)`; `role` is `""` when absent. paper-trail authenticates only: it does
not gate on bastion's `role`, and `cap` is informational.

## 3. The middleware (imperative shell: `platform/platform_token.py`)

The token is read from `X-Platform-Token`, else from `Authorization: Bearer`. Per request, in order:

1. **Public route** (method `GET` or `HEAD` and one of the paths below) → passes; no token is read.
2. **No trusted key configured** → `503 {"detail": "platform_auth_unconfigured"}` (fail closed),
   except under the test settings (`APP_ENV=test`), where the request passes as anonymous.
3. **No token** → `401 {"detail": "missing_platform_token"}`; with `DEMO_MODE=true` the request passes
   as anonymous and a WARNING is logged. `DEMO_MODE` defaults to `false`.
4. **Token present** → verified (§2). Any failure → `401 {"detail": "invalid_platform_token"}` and a
   WARNING naming the failed check, never the token — also with `DEMO_MODE=true`: the switch only
   tolerates a *missing* token.
5. Verified → passes with `request.state.platform_principal`.

`DEMO_MODE` and `APP_ENV` are read per request, so tests can switch them.

**Public routes** (method-aware: a `POST` to one of these paths is not public):
`/health`, `/version`, `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json`, `/metrics` (it keeps
its own `METRICS_TOKEN` bearer), `/platform/receipt-public-key`, `/debates/{id}/transcript.json`,
`/debates/{id}/transcript.md`. These are the verification surfaces: anyone can check a receipt
without asking us. Everything else is protected, including `GET /debates`, `GET /debates/{id}`, the
event stream, `POST /debates` and `POST /platform/debate`.

## 4. `POST /platform/debate` stays bastion-only

It is bastion's integrated-demo entry point and the most expensive call. The middleware authenticates
the request; the endpoint then requires `principal.iss == "bastion"`: a valid frontend token gets
`401 {"detail": "invalid_platform_token"}`, an anonymous request (DEMO_MODE, or the test settings
without a key) `401 {"detail": "missing_platform_token"}`.

## 5. Rate limiting

The paid endpoints' bucket key (`core/rate_limit.bucket_key`), per the contract as amended on
2026-10-20:

| Caller | Bucket |
|---|---|
| bastion's key | `"bastion:{sub}"` (`sub` is the visitor pseudonym) |
| the frontend's key | `"paper-trail-frontend:{client address}"`: the frontend's `sub` is the same for every visitor, so keying on it would put the whole site in one bucket; its proxy forwards exactly one client address in `X-Forwarded-For`, which it sets itself |
| anonymous | `"{client address}"` |

The client address is the leftmost non-empty `X-Forwarded-For` entry, then `X-Real-IP`, then the
socket peer, then `"unknown"`. (The first draft keyed every authenticated request on `iss:sub`.)

## 6. CORS

CORS is the outermost middleware: a preflight never reaches the token check, and a 401 or 503 still
carries `Access-Control-Allow-Origin` for an allowed origin, so the browser can read it. Allowed
request headers: `content-type`, `x-platform-token`, `x-request-id`, `authorization`,
`last-event-id`. Methods: `GET`, `POST`, `OPTIONS`.

## 7. Rollout

Per the workspace contract §5 this lands on branch `contract-v2` and is merged by the orchestrator,
backends first. Until the minters send `iss` and `aud`, their current tokens fail check 4 or 6.

## Test cases

### Conformance (workspace contract §3, verbatim) — `tests/unit/test_platform_contract_v2.py`

1. valid bastion token, `aud` = own id ⇒ authenticated
2. `aud` = another service's id ⇒ 401 invalid
3. `aud` missing ⇒ 401 invalid; `aud` as a list ⇒ 401 invalid
4. `iss` = `"bastion"` but signed with the frontend key ⇒ 401 invalid
5. frontend-key token with `role != "frontend"` ⇒ 401 invalid
6. expired 5 s ago ⇒ authenticated (leeway); expired 15 s ago ⇒ 401 invalid
7. `iat` 5 s in the future ⇒ authenticated; 15 s in the future ⇒ 401 invalid
8. `alg` = `none` or `HS256` ⇒ 401 invalid
9. tampered payload ⇒ 401 invalid
10. no token, `DEMO_MODE=false` ⇒ 401 missing; no token, `DEMO_MODE=true` ⇒ proceeds, WARNING logged
11. present-but-invalid token with `DEMO_MODE=true` ⇒ still 401 invalid
12. no trusted key configured (non-test settings) ⇒ 503 on a protected route, 200 on `/health`
13. every public route answers without a token
14. rate-limit key is `bastion:{sub}` for a bastion token and `{iss}:{forwarded address}` for a frontend
    token (two visitors through the same frontend land in different buckets)

### paper-trail's own cases

- **C15** a valid frontend token (`iss "paper-trail-frontend"`, `aud "paper-trail"`, role `frontend`)
  is authenticated; one with `iss "bastion"` signed by bastion's key but `role "frontend"` is too
  (bastion's role is not gated).
- **C16** a token signed by bastion's key that claims `iss "paper-trail-frontend"` ⇒ 401 invalid.
- **C17** `exp - iat = 301` ⇒ 401 invalid (`lifetime`); `300` ⇒ authenticated.
- **C18** an unknown key's signature ⇒ 401 invalid; a token without `sub` ⇒ 401 invalid.
- **C19** the WARNING for an invalid token names the failed check and never contains the token.
- **C20** the public list is method-aware: `POST /debates/{id}/transcript.json` without a token ⇒
  401 missing; and it is exact: `GET /debates/{id}` and `GET /debates/{id}/stream` without a token ⇒
  401 missing.
- **C21** `X-Platform-Token` is read first; a Bearer token is used only when that header is absent.
- **C22** `POST /platform/debate`: bastion token ⇒ 200; valid frontend token ⇒ 401 invalid;
  `DEMO_MODE=true` without a token ⇒ 401 missing.
- **C23** test settings without a key ⇒ a protected route passes as anonymous; with `APP_ENV`
  anything else ⇒ 503.
- **C24** the rate-limit key falls back to the forwarded client address for an anonymous request; a
  frontend token's bucket uses the leftmost entry of a chain, and the socket peer without a header.
- **C25** CORS: a preflight asking for `x-platform-token` from an allowed origin is answered by CORS
  (200, the header allowed) without a token; a 401 answer to an allowed origin carries
  `access-control-allow-origin`.
- **C26** key configuration: a key with a BOM and CRLF still parses; a malformed key logs one WARNING
  naming the variable without its value and counts as absent; bastion's URL-fetched key is cached.
- **C27** `DEMO_MODE` defaults to `false`.
