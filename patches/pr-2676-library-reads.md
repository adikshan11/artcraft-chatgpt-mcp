Title: Fix storytold/artcraft#2676: API keys get 401 on library lists and job history

Branch: cursor/api-key-library-reads-8caf (base main, storytold/artcraft-services)

## Summary
GET /v1/media_files/project/list, /v1/media_files/mesh/list, /v1/media_files/splat/list and GET /v1/jobs/session only read the website session cookie, so a valid ArtCraft API key got 401. They now use the existing API-key-or-cookie lookup. A present Authorization header does not fall through to the cookie. Job history still allows the anonymous visitor cookie when no key is sent. Cookie behavior is otherwise unchanged.

The Cloudflare 1010 block on Python urllib's default User-Agent is not changed here. It is documented in storytold/artcraft in _docs/artcraft_omni_api.md.

## Issue
Closes storytold/artcraft#2676

## Validation
Not compiled or run. The author's machine could not build this workspace (Rust 1.83, workspace needs edition 2024 crates). Please run CI.
