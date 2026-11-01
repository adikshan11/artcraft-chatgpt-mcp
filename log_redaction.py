# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Keep tokens and secrets out of log lines.

File contents are not logged by the tools. This filter is the backstop for
values that must not appear even if a message interpolates one: JWTs, bearer
tokens, ArtCraft job tokens, and the process's own secrets.
"""

import logging
import os
import re
import traceback

_SECRET_ENV = (
    "GRANT_KEY",
    "S3_SECRET_ACCESS_KEY",
    "S3_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ACCESS_KEY_ID",
    "ARTCRAFT_API_KEY",
    "GITHUB_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
)

# A JWT is three base64url segments. Bearer values and ArtCraft job tokens are
# redacted as a class so a new call site cannot leak one by logging a header.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_JOB = re.compile(r"\bjinf_[A-Za-z0-9_-]+\b")


def redact(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    for name in _SECRET_ENV:
        value = os.environ.get(name) or ""
        if len(value) >= 8 and value in text:
            text = text.replace(value, "[redacted]")
    text = _JWT.sub("[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _JOB.sub("[redacted]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_redacted", False):
            return True
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        record._redacted = True
        return True


def install() -> None:
    """Redact every record a handler emits, including ones that propagated."""
    if getattr(logging.Handler.handle, "_redacts", False):
        return
    original = logging.Handler.handle

    def handle(self, record):
        RedactingFilter().filter(record)
        return original(self, record)

    handle._redacts = True
    logging.Handler.handle = handle
