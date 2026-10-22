// Claude sessions: read the Claude Code sessions on this host like chats
// (programming/app.py, programming/claude_sessions.py).
//
// Asked for: browse and read his Claude Code sessions in Odysseus, which
// until now meant opening the Terminal. Moved out of the Odysseus core
// (static/js/claudeSessions.js) into the Programming mod: it is the mod's
// Claude sessions tab, a sandboxed frame, so URLs are relative and the
// project filter is kept by the mod (api/prefs).
//
// On the left every session, live ones first, with a search box and a
// project filter; on the right the chosen one as a chat: the user's
// prompts, Claude's replies, and each tool call folded to its name and a
// one-line summary, with its result (cut short) inside. A live session is
// followed every few seconds; older turns load on scroll up. On a phone the
// list and the transcript take turns, with a Back button.
// ?project=<project>&id=<session id> opens one session (a link from
// elsewhere: #mod/programming/sessions?path=/sessions?project=...&id=...).
// "Copy /claude attach" copies the command that hands the session to a chat.

import { mdToHtml, esc } from './md.js';

const API = 'api/claude_sessions';
const LIST_MS = 10000;
const LIVE_MS = 3000;
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function clock(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString([], today ? { hour: 'numeric', minute: '2-digit' }
    : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const shortCwd = (cwd) => String(cwd || '').replace(/^\/home\/[^/]+/, '~');

async function getJson(url) {
  const res = await fetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

const md = (text) => mdToHtml(text);

function toast(msg, ms = 2600) {
  document.querySelector('.toast')?.remove();
  const el = document.createElement('div');
  el.className = 'toast';
  el.setAttribute('role', 'status');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// A sandboxed frame may not get the async clipboard; fall back to the old way.
async function copyText(text) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('no clipboard');
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    ta.remove();
    return ok;
  }
}

function saveProject() {
  fetch('api/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionsProject: _project }) }).catch(() => {});
}

// ── State ────────────────────────────────────────────────────────────────
let _panel = null;
let _listTimer = null;
let _liveTimer = null;
let _sessions = [];
let _q = '';
let _project = '';
// The open transcript: { project, id, start, end, live, loadingOlder, results: Map }
let _cur = null;

const $ = (sel) => _panel && _panel.querySelector(sel);

// ── The list ─────────────────────────────────────────────────────────────
function projectName(s) {
  const c = shortCwd(s.cwd);
  return c.split('/').filter(Boolean).pop() || c || s.project;
}

function filtered() {
  const q = _q.trim().toLowerCase();
  return _sessions.filter((s) => (!_project || s.project === _project)
    && (!q || [s.title, s.cwd, s.git_branch, s.first_prompt, s.last_prompt, s.id, s.job && s.job.name]
      .some((v) => v && String(v).toLowerCase().includes(q))));
}

function paintProjects() {
  const sel = $('.cs-project');
  if (!sel) return;
  const byProject = new Map();
  for (const s of _sessions) {
    if (!byProject.has(s.project)) byProject.set(s.project, { cwd: s.cwd, n: 0 });
    byProject.get(s.project).n += 1;
  }
  if (_project && !byProject.has(_project)) _project = '';
  const opts = [...byProject.entries()].sort((a, b) => shortCwd(a[1].cwd).localeCompare(shortCwd(b[1].cwd)));
  sel.innerHTML = `<option value="">All projects (${_sessions.length})</option>`
    + opts.map(([p, v]) => `<option value="${esc(p)}"${p === _project ? ' selected' : ''}>${esc(shortCwd(v.cwd))} (${v.n})</option>`).join('');
}

function paintList() {
  const box = $('.cs-list');
  if (!box) return;
  const rows = filtered();
  if (!_sessions.length) {
    box.innerHTML = '<div class="empty">No Claude Code sessions on this host yet.</div>';
    return;
  }
  if (!rows.length) {
    box.innerHTML = '<div class="empty">Nothing matches.</div>';
    return;
  }
  box.innerHTML = rows.map((s) => `
    <button type="button" class="cs-item${_cur && _cur.id === s.id && _cur.project === s.project ? ' active' : ''}"
      data-project="${esc(s.project)}" data-id="${esc(s.id)}">
      <span class="cs-item-top">
        ${s.live ? '<span class="dot running" title="Live: written to in the last 2 minutes"></span>' : ''}
        <span class="cs-item-title">${esc(s.title)}</span>
      </span>
      <span class="cs-item-meta">
        <span class="cs-item-proj" title="${esc(s.cwd)}">${esc(projectName(s))}</span>
        ${s.git_branch && s.git_branch !== 'HEAD' ? `<span class="cs-chip" title="Git branch">${esc(s.git_branch)}</span>` : ''}
        ${s.job && s.job.state ? `<span class="cs-chip" title="Background job">${esc(s.job.state)}</span>` : ''}
      </span>
      <span class="cs-item-meta"><span>${esc(ago(s.last_activity))}</span><span>${s.message_count.toLocaleString()} messages</span></span>
    </button>`).join('');
}

async function refreshList() {
  if (!_panel) return;
  try {
    const d = await getJson(API);
    _sessions = d.sessions || [];
  } catch (e) {
    const box = $('.cs-list');
    if (box && !_sessions.length) box.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`;
    return;
  }
  if (!_panel) return;
  paintProjects();
  paintList();
  if (_cur) {
    const s = _sessions.find((x) => x.id === _cur.id && x.project === _cur.project);
    if (s) paintHead(s);
  }
}

// ── The transcript ───────────────────────────────────────────────────────
function paintHead(s) {
  const head = $('.cs-view-head');
  if (!head || !s) return;
  _cur.live = !!s.live;
  head.innerHTML = `
    <button type="button" class="cs-back" aria-label="Back to the list">‹ Sessions</button>
    <div class="cs-view-title">
      <div class="cs-view-name">${s.live ? '<span class="dot running"></span>' : ''}${esc(s.title)}</div>
      <div class="cs-view-sub">
        <code title="${esc(s.cwd)}">${esc(shortCwd(s.cwd))}</code>
        ${s.git_branch ? ` · <span class="cs-chip">${esc(s.git_branch)}</span>` : ''}
        · ${s.message_count.toLocaleString()} messages · ${s.live ? 'live' : esc(ago(s.last_activity))}
        ${s.job && s.job.name ? ` · job <b>${esc(s.job.name)}</b> (${esc(s.job.state)})` : ''}
      </div>
    </div>
    <div class="cs-head-actions">
      <button type="button" class="cs-copy" data-copy="claude --resume ${esc(s.id)}" title="Copy the command that resumes this session in a terminal">Copy resume</button>
      <button type="button" class="cs-copy cs-attach" data-copy="/claude attach ${esc(s.id)}" data-copy-note="Copied. Send it in a chat to let that chat's coding agent carry this session on." title="Copy /claude attach: send it in a chat to let that chat's coding agent carry this session on">Copy /claude attach</button>
    </div>`;
  syncLiveTimer();
}

function resultHtml(r) {
  return `<div class="cs-result${r.is_error ? ' cs-error' : ''}"><pre>${esc(r.text || '(no output)')}</pre>${r.truncated ? '<div class="cs-cut">Cut short.</div>' : ''}</div>`;
}

function turnEl(t) {
  const el = document.createElement('div');
  const when = clock(t.ts);
  const time = when ? `<span class="cs-time">${esc(when)}</span>` : '';
  if (t.kind === 'user') {
    el.className = 'cs-turn cs-user' + (t.sidechain ? ' cs-sidechain' : '');
    el.innerHTML = `<div class="cs-bubble"><div class="cs-plain">${esc(t.text)}</div></div><div class="cs-who">You ${time}</div>`;
  } else if (t.kind === 'assistant') {
    el.className = 'cs-turn cs-assistant' + (t.sidechain ? ' cs-sidechain' : '');
    el.innerHTML = `<div class="cs-who">Claude${t.model && t.model !== '<synthetic>' ? ` <span class="cs-model">${esc(t.model)}</span>` : ''} ${time}</div>
      <div class="cs-text">${md(t.text)}</div>`;
  } else if (t.kind === 'tool') {
    el.className = 'cs-turn cs-tool';
    el.dataset.toolId = t.id || '';
    const r = _cur.results.get(t.id);
    el.innerHTML = `<details><summary><span class="cs-tool-name">${esc(t.name)}</span>
      <span class="cs-tool-sum">${esc(t.summary)}</span><span class="cs-tool-state">${r ? (r.is_error ? 'error' : '') : '…'}</span></summary>
      <div class="cs-tool-body">${r ? resultHtml(r) : '<div class="cs-cut">No result yet.</div>'}</div></details>`;
    if (r && r.is_error) el.classList.add('cs-tool-error');
  } else if (t.kind === 'summary') {
    el.className = 'cs-turn cs-note';
    el.innerHTML = `<details><summary>Summary of the earlier conversation</summary><div class="cs-plain">${esc(t.text)}</div></details>`;
  } else {
    el.className = 'cs-turn cs-note';
    el.innerHTML = `<span>${esc(t.text)}</span> ${time}`;
  }
  return el;
}

// A result fills in its tool call wherever that is on the page; one whose
// call isn't loaded yet waits in `results` for it.
function addResult(r) {
  _cur.results.set(r.id, r);
  const box = $('.cs-turns');
  const el = box && box.querySelector(`.cs-tool[data-tool-id="${CSS.escape(r.id)}"]`);
  if (!el) return false;
  el.querySelector('.cs-tool-body').innerHTML = resultHtml(r);
  el.querySelector('.cs-tool-state').textContent = r.is_error ? 'error' : '';
  el.classList.toggle('cs-tool-error', !!r.is_error);
  return true;
}

function fragmentFor(turns) {
  // Results first: a call's result comes a line after the call.
  for (const t of turns) if (t.kind === 'tool_result') _cur.results.set(t.id, t);
  const frag = document.createDocumentFragment();
  for (const t of turns) if (t.kind !== 'tool_result') frag.appendChild(turnEl(t));
  return frag;
}

function paintOlderBar() {
  const bar = $('.cs-older');
  if (!bar || !_cur) return;
  bar.hidden = !_cur.start;
  bar.textContent = _cur.loadingOlder ? 'Loading earlier messages…' : 'Load earlier messages';
}

async function openSession(project, id) {
  _cur = { project, id, start: 0, end: 0, live: false, loadingOlder: false, results: new Map() };
  _panel.classList.add('cs-reading');
  paintList();
  const view = $('.cs-view');
  view.innerHTML = `<div class="cs-view-head"></div>
    <div class="cs-scroll"><button type="button" class="cs-older" hidden></button><div class="cs-turns"><div class="empty">Loading…</div></div></div>`;
  const cur = _cur;
  let d;
  try {
    d = await getJson(`${API}/${encodeURIComponent(project)}/${encodeURIComponent(id)}`);
  } catch (e) {
    if (_cur === cur) $('.cs-turns').innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`;
    return;
  }
  if (_cur !== cur || !_panel) return;
  cur.start = d.start;
  cur.end = d.end;
  paintHead(d.session);
  const box = $('.cs-turns');
  box.innerHTML = '';
  box.appendChild(fragmentFor(d.turns || []));
  if (!(d.turns || []).length) box.innerHTML = '<div class="empty">Nothing to show in this session yet.</div>';
  paintOlderBar();
  const sc = $('.cs-scroll');
  sc.scrollTop = sc.scrollHeight;
}

async function loadOlder() {
  const cur = _cur;
  if (!cur || !cur.start || cur.loadingOlder) return;
  cur.loadingOlder = true;
  paintOlderBar();
  try {
    const d = await getJson(`${API}/${encodeURIComponent(cur.project)}/${encodeURIComponent(cur.id)}?before=${cur.start}`);
    if (_cur !== cur || !_panel) return;
    const sc = $('.cs-scroll');
    const fromBottom = sc.scrollHeight - sc.scrollTop;
    const box = $('.cs-turns');
    box.querySelector(':scope > .empty')?.remove();
    box.insertBefore(fragmentFor(d.turns || []), box.firstChild);
    cur.start = d.start;
    sc.scrollTop = sc.scrollHeight - fromBottom;
  } catch (e) {
    toast(`Could not load earlier messages: ${e.message}`);
  } finally {
    cur.loadingOlder = false;
    paintOlderBar();
  }
}

async function pollLive() {
  const cur = _cur;
  if (!cur || !_panel || document.visibilityState !== 'visible') return;
  let d;
  try {
    d = await getJson(`${API}/${encodeURIComponent(cur.project)}/${encodeURIComponent(cur.id)}/updates?after=${cur.end}`);
  } catch (_) { return; }
  if (_cur !== cur || !_panel) return;
  if (d.reset) { openSession(cur.project, cur.id); return; }
  cur.end = d.end;
  const turns = d.turns || [];
  if (turns.length) {
    const sc = $('.cs-scroll');
    const atBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
    const box = $('.cs-turns');
    box.querySelector(':scope > .empty')?.remove();
    const rest = turns.filter((t) => t.kind !== 'tool_result' || !addResult(t));
    box.appendChild(fragmentFor(rest));
    if (atBottom) sc.scrollTop = sc.scrollHeight;
  }
  if (d.session) paintHead(d.session);
}

function syncLiveTimer() {
  const want = !!(_cur && _cur.live && _panel);
  if (want && !_liveTimer) _liveTimer = setInterval(pollLive, LIVE_MS);
  if (!want && _liveTimer) {
    clearInterval(_liveTimer);
    _liveTimer = null;
    // One last look, so the end of the session shows.
    pollLive();
  }
}

function backToList() {
  _cur = null;
  syncLiveTimer();
  _panel.classList.remove('cs-reading');
  $('.cs-view').innerHTML = '<div class="cs-pick empty">Pick a session to read it.</div>';
  paintList();
}

// ── The page ─────────────────────────────────────────────────────────────
export async function open(project, id) {
  if (!_panel) {
    try {
      const r = await fetch('api/prefs');
      if (r.ok) _project = ((await r.json()).prefs || {}).sessionsProject || '';
    } catch (_) { /* all projects */ }
    _panel = document.createElement('div');
    _panel.className = 'cs-root page';
    _panel.innerHTML = `
      <div class="cs-panel" role="region" aria-label="Claude sessions">
        <div class="cs-body">
          <div class="cs-side">
            <div class="cs-filters">
              <input type="search" class="cs-search" placeholder="Search titles, folders, branches" aria-label="Search sessions" value="${esc(_q)}">
              <select class="cs-project" aria-label="Project"><option value="">All projects</option></select>
            </div>
            <div class="cs-list"><div class="empty">Loading…</div></div>
          </div>
          <div class="cs-view"><div class="cs-pick empty">Pick a session to read it.</div></div>
        </div>
      </div>`;
    document.body.appendChild(_panel);
    _panel.addEventListener('click', (ev) => {
      const item = ev.target.closest('.cs-item');
      if (item) { openSession(item.dataset.project, item.dataset.id); return; }
      if (ev.target.closest('.cs-back')) { backToList(); return; }
      if (ev.target.closest('.cs-older')) { loadOlder(); return; }
      const copy = ev.target.closest('[data-copy]');
      if (copy) {
        copyText(copy.dataset.copy).then((ok) => toast(ok
          ? (copy.dataset.copyNote || 'Copied. Paste it in a terminal to carry on this session.')
          : copy.dataset.copy, ok ? 2600 : 8000));
      }
    });
    $('.cs-search').addEventListener('input', (ev) => { _q = ev.target.value; paintList(); });
    $('.cs-project').addEventListener('change', (ev) => {
      _project = ev.target.value;
      saveProject();
      paintList();
    });
    // Older turns load when the top is reached.
    $('.cs-view').addEventListener('scroll', (ev) => {
      if (ev.target.classList?.contains('cs-scroll') && ev.target.scrollTop < 40) loadOlder();
    }, true);
  }
  refreshList();
  if (!_listTimer) _listTimer = setInterval(() => { if (document.visibilityState === 'visible') refreshList(); }, LIST_MS);
  if (typeof project === 'string' && typeof id === 'string' && project && id) openSession(project, id);
}

function init() {
  const q = new URLSearchParams(location.search);
  open(q.get('project') || undefined, q.get('id') || undefined);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

window.claudeSessions = { open };
