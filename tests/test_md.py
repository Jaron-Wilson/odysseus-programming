"""static/md.js, the Markdown in Claude's replies: formats a little and lets
no HTML or script through. Runs with node, when it is installed."""
import json
import os
import shutil
import subprocess

import pytest

from tests.conftest import ROOT

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="needs node")


def render(text):
    md = os.path.join(ROOT, "programming", "static", "md.js")
    js = (f"import({json.dumps('file://' + md)}).then(m => "
          f"process.stdout.write(m.mdToHtml({json.dumps(text)})))")
    return subprocess.run([NODE, "--input-type=module", "-e", js], capture_output=True, text=True,
                          check=True, timeout=30).stdout


def test_it_formats_the_usual_things():
    out = render("# Plan\n\nSome **bold**, *it* and `x < y`.\n\n- one\n- two\n\n```\na <b>\n```\n"
                 "[docs](https://example.com/a?b=1&c=2)")
    assert "<h2>Plan</h2>" in out and "<strong>bold</strong>" in out and "<em>it</em>" in out
    assert "<code>x &lt; y</code>" in out and "<ul><li>one</li><li>two</li></ul>" in out
    assert "<pre><code>a &lt;b&gt;</code></pre>" in out
    assert '<a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">docs</a>' in out


@pytest.mark.parametrize("evil", [
    "<img src=x onerror=alert(1)>",
    "<script>alert(1)</script>",
    "[x](javascript:alert(1))",
    '[x](https://a.example/" onmouseover="alert(1))',
    "`<b>`**<i>**",
])
def test_no_html_gets_through(evil):
    out = render(evil)
    assert "<img" not in out and "<script" not in out and "javascript:" not in out.replace("[x](javascript:", "")
    assert 'onmouseover="' not in out and "<i>" not in out and "<b>" not in out
