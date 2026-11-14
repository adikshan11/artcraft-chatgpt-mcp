# Reproducible builds

A rebuild installs the same code that CI tested.

- `Dockerfile` names the base image by digest, not by the `python:3.12-slim` tag.
- `requirements.txt` holds the ranges we accept. `requirements.lock` holds the exact versions and
  hashes, made from it on Linux with Python 3.12. Docker and CI install the lock with
  `pip install --require-hashes`, so a changed or substituted file is refused.
- FFmpeg and the fonts still come from the Debian package index, at whatever version it serves
  that day. They are not pinned.

## Updating

Run this from the repository root. It uses the same image the Dockerfile does:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b \
  sh -c 'pip install -q pip-tools==7.4.1 && pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-header --output-file=requirements.lock requirements.txt'
```

That keeps every version already in the lock. To move to the newest versions that
`requirements.txt` allows, add `--upgrade` to the `pip-compile` command, or `--upgrade-package NAME`
(repeatable) to move only one. When you raise a minimum in `requirements.txt`, regenerate the lock in
the same change; `tests/test_resilience.py` fails when a locked version falls outside the ranges.

To move the base image, take the new digest of `python:3.12-slim` from Docker Hub, change it in both
`FROM` lines and in the command above, and rebuild the lock.
