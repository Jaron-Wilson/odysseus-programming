"""The Programming mod's web server (Starlette on uvicorn).

Pages (each a tab in Odysseus, see odysseus-mod.json):

  GET  /terminal                           the Terminal (static/terminal.js)
  GET  /sessions                           Claude sessions (static/sessions.js)
  GET  /settings                           the Terminal's limits, copy on select
  GET  /cards/live                         Home card: live Claude Code sessions
  GET  /health                             for the core, no identity

The Terminal (moved from the core's routes/terminal_routes.py):

  WS     api/terminal/ws                   start a session, or ?id= to reattach
  GET    api/terminal/sessions             this user's sessions
  PATCH  api/terminal/sessions/{id}        rename one
  DELETE api/terminal/sessions/{id}        close one (kills its processes)
  GET    api/terminal/info                 shell, grace period, session cap

Claude sessions (moved from the core's routes/claude_sessions_routes.py):

  GET api/claude_sessions                                every session, live first
  GET api/claude_sessions/{project}/{id}?before=&limit=  a page of turns, newest last
  GET api/claude_sessions/{project}/{id}/updates?after=  turns written since `after`

And GET/PUT api/prefs (this user's preferences), GET/PUT api/settings.

WebSocket messages: binary frames are raw terminal bytes both ways. Text
frames are JSON control messages. From the browser: {"type": "resize",
"rows", "cols"}, {"type": "title", "title"}, {"type": "ping"}. To the
browser: {"type": "hello", ...session info, "replay"}, {"type": "exit",
"code"}, {"type": "detached"} (opened in another tab), {"type": "gone"}
(no such session any more) and {"type": "error", "message"}.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os
import time
from typing import Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from programming import auth, claude_sessions as ccs, config, terminal

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
PAGES = os.path.join(HERE, "pages")

# Close codes the browser acts on (static/terminal.js).
CLOSE_TAKEN_OVER = 4000     # opened in another tab: don't reconnect
CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_GONE = 4404
CLOSE_TOO_MANY = 4429

OPEN_PATHS = ("/health", "/login")


def _page(name: str) -> HTMLResponse:
    with open(os.path.join(PAGES, name), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


def _err(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


async def _admin_or_403() -> Optional[JSONResponse]:
    ok, why = await asyncio.to_thread(auth.admin_ok)
    return None if ok else _err(403, why)


# ── Who is asking (pure ASGI, so it covers WebSockets too) ──────────────
class Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path") or "/"
        if path in OPEN_PATHS or path.startswith("/static/"):
            # Static files and the health check carry nothing private.
            scope.setdefault("state", {})["user"] = None
            return await self.app(scope, receive, send)
        req = Request(scope) if scope["type"] == "http" else WebSocket(scope, receive, send)
        user = auth.user_for(req.headers, req.cookies)
        if user is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": CLOSE_UNAUTHORIZED})
                return
            if not auth.under_core() and scope["method"] == "GET" and not path.startswith("/api/"):
                resp: Response = RedirectResponse("/login?next=" + path.lstrip("/"), status_code=303)
            else:
                resp = _err(401, "Not signed in")
            return await resp(scope, receive, send)
        scope.setdefault("state", {})["user"] = user
        return await self.app(scope, receive, send)


def _user(request) -> str:
    return request.state.user or ""


# ── Pages ───────────────────────────────────────────────────────────────
async def health(request: Request):
    return PlainTextResponse("ok")


async def index(request: Request):
    return _page("index.html")


async def terminal_page(request: Request):
    return _page("terminal.html")


async def sessions_page(request: Request):
    return _page("sessions.html")


async def settings_page(request: Request):
    return _page("settings.html")


async def live_card(request: Request):
    """Home card: the live Claude Code sessions (written to in the last 2
    minutes). Only for those who may read them."""
    ok, _why = await asyncio.to_thread(auth.admin_ok)
    rows = []
    if ok:
        sessions = await asyncio.to_thread(ccs.list_sessions)
        rows = [s for s in sessions if s.get("live")][:6]
    items = "".join(
        f'<li><span class="dot running"></span><b>{html.escape(s.get("title") or "")}</b>'
        f'<span class="sub">{html.escape((s.get("cwd") or "").replace(os.path.expanduser("~"), "~", 1))}</span></li>'
        for s in rows) or '<li class="sub">No Claude Code session is running right now.</li>'
    body = f"""<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="../static/common.css">
