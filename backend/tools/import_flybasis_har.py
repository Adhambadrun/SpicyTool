#!/usr/bin/env python3
"""Import an authorized Flybasis account's PRIVATE HAR into a private .env file.

No network calls, token exchanges, account changes, or token output. Only
successful HTTPS Supabase auth responses from sb.flybasis.com are accepted;
telemetry, request refresh tokens (already spent), and page HTML are ignored.
Use a fresh private capture after revoking any session uploaded publicly.

    python3 backend/tools/import_flybasis_har.py /path/to/private.har --check
    python3 backend/tools/import_flybasis_har.py /path/to/private.har

Default output: repo-root .env, mode 0600. Existing files are NEVER replaced.
Use --output backend/.env or --output .env.flybasis if .env already exists,
then merge/import the settings privately. No third-party dependencies needed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
AUTH_ORIGIN = "https://sb.flybasis.com"
AUTH_PATHS = {"/auth/v1/verify", "/auth/v1/token"}
MAX_HAR_BYTES = 32 * 1024 * 1024


class HarImportError(ValueError):
    """Safe, credential-free error suitable for terminal output."""


def _credential(value: object) -> str:
    # Credentials are data, never dotenv interpolation or shell commands.
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", value.strip()):
        return value.strip()
    return ""


@dataclass(repr=False)
class CapturedSession:
    anon_key: str = field(repr=False)
    refresh_token: str = field(repr=False)
    captured_at: datetime

    def settings(self) -> dict[str, str]:
        return {
            "SPICYTOOL_PROVIDERS": "Flybasis",
            "SPICYTOOL_MODELED_ENGINE": "0",
            "FLYBASIS_SUPABASE_URL": AUTH_ORIGIN,
            "FLYBASIS_SUPABASE_ANON_KEY": self.anon_key,
            "FLYBASIS_REFRESH_TOKEN": self.refresh_token,
        }


def extract_session(archive: object) -> CapturedSession:
    if not isinstance(archive, dict) or not isinstance(archive.get("log"), dict):
        raise HarImportError("Invalid HAR: expected log.entries.")
    entries = archive["log"].get("entries")
    if not isinstance(entries, list):
        raise HarImportError("Invalid HAR: expected log.entries.")
    candidates: list[CapturedSession] = []
    accounts: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        response = entry.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        try:
            url = urlsplit(request.get("url", ""))
            if (url.scheme != "https" or url.hostname != "sb.flybasis.com"
                    or url.port not in (None, 443) or url.username or url.password
                    or url.path not in AUTH_PATHS or request.get("method") != "POST"):
                continue
            status = response.get("status", 0)
            if type(status) is not int or not 200 <= status < 300:
                continue
            content = response.get("content", {})
            text = content.get("text", "")
            if content.get("encoding") == "base64":
                text = base64.b64decode(text, validate=True).decode("utf-8")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                continue
            captured_at = datetime.fromisoformat(entry.get("startedDateTime", "").replace("Z", "+00:00"))
            if captured_at.tzinfo is None:
                continue
            headers = {
                h.get("name", "").lower(): h.get("value")
                for h in request.get("headers", []) if isinstance(h, dict)
            }
            anon = _credential(headers.get("apikey"))
            refresh = _credential(payload.get("refresh_token"))
            access = _credential(payload.get("access_token"))
            if not (anon and refresh and access):
                continue
            user = payload.get("user")
            if isinstance(user, dict):
                account = user.get("id") or user.get("email")
                if isinstance(account, str) and account:
                    accounts.add(account)
            candidates.append(CapturedSession(anon, refresh, captured_at.astimezone(timezone.utc)))
        except (ValueError, TypeError, AttributeError, UnicodeError):
            # A malformed/irrelevant entry must never echo its tokens or URL.
            continue
    if len(accounts) > 1:
        raise HarImportError("The HAR contains multiple accounts. Export a private capture of just the account you intend to use.")
    if not candidates:
        raise HarImportError(
            "No complete successful Flybasis session found. Export a private HAR "
            "with response content from auth/v1/verify or auth/v1/token, including "
            "the apikey request header and refresh_token response field."
        )
    # HAR entries need not be in time order. The RESPONSE contains the latest
    # rotation; the request's refresh_token has already been consumed.
    return max(enumerate(candidates), key=lambda pair: (pair[1].captured_at, pair[0]))[1]


def load_session(path: Path) -> CapturedSession:
    try:
        with path.open("rb") as f:
            raw = f.read(MAX_HAR_BYTES + 1)
        if len(raw) > MAX_HAR_BYTES:
            raise HarImportError("HAR exceeds the 32 MiB import limit. Export only the login requests.")
        archive = json.loads(raw)
    except (OSError, ValueError) as exc:
        if isinstance(exc, HarImportError):
            raise
        raise HarImportError("Cannot read a valid HAR JSON file at the supplied path.") from None
    return extract_session(archive)


def write_env(session: CapturedSession, path: Path) -> None:
    name = path.name
    if not (name == ".env" or name.startswith(".env.")) or name.endswith(".example"):
        raise HarImportError("Output must be a private .env or .env.* file, never .env.example.")
    text = "# Private Flybasis session. Never commit, publish, or paste into logs.\n"
    text += "".join(f"{name}='{value}'\n" for name, value in session.settings().items())
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise HarImportError("Output already exists; nothing changed. Choose another private --output file and merge settings privately.") from None
    except OSError:
        raise HarImportError("Cannot create the private output file. Check its parent directory and permissions.") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        path.unlink(missing_ok=True)
        raise HarImportError("Could not write the private output file.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="local private HAR, not a URL")
    parser.add_argument("--check", action="store_true", help="validate capture only; write nothing")
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args(argv)
    try:
        session = load_session(args.har.expanduser())
        if not args.check:
            write_env(session, args.output.expanduser())
    except HarImportError as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 2
    print(f"Found a complete Flybasis login response captured {session.captured_at.isoformat()}.")
    print("Settings: " + ", ".join(session.settings()))
    if args.check:
        print("Check only: no credentials written and no network requests made.")
    else:
        print("Private env file created (mode 0600); no token values printed.")
        print("Restart the local app, or import these values into Vercel Environment Variables and redeploy.")
    print("A capture is not proof of current access. Revoke publicly exposed sessions and use a fresh private capture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
