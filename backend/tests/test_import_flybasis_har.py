"""HAR setup uses synthetic captures only; no real account data in fixtures."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import import_flybasis_har as importer


def entry(refresh="fresh-response-token", when="2026-09-06T21:03:03Z", path="token"):
    return {
        "startedDateTime": when,
        "request": {
            "method": "POST",
            "url": f"https://sb.flybasis.com/auth/v1/{path}",
            "headers": [{"name": "ApIkEy", "value": "synthetic-anon"}],
            "postData": {"text": json.dumps({"refresh_token": "spent-request-token"})},
        },
        "response": {
            "status": 200,
            "content": {"text": json.dumps({
                "access_token": "synthetic-short-lived-access",
                "refresh_token": refresh,
                "user": {"id": "synthetic-user", "email": "private@example.test"},
            })},
        },
    }


def archive(*entries):
    return {"log": {"entries": list(entries)}}


class HarImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.har = self.root / "private.har"
        self.output = self.root / ".env"
        self.har.write_text(json.dumps(archive(entry())))

    def tearDown(self):
        self.tmp.cleanup()

    def test_uses_latest_response_not_spent_request_even_if_entries_reordered(self):
        old = entry("older-response", "2026-09-06T21:02:51Z", "verify")
        session = importer.extract_session(archive(entry(), old))
        self.assertEqual(session.refresh_token, "fresh-response-token")
        self.assertEqual(session.anon_key, "synthetic-anon")
        self.assertNotIn("access_token", session.settings())
        self.assertNotIn("private@example.test", repr(session))
        self.assertNotIn("fresh-response-token", repr(session))

    def test_accepts_base64_response_content(self):
        row = entry(path="verify")
        content = row["response"]["content"]
        content["encoding"] = "base64"
        content["text"] = base64.b64encode(content["text"].encode()).decode()
        self.assertEqual(importer.extract_session(archive(row)).refresh_token, "fresh-response-token")

    def test_rejects_foreign_hosts_http_and_credentialed_urls(self):
        for url in (
            "https://evil.test/auth/v1/token", "http://sb.flybasis.com/auth/v1/token",
            "https://sb.flybasis.com.evil.test/auth/v1/token",
            "https://sb.flybasis.com@evil.test/auth/v1/token",
            "https://user:password@sb.flybasis.com/auth/v1/token",
            "https://sb.flybasis.com:444/auth/v1/token",
            "https://agentsearch.vercel.app/monitoring",
        ):
            with self.subTest(url=url):
                row = entry()
                row["request"]["url"] = url
                with self.assertRaises(importer.HarImportError):
                    importer.extract_session(archive(row))

    def test_incomplete_failed_and_malformed_entries_are_not_credentials(self):
        rows = [None, {}, {"request": []}, entry(), entry(), entry(), entry()]
        rows[3]["response"]["status"] = 401
        rows[4]["request"]["headers"] = []
        rows[5]["response"]["content"]["text"] = "bad JSON"
        rows[6]["response"]["content"]["text"] = json.dumps({"access_token": "access-only"})
        with self.assertRaises(importer.HarImportError):
            importer.extract_session(archive(*rows))

    def test_does_not_guess_between_different_accounts(self):
        second = entry("other-account-token")
        data = json.loads(second["response"]["content"]["text"])
        data["user"]["id"] = "another-user"
        second["response"]["content"]["text"] = json.dumps(data)
        with self.assertRaisesRegex(importer.HarImportError, "multiple accounts") as raised:
            importer.extract_session(archive(entry(), second))
        self.assertNotIn("private@example.test", str(raised.exception))
        self.assertNotIn("other-account-token", str(raised.exception))

    def test_rejects_dotenv_interpolation_and_command_injection(self):
        for token in ("abc\nFLYBASIS_BASE_URL=https://evil.test", "${HOME}", "$(touch bad)", "'; touch bad; '"):
            with self.subTest(token=token), self.assertRaises(importer.HarImportError):
                importer.extract_session(archive(entry(refresh=token)))

    def test_private_output_is_loadable_by_dotenv_and_contains_only_needed_settings(self):
        from dotenv import dotenv_values
        session = importer.load_session(self.har)
        importer.write_env(session, self.output)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(dict(dotenv_values(self.output)), session.settings())
        content = self.output.read_text()
        for unwanted in ("private@example.test", "synthetic-short-lived-access", "spent-request-token"):
            self.assertNotIn(unwanted, content)

    def test_never_overwrites_existing_env_or_symlink_target(self):
        session = importer.load_session(self.har)
        self.output.write_text("EXISTING_SETTING=keep-me\n")
        with self.assertRaises(importer.HarImportError):
            importer.write_env(session, self.output)
        self.assertEqual(self.output.read_text(), "EXISTING_SETTING=keep-me\n")
        link = self.root / ".env.link"
        link.symlink_to(self.output)
        with self.assertRaises(importer.HarImportError):
            importer.write_env(session, link)
        self.assertEqual(self.output.read_text(), "EXISTING_SETTING=keep-me\n")

    def test_cannot_write_to_example_or_source_files(self):
        session = importer.load_session(self.har)
        for name in (".env.example", "settings.py", "secrets.txt"):
            with self.subTest(name=name), self.assertRaises(importer.HarImportError):
                importer.write_env(session, self.root / name)

    def test_check_mode_has_no_side_effects_or_sensitive_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("socket.create_connection", side_effect=AssertionError("no network")):
            code = importer.main([str(self.har), "--check", "--output", str(self.output)])
        self.assertEqual(code, 0)
        self.assertFalse(self.output.exists())
        self.assertIn("FLYBASIS_REFRESH_TOKEN", output.getvalue())
        for secret in ("fresh-response-token", "synthetic-anon", "private@example.test", "synthetic-short-lived-access"):
            self.assertNotIn(secret, output.getvalue())

    def test_cli_writes_private_env_without_printing_secrets(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = importer.main([str(self.har), "--output", str(self.output)])
        self.assertEqual(code, 0)
        self.assertTrue(self.output.exists())
        self.assertNotIn("fresh-response-token", output.getvalue())
        self.assertNotIn("synthetic-anon", output.getvalue())

    def test_cli_errors_do_not_echo_archive_content(self):
        self.har.write_text("broken JSON with secret-not-for-logs")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = importer.main([str(self.har), "--output", str(self.output)])
        self.assertEqual(code, 2)
        self.assertNotIn("secret-not-for-logs", errors.getvalue())
        self.assertFalse(self.output.exists())

    def test_import_size_is_bounded(self):
        with patch.object(importer, "MAX_HAR_BYTES", 10):
            with self.assertRaisesRegex(importer.HarImportError, "import limit"):
                importer.load_session(self.har)


if __name__ == "__main__":
    unittest.main()
