"""Shared test setup: the mod runs as if under Odysseus (a fake core URL and
token), with its data in a temporary folder and the core's answer to
GET /api/mods/v1/me stubbed."""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("MOD_DATA_DIR", tempfile.mkdtemp(prefix="programming-test-"))
os.environ["ODYSSEUS_URL"] = "http://127.0.0.1:9"        # never reached: _fetch_me is stubbed
os.environ["ODYSSEUS_MOD_TOKEN"] = "odm_test-token"
os.environ["ODYSSEUS_MOD_ID"] = "programming"

from programming import auth, terminal  # noqa: E402

TOKEN = os.environ["ODYSSEUS_MOD_TOKEN"]
USER = "jaron"


def ident(user=USER, mod="programming", ts=None, token=TOKEN):
    return {k.lower(): v for k, v in auth.sign_identity(token, mod, user, ts).items()}


@pytest.fixture(autouse=True)
def _core(monkeypatch, tmp_path):
    """Under the core, admin only, a plain bash in a throwaway home."""
    monkeypatch.setenv("ODYSSEUS_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MOD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(auth, "_fetch_me", lambda: {"id": "programming", "admin_only": True, "access": "admins"})
    auth._me.update(at=0.0, ok=False, why="")
    monkeypatch.setenv("SHELL", "/bin/bash")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(terminal, "home_dir", lambda: str(home))
    yield
    terminal.manager.close_all()
    auth._me.update(at=0.0, ok=False, why="")
