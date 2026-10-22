// A small Markdown renderer for Claude's replies on the Claude sessions page.
// Everything is HTML-escaped first; only then are a few formats applied:
// fenced code, headings, lists, quotes, `code`, **bold**, *italic* and
// http(s) links (opened in a new tab without a referrer). Anything else,
// HTML included, shows as text.

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function inline(s) {
  // s is escaped already. Code spans first, so nothing inside them is formatted.
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, '$1<em>$2</em>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}

export function mdToHtml(text) {
  const lines = esc(text).split('\n');
  const out = [];
  let para = [];
  let list = null;         // {tag, items}
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; } };
  const flushList = () => {
    if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`); list = null; }
  };
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const fence = ln.match(/^\s*```/);
    if (fence) {
      flushPara(); flushList();
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      out.push(`<pre><code>${code.join('\n')}</code></pre>`);
      continue;
    }
    const h = ln.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flushPara(); flushList(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); continue; }
    const ul = ln.match(/^\s*[-*+]\s+(.*)$/);
    const ol = ln.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const tag = ul ? 'ul' : 'ol';
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [] };
      list.items.push((ul || ol)[1]);
      continue;
    }
    const q = ln.match(/^&gt;\s?(.*)$/);
    if (q) { flushPara(); flushList(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); continue; }
    if (!ln.trim()) { flushPara(); flushList(); continue; }
    flushList();
    para.push(ln);
  }
  flushPara(); flushList();
  return out.join('');
}
