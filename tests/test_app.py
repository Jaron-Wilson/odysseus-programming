"""The rest of the mod: its manifest and tutorial, its pages (relative URLs
only, as a sandboxed frame needs), preferences and settings, and the
standalone login."""
import json
import os
import re

import pytest
from starlette.testclient import TestClient

from programming import app as appmod, auth, config, terminal
from tests.conftest import ROOT, ident


def _client():
    return TestClient(appmod.build_app())


def _manifest():
    with open(os.path.join(ROOT, "odysseus-mod.json"), encoding="utf-8") as f:
        return json.load(f)


# ── Manifest ────────────────────────────────────────────────────────────

def test_the_manifest_is_admin_only_and_takes_over_the_old_pages():
    m = _manifest()
    assert m["id"] == "programming" and m["admin_only"] is True
    tabs = {t["id"]: t for t in m["ui"]["tabs"]}
    assert tabs["terminal"]["replaces"] == "terminal" and tabs["sessions"]["replaces"] == "claude-sessions"
    assert all(t["section"] == "build" for t in tabs.values())
    assert m["runtime"]["idle_stop_minutes"] == 0, "stopping the mod would end every shell"
    assert m["permissions"] == []


def test_every_page_the_manifest_names_is_served():
    m = _manifest()
    paths = [t["path"] for t in m["ui"]["tabs"]] + [c["path"] for c in m["ui"]["home_cards"]]
    paths += [m["ui"]["settings_path"], m["runtime"]["health"]]
    paths += [s["action"]["path"] for s in m["tutorial"]["steps"] if s.get("action", {}).get("path")]
    c = _client()
    for p in paths:
        assert c.get(p, headers=ident()).status_code == 200, p


def test_the_tutorial_covers_what_the_mod_does():
    steps = _manifest()["tutorial"]["steps"]
    assert 3 <= len(steps) <= 8
    text = " ".join(s["title"] + " " + s["body"] for s in steps)
    for word in ("Terminal", "Claude sessions", "/claude attach", "settings"):
        assert word in text, word
    assert any(s.get("action", {}).get("settings") for s in steps)
    for s in steps:
        assert len(s["title"]) <= 80 and len(s["body"]) <= 1200
        assert "—" not in s["body"], "no em dashes"


def test_the_manifest_passes_the_cores_validator():
    """When an Odysseus checkout is next to this one (ODYSSEUS_CORE=<path>),
    check the manifest with its own validator."""
    core = os.environ.get("ODYSSEUS_CORE")
    if not core:
        pytest.skip("set ODYSSEUS_CORE to an Odysseus checkout to run this")
    import sys
    sys.path.insert(0, core)
    from src.mods import manifest as mf
    m = mf.load(ROOT)
    assert m["admin_only"] is True and m["warnings"] == []


# ── Pages ───────────────────────────────────────────────────────────────

def _front_end_files():
    for d in ("pages", "static"):
        base = os.path.join(ROOT, "programming", d)
        for name in os.listdir(base):
            if name.endswith((".html", ".js")):
                yield os.path.join(base, name)


def test_the_pages_use_relative_urls_only():
    """A mod page lives under /mods/<id>/~<frame token>/: a URL starting with
    "/" would leave it (and lose the token)."""
    bad = re.compile(r"""(?:fetch\(|href=|src=|new URL\(|import\()\s*[`'"]/(?!/)""")
    for path in _front_end_files():
        src = open(path, encoding="utf-8").read()
        assert not bad.search(src), (path, bad.search(src).group(0))
        assert "localStorage" not in src.replace("no localStorage", ""), path


def test_no_page_reaches_for_the_odysseus_page():
    for path in _front_end_files():
        src = open(path, encoding="utf-8").read()
        for word in ("window.parent.document", "top.location", "/api/terminal", "/api/claude_sessions",
                     "document.getElementById('message')"):
            assert word not in src, (path, word)


