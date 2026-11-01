These diffs are for storytold/artcraft-services#196 and storytold/artcraft#2676.

This branch is not a pull request. The token cannot fork or push to those Storytold repositories (HTTP 403).

- `0001` through `0004` apply in order on a fork of storytold/artcraft-services. `0001` is the library API-key reads. `0002` to `0004` are the OAuth connector, its hardening, and the rate limit and revoke changes.
- `0005` applies on a fork of storytold/artcraft. It documents the Cloudflare user-agent block. It does not change the Cloudflare rule.
- `pr-2676-library-reads.md` and `pr-196-oauth-connector.md` hold the PR titles and bodies. They follow the storytold/soundcraft#1225 format: `Fix #N: title`, then an `## Issue` section with `Closes owner/repo#N`.
- `issue-oauth-followup.md` is the follow-up issue for the OpenAPI listing and the fixed issuer host. Open it on storytold/artcraft-services.
