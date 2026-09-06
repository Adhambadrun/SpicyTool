"""The live verifier must not assert mock flights or spend multiple searches."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.http_engine import ProviderError
from providers import flybasis_session
from tools import verify_flybasis_socket as verifier


class LiveVerifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environ = patch.dict(os.environ, {"FLYBASIS_API_KEY": "synthetic-official-key"})
        self.environ.start()
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()

    def tearDown(self):
        self.redirect.__exit__(None, None, None)
        self.environ.stop()

    async def test_live_accepts_real_result_counts_and_sends_one_query(self):
        for count in (1, 3, 17):
            with self.subTest(count=count):
                result = SimpleNamespace(
                    status=SimpleNamespace(ok=True, latency_ms=10), results=[object()] * count,
                )
                with patch.object(verifier, "_search", new=AsyncMock(return_value=result)) as search:
                    self.assertEqual(await verifier.run_live_check(
                        origin="SFO", destination="NRT", date="2026-10-05", cabin="business",
                    ), 0)
                    search.assert_awaited_once_with(
                        origin="SFO", destination="NRT", date="2026-10-05", cabin="business",
                    )

    async def test_live_empty_success_is_explicit_not_a_fake_flight(self):
        result = SimpleNamespace(status=SimpleNamespace(ok=True, latency_ms=10), results=[])
        with patch.object(verifier, "_search", new=AsyncMock(return_value=result)) as search:
            self.assertEqual(await verifier.run_live_check(), 0)
            search.assert_awaited_once()
        self.assertIn("no award availability", self.output.getvalue())

    async def test_live_failure_does_not_echo_upstream_credentials(self):
        result = SimpleNamespace(status=SimpleNamespace(ok=False, error="token=never-print-this"), results=[])
        with patch.object(verifier, "_search", new=AsyncMock(return_value=result)) as search:
            self.assertEqual(await verifier.run_live_check(), 1)
            search.assert_awaited_once()
        self.assertNotIn("never-print-this", self.output.getvalue())

    async def test_auth_only_checks_quota_but_never_searches(self):
        with patch.dict(os.environ, {"FLYBASIS_API_KEY": ""}), \
             patch.object(flybasis_session, "configured", return_value=True), \
             patch.object(flybasis_session, "access_token", new=AsyncMock(return_value="private-access")), \
             patch.object(flybasis_session, "searches_remaining", new=AsyncMock(return_value=0)), \
             patch.object(verifier, "_search", new=AsyncMock()) as search:
            self.assertEqual(await verifier.run_live_check(auth_only=True), 0)
            search.assert_not_awaited()
        self.assertIn("searches remaining: 0", self.output.getvalue())
        self.assertIn("No award search", self.output.getvalue())
        self.assertNotIn("private-access", self.output.getvalue())

    async def test_auth_only_unknown_quota_is_not_success(self):
        with patch.dict(os.environ, {"FLYBASIS_API_KEY": ""}), \
             patch.object(flybasis_session, "configured", return_value=True), \
             patch.object(flybasis_session, "access_token", new=AsyncMock(return_value="private-access")), \
             patch.object(flybasis_session, "searches_remaining", new=AsyncMock(return_value=None)), \
             patch.object(verifier, "_search", new=AsyncMock()) as search:
            self.assertEqual(await verifier.run_live_check(auth_only=True), 1)
            search.assert_not_awaited()

    async def test_auth_only_error_is_redacted_and_never_searches(self):
        with patch.dict(os.environ, {"FLYBASIS_API_KEY": ""}), \
             patch.object(flybasis_session, "configured", return_value=True), \
             patch.object(flybasis_session, "access_token", new=AsyncMock(side_effect=ProviderError("secret-token"))), \
             patch.object(verifier, "_search", new=AsyncMock()) as search:
            self.assertEqual(await verifier.run_live_check(auth_only=True), 1)
            search.assert_not_awaited()
        self.assertNotIn("secret-token", self.output.getvalue())

    async def test_official_key_auth_only_cannot_accidentally_spend_a_search(self):
        with patch.object(verifier, "_search", new=AsyncMock()) as search:
            self.assertEqual(await verifier.run_live_check(auth_only=True), 2)
            search.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
