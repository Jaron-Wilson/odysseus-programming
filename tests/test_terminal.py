"""The Terminal (programming/terminal.py, programming/app.py): a live shell,
checked against a real PTY and a real shell. Ported from the Odysseus core's
tests/test_terminal.py when the Terminal moved into this mod.

  * what is typed runs, and its output comes back;
  * resizing the window resizes the terminal (`stty size` follows);
  * a reload or a dropped connection doesn't lose the shell: reattaching
    replays the recent output and the shell still has its state;
  * closing a terminal kills what runs in it;
  * the per-user session cap holds;
  * only a signed identity from the core gets one, and only while the core
    keeps the mod admin only;
  * a terminal nobody reattaches to is closed after the grace period.
"""
import concurrent.futures
import contextlib
import json
import os
import re
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from programming import app as appmod, auth, terminal
from tests.conftest import USER, ident

pytestmark = pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a POSIX PTY")

H = ident()


def _client():
    return TestClient(appmod.build_app())


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)


@contextlib.contextmanager
def _connect(client, url, headers=None):
    """client.websocket_connect, but leaving it the way a browser does: close,
    then let the server finish with it."""
    with client.websocket_connect(url, headers=ident() if headers is None else headers) as ws:
        try:
            yield ws
        finally:
            try:
                ws.close(1000)
            except Exception:
                pass
            time.sleep(0.15)


def _recv(ws, timeout=10):
    return _pool.submit(ws.receive).result(timeout=timeout)


def _hello(ws):
    m = _recv(ws)
    assert "text" in m, m
    msg = json.loads(m["text"])
    assert msg["type"] == "hello", msg
    return msg


def _read_until(ws, pattern, timeout=10):
    out = b""
    end = time.time() + timeout
    while time.time() < end:
        m = _recv(ws, timeout=max(0.1, end - time.time()))
        if m["type"] == "websocket.close":
            break
        if m.get("bytes"):
            out += m["bytes"]
            if re.search(pattern, out.decode("utf-8", "replace")):
                return out.decode("utf-8", "replace")
    raise AssertionError(f"never saw {pattern!r} in {out[-2000:]!r}")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except OSError:
        return False


