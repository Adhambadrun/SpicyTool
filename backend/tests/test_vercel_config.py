"""Keep the deployment config valid without weakening credential exclusions."""
from __future__ import annotations

import fnmatch
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class VercelConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "vercel.json").read_text())
        self.pattern = self.config["functions"]["app.py"]["excludeFiles"]

    def excludes(self, path):
        # The project intentionally uses a flat brace list. **/ can match
        # zero directories (so **/.env* protects root .env as well).
        self.assertTrue(self.pattern.startswith("{") and self.pattern.endswith("}"))
        patterns = self.pattern[1:-1].split(",")
        return any(
            fnmatch.fnmatchcase(path, p)
            or (p.startswith("**/") and fnmatch.fnmatchcase(path, p[3:]))
            for p in patterns
        )

    def test_exclude_files_respects_vercel_schema_limit(self):
        # https://openapi.vercel.sh/vercel.json: excludeFiles.maxLength = 256.
        # 267 characters passed all app tests but prevented deployment.
        self.assertIsInstance(self.pattern, str)
        self.assertLessEqual(len(self.pattern), 256)
        self.assertEqual(self.config["framework"], "fastapi")

    def test_credentials_and_archives_stay_out_of_function_bundle(self):
        for path in (
            ".env", ".env.flybasis", "backend/.env", "backend/.env.private",
            "agentsearch.vercel.app.har", "captures/private.har",
            "backend/data/.auth_secret", "backend/data/.flybasis_refresh_token",
            "backend/data/.flybasis_refresh_token.temporary",
        ):
            with self.subTest(path=path):
                self.assertTrue(self.excludes(path))

    def test_runtime_files_remain_available(self):
        for path in (
            "app.py", "requirements.txt", "backend/main.py", "backend/api_v2.py",
            "backend/providers/flybasis_session.py", "backend/data/airports.json",
            "frontend/index.html",
        ):
            with self.subTest(path=path):
                self.assertFalse(self.excludes(path))


if __name__ == "__main__":
    unittest.main()
