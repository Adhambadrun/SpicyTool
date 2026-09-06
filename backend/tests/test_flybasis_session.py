"""Session-auth regressions. All credentials and upstream responses are synthetic.

Run from the repo root:
    .venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py' -v
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.http_engine import ProviderError
from providers import flybasis_session as session
from providers.flybasis import Flybasis


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "refresh.json"
        self.environ = patch.dict(os.environ, {
            "FLYBASIS_SUPABASE_URL": "https://supabase.test",
            "FLYBASIS_SUPABASE_ANON_KEY": "synthetic-anon",
            "FLYBASIS_REFRESH_TOKEN": "seed-refresh",
            "FLYBASIS_REFRESH_FILE": str(self.store),
            "FLYBASIS_API2_URL": "https://api2.test",
            "FLYBASIS_API_KEY": "",
            "FLYBASIS_EMAIL": "",
            "FLYBASIS_PASSWORD": "",
        })
        self.environ.start()
        session.reset_cache()
        self.calls = []
        self.expected_refresh = "seed-refresh"
        self.auth_count = 0
        self.auth_patch = patch.object(session, "_post", side_effect=self.upstream)
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()
        session.reset_cache()
        self.environ.stop()
        self.tmp.cleanup()

    async def upstream(self, engine, url, **kwargs):
        self.calls.append((url, kwargs))
        # Yield to reproduce simultaneous airport/date searches on a cold cache.
        await asyncio.sleep(0)
        if url.endswith("/auth/v1/token"):
            self.assertEqual(kwargs["headers"]["apikey"], "synthetic-anon")
            if kwargs["params"]["grant_type"] == "refresh_token":
                if kwargs["json"]["refresh_token"] != self.expected_refresh:
                    return httpx.Response(400, json={"error": "refresh token already used"})
            self.auth_count += 1
            self.expected_refresh = f"rotated-refresh-{self.auth_count}"
            return httpx.Response(200, json={
                "access_token": f"access-{self.auth_count}",
                "refresh_token": self.expected_refresh,
                "expires_in": 3600,
            })
        self.assertEqual(url, "https://api2.test/trpc/user.whoami?batch=1")
        # Shape from the HAR: access-token (NOT Authorization) and an empty
        # JSON batch body. Do not copy credentials/account details from a HAR.
        self.assertEqual(kwargs["headers"].get("access-token"), f"access-{self.auth_count}")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs.get("json"), {})
        return httpx.Response(200, json=[{"result": {"data": {
            "maxSearchesRemaining": 4,
        }}}])

    async def test_expired_access_uses_rotated_refresh_not_env_seed(self):
        self.assertEqual(await session.access_token(), "access-1")
        session._cache = ("access-1", 0)
        self.assertEqual(await session.access_token(), "access-2")
        self.assertEqual(self.auth_count, 2)
        self.assertEqual(os.environ["FLYBASIS_REFRESH_TOKEN"], "seed-refresh")

    async def test_parallel_searches_share_one_refresh(self):
        tokens = await asyncio.gather(*(session.access_token() for _ in range(12)))
        self.assertEqual(tokens, ["access-1"] * 12)
        self.assertEqual(self.auth_count, 1)

    async def test_restart_reuses_persisted_rotation_with_env_seed_still_set(self):
        await session.access_token()
        session.reset_cache()
        self.assertEqual(await session.access_token(), "access-2")
        self.assertEqual(self.auth_count, 2)

    async def test_new_env_credential_invalidates_cache_and_old_store(self):
        await session.access_token()
        os.environ["FLYBASIS_REFRESH_TOKEN"] = "replacement-seed"
        self.expected_refresh = "replacement-seed"
        self.assertEqual(await session.access_token(), "access-2")
        self.assertEqual(self.calls[-1][1]["json"]["refresh_token"], "replacement-seed")

    async def test_new_account_does_not_reuse_password_session(self):
        os.environ["FLYBASIS_REFRESH_TOKEN"] = ""
        os.environ["FLYBASIS_EMAIL"] = "first@example.test"
        os.environ["FLYBASIS_PASSWORD"] = "first-password"
        await session.access_token()
        os.environ["FLYBASIS_EMAIL"] = "second@example.test"
        os.environ["FLYBASIS_PASSWORD"] = "second-password"
        self.assertEqual(await session.access_token(), "access-2")
        self.assertEqual(self.calls[-1][1]["params"], {"grant_type": "password"})
        self.assertEqual(self.calls[-1][1]["json"]["email"], "second@example.test")

    async def test_read_only_storage_keeps_rotation_in_memory(self):
        # A directory cannot be replaced by a token file, even when tests run
        # as root. Simulates read-only/ephemeral serverless storage reliably.
        os.environ["FLYBASIS_REFRESH_FILE"] = self.tmp.name
        await session.access_token()
        session._cache = ("access-1", 0)
        self.assertEqual(await session.access_token(), "access-2")

    async def test_persisted_rotation_has_private_permissions(self):
        await session.access_token()
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.store.read_text())["refresh_token"], "rotated-refresh-1")

    async def test_missing_anon_key_disables_session_before_network(self):
        os.environ["FLYBASIS_SUPABASE_ANON_KEY"] = ""
        self.assertFalse(session.configured())
        self.assertFalse(Flybasis().enabled)
        with self.assertRaisesRegex(ProviderError, "FLYBASIS_SUPABASE_ANON_KEY"):
            await session.access_token()
        self.assertEqual(self.auth_count, 0)

    async def test_quota_matches_captured_header_and_body(self):
        self.assertEqual(await session.searches_remaining(), 4)
        self.assertEqual(self.auth_count, 1)

    async def test_quota_is_best_effort_when_login_fails(self):
        self.expected_refresh = "different"
        self.assertIsNone(await session.searches_remaining())

    async def test_malformed_auth_response_is_safe_and_not_cached(self):
        cases = [
            httpx.Response(200, text="not JSON"),
            httpx.Response(200, json=[]),
            httpx.Response(200, json={"access_token": {"secret": "never-echo-me"}}),
            httpx.Response(200, json={"access_token": "never-echo-me", "expires_in": "NaN"}),
            httpx.Response(200, json={"access_token": "never-echo-me", "expires_in": -1}),
        ]
        for response in cases:
            with self.subTest(response=response), patch.object(session, "_post", return_value=response):
                session.reset_cache()
                with self.assertRaises(ProviderError) as raised:
                    await session.access_token()
                self.assertNotIn("never-echo-me", str(raised.exception))
                self.assertIsNone(session._cache)

    async def test_rapidapi_key_does_not_block_a_valid_flybasis_session(self):
        os.environ["FLYBASIS_API_KEY"] = "0123456789mshabcdefghijjsn1234567890"
        self.assertTrue(Flybasis().enabled)
        self.assertIsNone(Flybasis().disabled_reason())

    async def test_session_token_not_misplaced_rapidapi_key_reaches_socket(self):
        from providers.base import SearchQuery

        os.environ["FLYBASIS_API_KEY"] = "0123456789mshabcdefghijjsn1234567890"
        client = FakeSocket()
        with patch("socketio.AsyncClient", return_value=client):
            await Flybasis().fetch_raw(SearchQuery("JFK", "LHR", "2026-10-05"), None)
        self.assertEqual(client.auth, {"token": "access-1"})
        self.assertEqual(client.events[0][0], "search")

    async def test_socket_errors_cannot_echo_session_tokens(self):
        from providers.base import SearchQuery

        for failure in ("connect", "error-event"):
            with self.subTest(failure=failure):
                client = FakeSocket(failure=failure)
                with patch("socketio.AsyncClient", return_value=client):
                    with self.assertRaises(ProviderError) as raised:
                        await Flybasis().fetch_raw(SearchQuery("JFK", "LHR", "2026-10-05"), None)
                self.assertNotIn("access-1", str(raised.exception))
                self.assertIn("[redacted]", str(raised.exception))

    async def test_corrupt_store_does_not_break_explicit_configuration(self):
        self.store.write_text("{partial JSON")
        self.assertTrue(session.configured())
        self.assertEqual(await session.access_token(), "access-1")

    async def test_legacy_plain_token_file_works_when_no_env_seed_is_set(self):
        os.environ["FLYBASIS_REFRESH_TOKEN"] = ""
        self.store.write_text("seed-refresh")
        self.assertEqual(await session.access_token(), "access-1")
        session.reset_cache()
        self.assertEqual(await session.access_token(), "access-2")

    async def test_official_socket_key_does_not_require_supabase(self):
        os.environ["FLYBASIS_API_KEY"] = "official-synthetic-token"
        os.environ["FLYBASIS_SUPABASE_ANON_KEY"] = ""
        self.assertTrue(Flybasis().enabled)
        self.assertIsNone(Flybasis().disabled_reason())


class FakeSocket:
    def __init__(self, failure=None):
        self.handlers = {}
        self.auth = None
        self.events = []
        self.failure = failure

    def on(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler
        return register

    async def connect(self, url, *, auth, **kwargs):
        self.auth = auth
        if self.failure == "connect":
            raise RuntimeError("Rejected token " + auth["token"])
        await self.handlers["connect"]()
        if self.failure == "error-event":
            await self.handlers["error"]({"message": "Rejected token " + auth["token"]})
        else:
            await self.handlers["data"]({"data": {"awd": []}})
        await self.handlers["disconnect"]()

    async def emit(self, name, payload):
        self.events.append((name, payload))

    async def disconnect(self):
        pass


if __name__ == "__main__":
    unittest.main()