def _wait_dead(pid, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_what_is_typed_runs_and_the_output_comes_back():
    with _client() as c, _connect(c, "/api/terminal/ws?rows=24&cols=80") as ws:
        hello = _hello(ws)
        assert hello["replay"] is False and hello["id"] and hello["title"] == "Shell 1"
        ws.send_bytes(b"echo hello-$((1+1))\n")
        _read_until(ws, r"hello-2")
        ws.send_bytes(b"echo $TERM $ODYSSEUS_TERMINAL\n")
        _read_until(ws, r"xterm-256color 1")


def test_resizing_changes_stty_size():
    with _client() as c, _connect(c, "/api/terminal/ws?rows=24&cols=80") as ws:
        _hello(ws)
        ws.send_bytes(b"stty size\n")
        _read_until(ws, r"24 80")
        ws.send_text(json.dumps({"type": "resize", "rows": 40, "cols": 132}))
        ws.send_bytes(b"stty size\n")
        _read_until(ws, r"40 132")


def test_reattaching_replays_the_scrollback_and_the_shell_kept_its_state():
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as ws:
            sid = _hello(ws)["id"]
            ws.send_bytes(b"export X=kept-$((20+22)); echo marker-one\n")
            _read_until(ws, r"marker-one\r?\n")
        listed = c.get("/api/terminal/sessions", headers=ident()).json()["sessions"]
        assert [s["id"] for s in listed] == [sid]
        with _connect(c, f"/api/terminal/ws?id={sid}") as ws:
            hello = _hello(ws)
            assert hello["replay"] is True and hello["id"] == sid
            _read_until(ws, r"marker-one")
            ws.send_bytes(b"echo got-$X\n")
            _read_until(ws, r"got-kept-42")


def test_a_second_tab_takes_the_session_over():
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as first:
            sid = _hello(first)["id"]
            with _connect(c, f"/api/terminal/ws?id={sid}") as second:
                _hello(second)
                msgs = []
                while True:
                    m = _recv(first)
                    msgs.append(m)
                    if m["type"] == "websocket.close":
                        break
                assert any(json.loads(m["text"]).get("type") == "detached" for m in msgs if m.get("text"))
                assert msgs[-1]["code"] == appmod.CLOSE_TAKEN_OVER
                second.send_bytes(b"echo still-here\n")
                _read_until(second, r"still-here")


def test_closing_kills_the_shell_and_its_jobs():
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as ws:
            sid = _hello(ws)["id"]
            ws.send_bytes(b"sleep 300 & echo JOB=$!\n")
            job = int(re.search(r"JOB=(\d+)", _read_until(ws, r"JOB=\d+")).group(1))
            shell = terminal.manager.get(USER, sid).pid
            assert _alive(job) and _alive(shell)
            assert c.delete(f"/api/terminal/sessions/{sid}", headers=ident()).json() == {"ok": True}
            end = time.time() + 10
            saw_exit = False
            while time.time() < end:
                m = _recv(ws)
                if m.get("text") and json.loads(m["text"])["type"] == "exit":
                    saw_exit = True
                if m["type"] == "websocket.close":
                    break
            assert saw_exit
        assert _wait_dead(shell) and _wait_dead(job)
        assert c.get("/api/terminal/sessions", headers=ident()).json()["sessions"] == []
        assert c.delete(f"/api/terminal/sessions/{sid}", headers=ident()).status_code == 404
        with _connect(c, f"/api/terminal/ws?id={sid}") as ws:
            assert json.loads(_recv(ws)["text"])["type"] == "gone"


def test_exit_in_the_shell_ends_the_session():
    with _client() as c, _connect(c, "/api/terminal/ws") as ws:
        _hello(ws)
        ws.send_bytes(b"exit 3\n")
        end = time.time() + 10
        while time.time() < end:
            m = _recv(ws)
            if m.get("text"):
                msg = json.loads(m["text"])
                if msg["type"] == "exit":
                    assert msg["code"] == 3
                    break
        else:
            raise AssertionError("no exit message")
        assert c.get("/api/terminal/sessions", headers=ident()).json()["sessions"] == []


def test_the_session_cap_holds(monkeypatch):
    monkeypatch.setenv("PROGRAMMING_TERMINAL_MAX_SESSIONS", "2")     # so the settings leave it alone
    monkeypatch.setattr(terminal, "MAX_SESSIONS", 2)
    with _client() as c:
        for _ in range(2):
            with _connect(c, "/api/terminal/ws") as ws:
                _hello(ws)
        with _connect(c, "/api/terminal/ws") as ws:
            m = json.loads(_recv(ws)["text"])
            assert m["type"] == "error" and "Close one" in m["message"]
            close = _recv(ws)
            assert close["type"] == "websocket.close" and close["code"] == appmod.CLOSE_TOO_MANY
        titles = sorted(s["title"] for s in c.get("/api/terminal/sessions", headers=ident()).json()["sessions"])
        assert titles == ["Shell 1", "Shell 2"]


def test_sessions_are_per_user():
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as ws:
            _hello(ws)
        assert len(c.get("/api/terminal/sessions", headers=ident()).json()["sessions"]) == 1
        assert c.get("/api/terminal/sessions", headers=ident("alex")).json()["sessions"] == []


def test_the_start_folder_has_to_be_inside_home():
    home = terminal.home_dir()
    os.makedirs(os.path.join(home, "proj"))
    with _client() as c:
        with _connect(c, "/api/terminal/ws?cwd=" + os.path.join(home, "proj")) as ws:
            _hello(ws)
            ws.send_bytes(b"pwd\n")
            _read_until(ws, re.escape(os.path.join(home, "proj")))
        with _connect(c, "/api/terminal/ws?cwd=/etc") as ws:
            m = json.loads(_recv(ws)["text"])
            assert m["type"] == "error" and "home" in m["message"]


@pytest.mark.parametrize("headers", [
    {},                                                    # no identity at all
    ident(token="odm_someone-else"),                       # signed with another token
    ident(mod="other-mod"),                                # for another mod
    ident(ts=int(time.time()) - 3600),                     # an old signature
    ident(user=auth.CORE_USER),                            # the core's own identity is no user
])
def test_only_a_signed_user_gets_a_shell(headers):
    with _client() as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with _connect(c, "/api/terminal/ws", headers=headers) as ws:
                ws.receive()
        assert e.value.code == appmod.CLOSE_UNAUTHORIZED
        assert c.get("/api/terminal/sessions", headers=headers).status_code == 401
    assert terminal.manager._sessions == {}


@pytest.mark.parametrize("me", [
    {"id": "programming"},                                 # an old core: no admin_only in GET me
    {"id": "programming", "admin_only": False, "access": "users"},
])
def test_no_shell_unless_the_core_keeps_the_mod_admin_only(monkeypatch, me):
    monkeypatch.setattr(auth, "_fetch_me", lambda: me)
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as ws:
            m = json.loads(_recv(ws)["text"])
            assert m["type"] == "error" and "admin only" in m["message"]
            close = _recv(ws)
            assert close["code"] == appmod.CLOSE_FORBIDDEN
        r = c.get("/api/terminal/info", headers=ident())
        assert r.status_code == 403 and "Update Odysseus" in r.json()["detail"]
        assert c.get("/api/claude_sessions", headers=ident()).status_code == 403
    assert terminal.manager._sessions == {}


def test_a_core_it_cannot_reach_means_no_for_now(monkeypatch):
    def down():
        raise OSError("connection refused")
    monkeypatch.setattr(auth, "_fetch_me", down)
    with _client() as c:
        r = c.get("/api/terminal/info", headers=ident())
        assert r.status_code == 403 and "Could not reach Odysseus" in r.json()["detail"]


def test_a_detached_session_is_closed_after_the_grace_period(monkeypatch):
    monkeypatch.setenv("PROGRAMMING_TERMINAL_GRACE_S", "1")
    monkeypatch.setattr(terminal, "GRACE_S", 0.5)
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as ws:
            sid = _hello(ws)["id"]
            pid = terminal.manager.get(USER, sid).pid
        assert [s["attached"] for s in c.get("/api/terminal/sessions", headers=ident()).json()["sessions"]] == [False]
        assert _wait_dead(pid)
        assert c.get("/api/terminal/sessions", headers=ident()).json()["sessions"] == []


def test_rename_and_info():
    with _client() as c:
        with _connect(c, "/api/terminal/ws") as ws:
            sid = _hello(ws)["id"]
        r = c.patch(f"/api/terminal/sessions/{sid}", json={"title": "ssh pi"}, headers=ident())
        assert r.json()["title"] == "ssh pi"
        assert c.get("/api/terminal/sessions", headers=ident()).json()["sessions"][0]["title"] == "ssh pi"
        info = c.get("/api/terminal/info", headers=ident()).json()
        assert info["shell"] == "/bin/bash" and info["max_sessions"] == terminal.MAX_SESSIONS


def test_the_shell_does_not_get_the_mods_secrets(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-should-not-leak")
    with _client() as c, _connect(c, "/api/terminal/ws") as ws:
        _hello(ws)
        ws.send_bytes(b"printf 'k%s=[%s] t%s=[%s]\\n' ey \"$SOME_API_KEY\" ok \"$ODYSSEUS_MOD_TOKEN\"\n")
        out = _read_until(ws, r"tok=\[[^\]]*\]")
        assert "key=[]" in out and "tok=[]" in out


def test_output_and_keystrokes_are_never_logged():
    src = open(terminal.__file__, encoding="utf-8").read() + open(appmod.__file__, encoding="utf-8").read()
    assert not re.search(r"logger\.\w+\([^)]*(data|scrollback|chunk|text)\b", src)
