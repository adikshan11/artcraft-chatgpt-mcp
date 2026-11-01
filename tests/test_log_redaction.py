# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Logging redaction must hide secrets without breaking uvicorn's access line."""

import io
import logging
import os
import unittest
from unittest.mock import patch

import uvicorn.logging

import log_redaction


def _logger(name, formatter):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


class LogRedactionTests(unittest.TestCase):
    def test_access_log_keeps_its_five_fields_and_redacts_a_token_in_the_path(self):
        log_redaction.install()
        logger, stream = _logger(
            "test.access.redaction",
            uvicorn.logging.AccessFormatter(
                '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                use_colors=False,
            ),
        )
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4In0.signature1"
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.0.8:1",
            "GET",
            f"/health?access_token={token}",
            "1.1",
            200,
        )
        line = stream.getvalue()
        self.assertIn("GET", line)
        self.assertIn("200", line)
        self.assertIn("/health", line)
        self.assertNotIn(token, line)
        self.assertIn("[redacted]", line)

    def test_a_bearer_argument_is_redacted_in_the_formatted_line(self):
        log_redaction.install()
        logger, stream = _logger("test.message.redaction", logging.Formatter("%(message)s"))
        logger.info("calling %s", "Bearer super-secret-token")
        line = stream.getvalue()
        self.assertNotIn("super-secret-token", line)
        self.assertIn("Bearer [redacted]", line)

    def test_a_process_secret_is_redacted_from_the_formatted_line(self):
        log_redaction.install()
        logger, stream = _logger("test.secret.redaction", logging.Formatter("%(message)s"))
        secret = "grant-key-value-0123456789"
        with patch.dict(os.environ, {"GRANT_KEY": secret}):
            logger.info("key is %s", secret)
        line = stream.getvalue()
        self.assertNotIn(secret, line)
        self.assertIn("[redacted]", line)

    def test_exception_text_is_redacted(self):
        log_redaction.install()
        logger, stream = _logger("test.exception.redaction", logging.Formatter("%(message)s"))
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ5In0.signature2"
        try:
            raise RuntimeError(f"upstream said {token}")
        except RuntimeError:
            logger.exception("call failed")
        line = stream.getvalue()
        self.assertNotIn(token, line)
        self.assertIn("[redacted]", line)
        self.assertIn("RuntimeError", line)
