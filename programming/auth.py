"""Who is asking, under Odysseus and standalone.

Under Odysseus (ODYSSEUS_URL set) the core signs every request it forwards
with the mod's token (docs/mods.md in Odysseus, "The proxy"): the mod trusts
only those headers, and only a real user (never the core's own identity).
The core also decides who may open the mod at all; the manifest says
`admin_only`, so that is admins only. Because the Terminal is a shell on the
server, the mod also asks the core (GET /api/mods/v1/me) whether it really
enforces that, and refuses the Terminal on a core too old to.

Standalone (ODYSSEUS_URL unset) the mod has its own login: the password is
MOD_PASSWORD, or one made up and printed at start. A signed cookie keeps the
browser signed in, and WebSockets must come from the page's own origin.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.request
from typing import Optional, Tuple
from urllib.parse import urlsplit

from programming import config

logger = logging.getLogger(__name__)

SKEW_S = 300
CORE_USER = "odysseus-core"
COOKIE = "programming_session"
COOKIE_TTL_S = 30 * 24 * 3600
STANDALONE_USER = "local"


def under_core() -> bool:
    return bool(os.environ.get("ODYSSEUS_URL"))


def _token() -> str:
    return os.environ.get("ODYSSEUS_MOD_TOKEN", "")


def sign_identity(token: str, mod_id: str, user: str, ts: Optional[int] = None) -> dict:
    ts = int(ts if ts is not None else time.time())
    sig = hmac.new(token.encode(), f"v1\n{mod_id}\n{user}\n{ts}".encode(), hashlib.sha256).hexdigest()
    return {"X-Odysseus-User": user, "X-Odysseus-Mod": mod_id, "X-Odysseus-Ts": str(ts), "X-Odysseus-Sig": sig}


def verify_identity(headers, token: Optional[str] = None, now: Optional[float] = None) -> Optional[str]:
    """The user named by valid identity headers, else None."""
    token = _token() if token is None else token
    user, mid = headers.get("x-odysseus-user"), headers.get("x-odysseus-mod")
    ts, sig = headers.get("x-odysseus-ts"), headers.get("x-odysseus-sig")
    want_mod = os.environ.get("ODYSSEUS_MOD_ID")
    if not (token and user and mid and ts and sig) or (want_mod and mid != want_mod):
        return None
    try:
        t = int(ts)
    except ValueError:
        return None
    if abs((now if now is not None else time.time()) - t) > SKEW_S:
        return None
    good = sign_identity(token, mid, user, t)["X-Odysseus-Sig"]
    return user if hmac.compare_digest(good, sig) else None


# ── Standalone login ────────────────────────────────────────────────────
_secret: Optional[bytes] = None
_password: Optional[str] = None


def _cookie_secret() -> bytes:
    global _secret
    if _secret is None:
        p = os.path.join(config.data_dir(), ".secret")
        try:
            with open(p, "rb") as f:
                _secret = f.read()
        except OSError:
            _secret = b""
        if len(_secret) < 32:
            _secret = secrets.token_bytes(32)
            with open(p, "wb") as f:
                f.write(_secret)
            os.chmod(p, 0o600)
    return _secret


def password() -> str:
    global _password
    if _password is None:
        _password = os.environ.get("MOD_PASSWORD") or secrets.token_urlsafe(12)
        if not os.environ.get("MOD_PASSWORD"):
            print(f"[programming] standalone login password: {_password}", flush=True)
    return _password


def check_password(given: str) -> bool:
    return hmac.compare_digest(str(given or "").encode(), password().encode())


def make_cookie(now: Optional[float] = None) -> str:
    exp = int((now or time.time()) + COOKIE_TTL_S)
    sig = hmac.new(_cookie_secret(), f"v1|{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def cookie_ok(value: Optional[str], now: Optional[float] = None) -> bool:
    try:
        exp_s, sig = str(value or "").split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < (now or time.time()):
        return False
    good = hmac.new(_cookie_secret(), f"v1|{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def same_origin(headers) -> bool:
    """Standalone WebSockets: the Origin must be this server (cross-site
    WebSocket hijacking with the login cookie)."""
    origin = (headers.get("origin") or "").strip()
    if not origin or origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == (headers.get("host") or "").strip().lower()


def user_for(headers, cookies) -> Optional[str]:
    """The user behind a request or socket, or None to refuse it."""
    if under_core():
        user = verify_identity(headers)
        return user if user and user != CORE_USER else None
    return STANDALONE_USER if cookie_ok(cookies.get(COOKIE)) else None


# ── Does the core keep this mod admin only? ─────────────────────────────
_me = {"at": 0.0, "ok": False, "why": "not checked yet"}
_me_lock = threading.Lock()
ME_TTL_S = 60


def _fetch_me() -> dict:
    req = urllib.request.Request(os.environ["ODYSSEUS_URL"].rstrip("/") + "/api/mods/v1/me",
                                 headers={"Authorization": f"Bearer {_token()}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def admin_ok() -> Tuple[bool, str]:
    """(ok, why not), for the Terminal and the Claude sessions (both admin only
    in the core before). Standalone: the login is the guard. Under the core: only
    when the core says this mod is admin only."""
    if not under_core():
        return True, ""
    with _me_lock:
        if time.time() - _me["at"] < ME_TTL_S:
            return _me["ok"], _me["why"]
    try:
        me = _fetch_me()
        if me.get("admin_only") is True and me.get("access", "admins") == "admins":
            ok, why = True, ""
        else:
            ok, why = False, ("This Odysseus can't keep this mod admin only (it is too old for the mod's "
                              "admin_only setting). Update Odysseus to use it.")
    except Exception as e:     # the core is restarting: refuse for now, ask again soon
        logger.warning("could not ask the core about access: %s", e)
        with _me_lock:
            _me.update(at=time.time() - ME_TTL_S + 5, ok=False,
                       why="Could not reach Odysseus to check access. Try again in a moment.")
            return False, _me["why"]
    with _me_lock:
        _me.update(at=time.time(), ok=ok, why=why)
    return ok, why