def test_pages_need_an_identity_and_static_files_do_not():
    c = _client()
    assert c.get("/terminal").status_code == 401
    assert c.get("/terminal", headers=ident()).status_code == 200
    assert c.get("/static/xterm/xterm.mjs").status_code == 200
    assert c.get("/health").text == "ok"


# ── Preferences and settings ────────────────────────────────────────────

def test_prefs_are_per_user_and_checked():
    c = _client()
    r = c.put("/api/prefs", json={"fontSize": 99, "keys": True, "copyOnSelect": True, "evil": "x"}, headers=ident())
    assert r.json()["prefs"] == {"fontSize": 28, "keys": True, "copyOnSelect": True}
    c.put("/api/prefs", json={"keys": None, "sessionsProject": "-home-jaron-proj"}, headers=ident())
    assert c.get("/api/prefs", headers=ident()).json()["prefs"] == {
        "fontSize": 28, "copyOnSelect": True, "sessionsProject": "-home-jaron-proj"}
    assert c.get("/api/prefs", headers=ident("alex")).json()["prefs"] == {}
    assert oct(os.stat(os.path.join(config.data_dir(), "prefs.json")).st_mode & 0o777) == "0o600"


def test_settings_change_the_terminal_limits():
    c = _client()
    d = c.get("/api/settings", headers=ident()).json()
    assert d["settings"] == {"grace_minutes": 10, "max_sessions": 8, "scrollback_kb": 200}
    d = c.put("/api/settings", json={"grace_minutes": 30, "max_sessions": 500, "scrollback_kb": "x"},
              headers=ident()).json()
    assert d["settings"] == {"grace_minutes": 30, "max_sessions": 32, "scrollback_kb": 200}
    assert terminal.GRACE_S == 1800 and terminal.MAX_SESSIONS == 32
    # A restart of the mod keeps them.
    _client()
    assert terminal.GRACE_S == 1800


def test_settings_are_admin_only_too(monkeypatch):
    monkeypatch.setattr(auth, "_fetch_me", lambda: {"admin_only": False})
    c = _client()
    assert c.put("/api/settings", json={"max_sessions": 1}, headers=ident()).status_code == 403


# ── Standalone ──────────────────────────────────────────────────────────

@pytest.fixture
def standalone(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_URL")
    monkeypatch.setenv("MOD_PASSWORD", "hunter2-correct")
    monkeypatch.setattr(auth, "_password", None)


def test_standalone_has_its_own_login(standalone):
    c = _client()
    r = c.get("/terminal", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=terminal"
    assert c.get("/api/terminal/sessions").status_code == 401
    # Identity headers mean nothing without the core.
    assert c.get("/api/terminal/sessions", headers=ident()).status_code == 401
    assert c.post("/login", data={"password": "wrong"}).status_code == 200
    assert auth.COOKIE not in c.cookies
    r = c.post("/login?next=terminal", data={"password": "hunter2-correct"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "terminal"
    assert c.get("/api/terminal/sessions").json() == {"sessions": []}


def test_standalone_sockets_must_come_from_its_own_page(standalone):
    from starlette.websockets import WebSocketDisconnect
    c = _client()
    c.post("/login", data={"password": "hunter2-correct"})
    for origin in ("https://evil.example", "null"):
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/api/terminal/ws", headers={"origin": origin}) as ws:
                ws.receive()
        assert e.value.code == appmod.CLOSE_FORBIDDEN
    with c.websocket_connect("/api/terminal/ws", headers={"origin": "http://testserver"}) as ws:
        assert json.loads(ws.receive_text())["type"] == "hello"
        ws.close()


def test_a_forged_or_expired_cookie_is_refused(standalone):
    import time
    assert auth.cookie_ok(auth.make_cookie())
    assert not auth.cookie_ok(auth.make_cookie(now=time.time() - auth.COOKIE_TTL_S - 5))
    exp, sig = auth.make_cookie().split(".")
    assert not auth.cookie_ok(f"{int(exp) + 1}.{sig}")
    assert not auth.cookie_ok("nonsense")
