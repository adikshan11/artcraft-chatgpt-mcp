Title: Fix #196: Sign in with ArtCraft (OAuth) for third-party apps and assistant connectors

Branch: cursor/oauth-connector-sign-in-8caf (base main, storytold/artcraft-services)
This branch contains the API-key library reads commit too. Merge the 2676 PR first, or review this one as the whole change.

## Summary
- Authorization code with PKCE (S256 required), dynamic client registration (RFC 7591 subset), consent page, refresh token rotation, token revocation, and a page at /oauth/grants to revoke an app.
- Discovery at /.well-known/oauth-authorization-server.
- Scopes: generate, library:read, library:write. API keys stay unscoped.
- Access and refresh tokens are opaque. Only SHA-256 hashes are stored.
- An artcraft_oauth_at_ token is accepted on /v1/omni_api/* (generate, upload, job status), omni_gen generate, the library lists, and GET /v1/jobs/session, when it has the scope that route needs. A token without the scope gets 403. An unknown or expired token gets 401 and never falls through to the cookie.
- Migration 2026-11-01-150000-0000_create_oauth_connector, plus a copy in migrations_squashed like the MCP sessions migration. Drop the squashed copy if the pipeline applies it on top of migrated databases.

## Known limits
- Registration is open and has no rate limit. The consent page shows the app name and the exact redirect address.
- Refresh tokens rotate, but replaying an old one does not revoke the whole grant.
- /oauth/revoke does not authenticate the client. Knowing the token is enough.
- Consent and revoke POSTs are refused unless Origin matches the host. The consent page cannot be framed. Token responses send Cache-Control: no-store.
- Tokens are opaque and introspected from MySQL, not JWT.
- omni_gen generate (including the website upload path) requires generate, not library:write.
- OAuth calls on omni_gen are recorded as PlatformType::ApiKey.
- The routes are not in the OpenAPI list.
- The login step tells the person to sign in to ArtCraft in the same browser and reload. No login URL is invented.

## Issue
Closes storytold/artcraft-services#196

## Validation
Not compiled or run. The author's machine could not build this workspace (Rust 1.83, workspace needs edition 2024 crates). The PKCE challenge was checked against the RFC 7636 appendix B vector in Python. Please run CI and the migration on a test database.
