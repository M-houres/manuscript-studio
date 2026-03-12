from __future__ import annotations

import os
import sys
import re
import time
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("APP_ENV", "smoke")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'smoke.db').as_posix()}")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("LOG_FILE", str(DATA_DIR / "logs" / "smoke.log"))
os.environ.setdefault("CSRF_ENABLED", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def extract_csrf(html: str) -> str:
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("CSRF token not found in HTML.")
    return match.group(1)


def expect_status(name: str, resp, expected: Iterable[int]) -> bool:
    expected_set = set(expected)
    ok = resp.status_code in expected_set
    label = "OK" if ok else "FAIL"
    print(f"[{label}] {name}: {resp.status_code}, expected {sorted(expected_set)}")
    return ok


def main() -> int:
    failures = 0
    with TestClient(app) as client:
        resp = client.get("/healthz")
        if not expect_status("GET /healthz", resp, [200]):
            failures += 1
        else:
            payload_ok = resp.json().get("status") == "ok"
            print("[OK] /healthz payload" if payload_ok else "[FAIL] /healthz payload")
            if not payload_ok:
                failures += 1

        for path in ["/", "/login", "/register", "/privacy", "/terms", "/contact"]:
            resp = client.get(path)
            if not expect_status(f"GET {path}", resp, [200]):
                failures += 1

        reg_page = client.get("/register")
        csrf = extract_csrf(reg_page.text)
        email = f"smoke_{int(time.time())}@example.com"
        resp = client.post(
            "/register",
            data={"email": email, "name": "Smoke User", "password": "Pass1234", "csrf_token": csrf},
            follow_redirects=False,
        )
        if not expect_status("POST /register", resp, [303]):
            failures += 1

        for path in ["/assets", "/history"]:
            resp = client.get(path, follow_redirects=False)
            if not expect_status(f"GET {path} (authed)", resp, [200]):
                failures += 1

        resp = client.get("/admin", follow_redirects=False)
        if not expect_status("GET /admin (non-admin)", resp, [403]):
            failures += 1

        client.cookies.clear()
        resp = client.get("/assets", follow_redirects=False)
        if not expect_status("GET /assets (anon)", resp, [303]):
            failures += 1

        login_page = client.get("/login")
        csrf = extract_csrf(login_page.text)
        resp = client.post(
            "/login",
            data={"email": email, "password": "Pass1234", "csrf_token": csrf},
            follow_redirects=False,
        )
        if not expect_status("POST /login", resp, [303]):
            failures += 1

        resp = client.get("/payments/return", follow_redirects=False)
        if not expect_status("GET /payments/return (disabled)", resp, [404]):
            failures += 1

    if failures:
        print(f"Smoke test result: FAIL ({failures} failures)")
        return 1
    print("Smoke test result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
