# Programming (an Odysseus mod)

Odysseus's coding tools, moved out of the core into one mod:

- **Terminal**: a live shell on the server, as the user Odysseus runs as. Each shell is a tab;
  a shell keeps running when the page reloads or the connection drops, and comes back with
  its recent output. Phones get a row of extra keys (Esc, Tab, Ctrl, Alt, arrows). Copy and
  paste work like Windows Terminal (Ctrl+C with a selection copies, Ctrl+V pastes).
- **Claude sessions**: every Claude Code session on this host (`~/.claude/projects`), live
  ones first, read like a chat. Live sessions follow along. **Copy /claude attach** copies
  the command that hands a session to a chat's coding agent.
- **Home card**: the Claude Code sessions running right now.
- **Settings**: how many terminals can be open at once, how long a detached one keeps running,
  how much output is replayed, and copy on select.

The mod is **admin only** (`"admin_only": true` in `odysseus-mod.json`): the Terminal is a
shell on the server and the transcripts hold everything said in those sessions. Odysseus
checks the admin role on every page, socket and tool call, and the mod refuses its Terminal
and Claude sessions on a core too old to enforce that (it asks `GET /api/mods/v1/me`).

## Install

Settings > Mods > Browse (it has the `odysseus-mod` topic), or paste
`git@github.com:Jaron-Wilson/odysseus-programming.git` into **Install a mod**. Approve it:
it asks for no permissions; its setup makes a virtualenv and installs `requirements.txt`
(Starlette, uvicorn, websockets). Its tabs go in **Build** and take over the old
`#terminal` and `#claude-sessions` links and saved Workspace tabs.

Needs Odysseus with the `admin_only` manifest flag (Jaron-Wilson/odysseus PR #199) and a
POSIX server (the Terminal uses a PTY).

## Moving over from the built-in pages

There is nothing to migrate. The built-in Terminal kept its shells in memory only (they ended
with every restart) and its preferences in the browser; the Claude sessions page read
`~/.claude` directly. The only change: the Terminal's limits used to be
`ODYSSEUS_TERMINAL_MAX_SESSIONS` and `ODYSSEUS_TERMINAL_GRACE_S` in Odysseus's `.env`; a mod
does not see that file, so they are this mod's settings now (or `PROGRAMMING_TERMINAL_*`
environment variables when running it standalone).

The coding agents themselves (the `claude_code` tool with Claude Code, OpenCode and
Antigravity, its approvals, the Background page, `/claude attach` and Claude direct) still
live in the core, with their files in the data dir (`claude_code_*`). See
[What is next](#what-is-next).

## Standalone

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
MOD_PASSWORD=pick-one MOD_DATA_DIR=./data .venv/bin/python -m programming    # http://127.0.0.1:8770
```

Without `ODYSSEUS_URL` it has its own login (`MOD_PASSWORD`, or one printed at start), and
its sockets only accept its own page's origin. `MOD_HOST` and `MOD_PORT` change where it
listens.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
ODYSSEUS_CORE=~/odysseus .venv/bin/python -m pytest -q tests/test_app.py   # also checks the manifest with the core's validator
```

The tests run real shells on a PTY, signed identities as the core sends them, the
admin-only check against old and new cores, the transcript reader, the Markdown renderer
(with node) and the standalone login.

Layout:

```
odysseus-mod.json          the manifest (tabs, card, settings, tutorial)
programming/app.py         the server: pages, the Terminal socket and API, Claude sessions API
programming/auth.py        signed identities, the admin-only check, the standalone login
programming/terminal.py    shells on PTYs (from the core's src/terminal.py)
programming/claude_sessions.py   the transcript reader (from the core's src/claude_code_sessions.py)
programming/config.py      settings and per-user preferences in MOD_DATA_DIR
programming/pages/         the HTML pages
programming/static/        terminal.js, sessions.js, md.js, CSS, xterm.js (MIT, see its LICENSE files)
```

Mod pages run in a sandboxed frame with an opaque origin: every URL is relative (the frame URL
carries the core's token), there is no localStorage (preferences are kept by the mod), and the
page cannot read Odysseus's theme (it uses Odysseus's default colors, light or dark).

## What is next

The rest of the "Programming" group is still in the core, because it is wired into the chat and
the agent loop rather than being a page:

- the `claude_code` tool (Claude Code, OpenCode, Antigravity via `qemu-x86_64 -cpu max`), its
  background runs that post back into the chat, restart reattachment and stall checks;
- plan approvals (`claude_code_approvals`, native plans, the overlay and SMS approve paths);
- `/claude attach` and Claude direct (a chat that replies straight from a Claude Code session);
- the Background page and DevOps' coder statistics;
- Code (code-server) needs a same-origin frame (VS Code uses storage and service workers), which
  a sandboxed mod page does not get.

Moving those needs core APIs first (a mod tool that runs in the background and posts its result
into the chat, chat approvals for mods, a chat engine hook). The plan is in the Odysseus PR that
removed the built-in pages.

## License

AGPL-3.0, like Odysseus (the Terminal and the transcript reader came from it). xterm.js and its
addons are MIT (`programming/static/xterm/LICENSE-*.txt`).