<style>body{{padding:10px 12px;font-size:13px}} ul{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}}
li{{display:flex;align-items:center;gap:8px;min-width:0}} b{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.sub{{opacity:.6;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}</style>
<ul>{items}</ul>"""
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


async def login(request: Request):
    if auth.under_core():
        return _err(404, "Not here: Odysseus signs you in.")
    nxt = request.query_params.get("next") or "terminal"
    if not nxt.replace("/", "").replace("-", "").replace("_", "").isalnum():
        nxt = "terminal"
    msg = ""
    if request.method == "POST":
        form = await request.form()
        if auth.check_password(str(form.get("password") or "")):
            resp = RedirectResponse(nxt, status_code=303)
            resp.set_cookie(auth.COOKIE, auth.make_cookie(), max_age=auth.COOKIE_TTL_S, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https")
            return resp
        await asyncio.sleep(1)
        msg = "<p class='notice'>That is not the password.</p>"
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Programming: sign in</title><link rel="stylesheet" href="static/common.css">
<form method="post" style="max-width:320px;margin:15vh auto;display:flex;flex-direction:column;gap:10px;padding:0 16px">
<h2 style="margin:0">Programming</h2>{msg}
<input type="password" name="password" placeholder="Password" autofocus required
 style="font:inherit;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:none;color:inherit">
<button class="btn" type="submit">Sign in</button></form>""")


# ── Terminal API ────────────────────────────────────────────────────────
async def _refuse(ws: WebSocket, code: int, message: str) -> None:
    await ws.accept()
    try:
        await ws.send_text(json.dumps({"type": "error", "message": message}))
        await ws.close(code=code)
    except Exception:
        pass


async def terminal_ws(ws: WebSocket):
    if not auth.under_core() and not auth.same_origin(ws.headers):
        await ws.close(code=CLOSE_FORBIDDEN)
        return
    ok, why = await asyncio.to_thread(auth.admin_ok)
    if not ok:
        await _refuse(ws, CLOSE_FORBIDDEN, why)
        return
    owner = ws.scope["state"]["user"]
    q = ws.query_params
    rows, cols = q.get("rows"), q.get("cols")
    sid = q.get("id")
    if sid:
        s = terminal.manager.get(owner, sid)
        if not s:
            await ws.accept()
            try:
                await ws.send_text(json.dumps({"type": "gone", "id": sid}))
                await ws.close(code=CLOSE_GONE)
            except Exception:
                pass
            return
        replay = True
    else:
        try:
            s = terminal.manager.create(owner, cwd=q.get("cwd"), rows=rows or 24, cols=cols or 80,
                                        title=q.get("title"))
        except terminal.TooManySessions as e:
            await _refuse(ws, CLOSE_TOO_MANY, str(e))
            return
        except ValueError as e:
            await _refuse(ws, CLOSE_BAD_REQUEST, str(e))
            return
        except OSError as e:
            logger.warning("Could not start a terminal: %s", e)
            await _refuse(ws, 1011, f"Could not start a shell: {e.strerror or e}")
            return
        replay = False
    await ws.accept()
    if rows and cols:
        s.resize(rows, cols)
    att = s.attach(ws)
    hello = {"type": "hello", "replay": replay, **s.info()}
    sender = asyncio.create_task(terminal.pump(s, att, hello))

    async def receive():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            data = m.get("bytes")
            if data is not None:
                s.write(data)
                continue
            text = m.get("text")
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "resize":
                s.resize(msg.get("rows"), msg.get("cols"))
            elif kind == "input" and isinstance(msg.get("data"), str):
                s.write(msg["data"].encode("utf-8", "replace"))
            elif kind == "title" and isinstance(msg.get("title"), str) and msg["title"].strip():
                s.title = msg["title"].strip()[:60]

    receiver = asyncio.create_task(receive())
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # No awaiting in here: this also runs when the server cancels the
        # connection, and must not swallow that cancellation.
        taken_over = s.att is not att and not s.exited
        s.detach(att)
        for t in (sender, receiver):
            if not t.done():
                t.cancel()
    await asyncio.gather(sender, receiver, return_exceptions=True)
    try:
        if s.exited:
            await ws.close(code=1000)
        elif taken_over:
            await ws.send_text(json.dumps({"type": "detached"}))
            await ws.close(code=CLOSE_TAKEN_OVER)
    except Exception:
        pass


async def list_terminals(request: Request):
    if (r := await _admin_or_403()):
        return r
    return JSONResponse({"sessions": [s.info() for s in terminal.manager.list(_user(request))]})


async def one_terminal(request: Request):
    if (r := await _admin_or_403()):
        return r
    sid = request.path_params["sid"]
    owner = _user(request)
    if request.method == "DELETE":
        if not terminal.manager.close(owner, sid):
            return _err(404, "No such terminal")
        return JSONResponse({"ok": True})
    s = terminal.manager.get(owner, sid)
    if not s:
        return _err(404, "No such terminal")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    title = str((body or {}).get("title") or "").strip()
    if title:
        s.title = title[:60]
    return JSONResponse(s.info())


async def terminal_info(request: Request):
    if (r := await _admin_or_403()):
        return r
    return JSONResponse({
        "shell": terminal.login_shell(), "home": terminal.home_dir(),
        "grace_s": terminal.GRACE_S, "max_sessions": terminal.MAX_SESSIONS,
        "scrollback_bytes": terminal.SCROLLBACK_BYTES,
    })


# ── Claude sessions API ─────────────────────────────────────────────────
def _transcript_or_none(project: str, session_id: str) -> Optional[str]:
    return ccs.transcript_path(project, session_id)


async def list_claude(request: Request):
    if (r := await _admin_or_403()):
        return r
    sessions = await asyncio.to_thread(ccs.list_sessions)
    return JSONResponse({"sessions": sessions, "live_window_s": ccs.LIVE_S, "now": time.time()})


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


async def claude_page(request: Request):
    if (r := await _admin_or_403()):
        return r
    project, sid = request.path_params["project"], request.path_params["sid"]
    path = _transcript_or_none(project, sid)
    if not path:
        return _err(404, "No such session")
    limit = max(1, min(_int(request.query_params.get("limit"), ccs.PAGE_TURNS), ccs.PAGE_TURNS))
    page = await asyncio.to_thread(ccs.read_page, path, _int(request.query_params.get("before")), limit)
    page["session"] = await asyncio.to_thread(ccs.session_info, project, path)
    return JSONResponse(page)


async def claude_updates(request: Request):
    if (r := await _admin_or_403()):
        return r
    project, sid = request.path_params["project"], request.path_params["sid"]
    path = _transcript_or_none(project, sid)
    if not path:
        return _err(404, "No such session")
    out = await asyncio.to_thread(ccs.read_after, path, _int(request.query_params.get("after"), 0))
    out["session"] = await asyncio.to_thread(ccs.session_info, project, path)
    return JSONResponse(out)


# ── Preferences and settings ────────────────────────────────────────────
async def prefs(request: Request):
    user = _user(request)
    if request.method == "PUT":
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "JSON, please")
        if not isinstance(body, dict):
            return _err(400, "An object, please")
        return JSONResponse({"prefs": await asyncio.to_thread(config.save_prefs, user, body)})
    return JSONResponse({"prefs": config.prefs(user)})


