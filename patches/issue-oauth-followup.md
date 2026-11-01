Title: OAuth sign-in follow-up: OpenAPI listing and a fixed issuer host (see #196)
Repo: storytold/artcraft-services

Follow-up to #196. Two parts of the OAuth sign-in work need a Storytold decision or a build, so they are not in the patches.

The patches are on a branch because the author cannot fork or push to this repo: https://github.com/adikshan11/bridgecraft/tree/cursor/artcraft-services-patches-8caf/patches

## 1. List the OAuth routes in OpenAPI
The routes are `GET /.well-known/oauth-authorization-server`, `POST /oauth/register`, `GET` and `POST /oauth/authorize`, `POST /oauth/token`, `POST /oauth/revoke`, `GET /oauth/grants`, and `POST /oauth/grants/{client_token}/revoke`. They are not in `docs/api_doc.rs`. Adding them needs `#[utoipa::path]` on each handler and schema types for the register and token bodies. This should be done by someone who can run the docs build.

## 2. Fixed issuer host
The discovery document builds `issuer` from the request host. Production should return one fixed value, for example the public API origin, from a config setting. The discovery response is already `Cache-Control: no-store`. Which value and which setting name to use is Storytold's call.

## Not yet checked
The patches were written without a compiler (the author's machine has Rust 1.83 and the workspace needs newer). They need CI and the migration run on a test database.
