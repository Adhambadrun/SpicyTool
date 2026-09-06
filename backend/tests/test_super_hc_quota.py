"""Protected monthly Super HC counter. Synthetic credentials, no live calls."""
from __future__ import annotations

import contextlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from core import auth
from providers import flybasis_session
from providers.flybasis import Flybasis
from services import aggregator


class SuperHcQuotaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.dict(os.environ, {"FLYBASIS_API_KEY": ""}))
        self.patches.enter_context(patch.object(main, "AUTH_ENFORCE", True))
        self.patches.enter_context(patch.object(auth, "verify_token", side_effect=lambda token:
            "owner@example.test" if token == "synthetic-app-session" else None))
        self.patches.enter_context(patch.object(aggregator, "_registry", [Flybasis()]))
        self.configured = self.patches.enter_context(patch.object(flybasis_session, "configured", return_value=True))
        self.quota = self.patches.enter_context(patch.object(flybasis_session, "searches_remaining", new=AsyncMock(return_value=3)))
        self.search = self.patches.enter_context(patch.object(Flybasis, "fetch_raw", new=AsyncMock()))

    async def request(self, token="synthetic-app-session"):
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://app.test") as client:
            return await client.get("/api/v2/super-hc/quota", headers=headers)

    def assert_unknown(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "provider": "Flybasis", "remaining": None, "period": "month", "available": False,
        })

    async def test_private_quota_requires_valid_app_session(self):
        for token in (None, "invalid"):
            with self.subTest(token=token):
                self.assertEqual((await self.request(token)).status_code, 401)
        self.quota.assert_not_awaited()

    async def test_counter_is_monthly_private_and_read_only(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "provider": "Flybasis", "remaining": 3, "period": "month", "available": True,
        })
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("private", response.headers["cache-control"])
        self.quota.assert_awaited_once()
        self.search.assert_not_awaited()

    async def test_zero_is_a_known_count_not_unavailable(self):
        self.quota.return_value = 0
        response = await self.request()
        self.assertEqual(response.json()["remaining"], 0)
        self.assertTrue(response.json()["available"])

    async def test_each_refresh_reads_latest_provider_value(self):
        self.assertEqual((await self.request()).json()["remaining"], 3)
        self.quota.return_value = 2
        self.assertEqual((await self.request()).json()["remaining"], 2)
        self.assertEqual(self.quota.await_count, 2)
        self.search.assert_not_awaited()

    async def test_unconfigured_session_does_not_call_provider(self):
        self.configured.return_value = False
        self.assert_unknown(await self.request())
        self.quota.assert_not_awaited()

    async def test_official_key_does_not_show_an_unrelated_session_quota(self):
        os.environ["FLYBASIS_API_KEY"] = "synthetic-official-key"
        self.assert_unknown(await self.request())
        self.quota.assert_not_awaited()

    async def test_unselected_flybasis_does_not_show_a_quota(self):
        with patch.object(aggregator, "_registry", []):
            self.assert_unknown(await self.request())
        self.quota.assert_not_awaited()

    async def test_invalid_or_missing_count_is_unknown_not_zero(self):
        for value in (None, -1, True, "3", 3.5, {"token": "never-expose-this"}):
            with self.subTest(value=value):
                self.quota.return_value = value
                response = await self.request()
                self.assert_unknown(response)
                self.assertNotIn("never-expose-this", response.text)

    async def test_provider_errors_and_timeouts_do_not_leak_details(self):
        for error in (RuntimeError("private-upstream-token"), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.quota.side_effect = error
                response = await self.request()
                self.assert_unknown(response)
                self.assertNotIn("private-upstream-token", response.text)
        self.search.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