async def settings_api(request: Request):
    if (r := await _admin_or_403()):
        return r
    if request.method == "PUT":
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "JSON, please")
        if not isinstance(body, dict):
            return _err(400, "An object, please")
        await asyncio.to_thread(config.save_settings, body)
    return JSONResponse({"settings": config.settings(), "bounds": {k: [lo, hi] for k, (lo, hi, _d) in config.SETTINGS.items()},
                         "shell": terminal.login_shell(), "home": terminal.home_dir(),
                         "open_terminals": sum(1 for _ in terminal.manager._sessions.values())})


@contextlib.asynccontextmanager
async def _lifespan(app):
    yield
    # Stopping the mod hangs up every terminal (their shells get SIGHUP).
    terminal.manager.close_all()


def build_app() -> Starlette:
    config.apply()
    routes = [
        Route("/health", health),
        Route("/login", login, methods=["GET", "POST"]),
        Route("/", index),
        Route("/terminal", terminal_page),
        Route("/sessions", sessions_page),
        Route("/settings", settings_page),
        Route("/cards/live", live_card),
        WebSocketRoute("/api/terminal/ws", terminal_ws),
        Route("/api/terminal/sessions", list_terminals),
        Route("/api/terminal/sessions/{sid}", one_terminal, methods=["PATCH", "DELETE"]),
        Route("/api/terminal/info", terminal_info),
        Route("/api/claude_sessions", list_claude),
        Route("/api/claude_sessions/{project}/{sid}", claude_page),
        Route("/api/claude_sessions/{project}/{sid}/updates", claude_updates),
        Route("/api/prefs", prefs, methods=["GET", "PUT"]),
        Route("/api/settings", settings_api, methods=["GET", "PUT"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=_lifespan)
    return Identity(app)


app = build_app()
