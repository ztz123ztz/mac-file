"""Shared environment helpers for the mac skill scripts.

Imported by sibling scripts (``mac_run.py``, ``mac_setup.py``) — same
directory, no package needed. Covers the two known-hostile installs:

1. aider-chat wheel metadata lies twice: ``requires-python <3.13`` (false —
   the wheel runs fine) and a hard ``numpy==1.26.4`` pin (would downgrade
   the CAD stack). Install with ``--ignore-requires-python --no-deps``.
2. Python >= 3.13 removed stdlib ``audioop``, which aider reaches via pydub.
   Shim it from pydub's vendored fallback when missing.

Everything here is best-effort: failure returns False and the engine falls
back to direct-API repair. Never raise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

AIDER_EXTRA_DEPS = ["json5", "Pillow", "pypandoc", "pydub"]

# Module name -> pip package for the iterative resolver below.
_MODULE_TO_PACKAGE = {
    "PIL": "Pillow",
    "yaml": "pyyaml",
    "pypandoc": "pypandoc",
    "pydub": "pydub",
    "json5": "json5",
    "mixpanel": "mixpanel",
    "watchfiles": "watchfiles",
    "tree_sitter": "tree-sitter",
    "grep_ast": "grep-ast",
    "litellm": "litellm",
    "diskcache": "diskcache",
    "jsonlines": "jsonlines",
    "prompt_toolkit": "prompt_toolkit",
    "rich": "rich",
}


def _run(python: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([python, *args], capture_output=True, text=True,
                          timeout=timeout)


def _pip(python: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """pip with proxy fallback: first as-is, then with proxy vars stripped
    (direct PyPI). Returns the first success, else the last result."""
    r = _run(python, "-m", "pip", *args, timeout=timeout)
    if r.returncode == 0:
        return r
    import os as _os
    env = {k: v for k, v in _os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy",
                                "no_proxy")}
    try:
        r2 = subprocess.run(
            [python, "-m", "pip", *args], capture_output=True, text=True,
            timeout=timeout, env={**env, "PATH": _os.environ.get("PATH", "")})
        return r2 if r2.returncode == 0 else r
    except Exception:
        return r


def site_packages(python: str) -> str | None:
    try:
        r = _run(python, "-c", "import site; print(site.getsitepackages()[0])",
                 timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def aider_import_check(python: str) -> tuple[bool, str]:
    """True if ``from aider.coders import Coder`` works. Returns (ok, note)."""
    try:
        r = _run(python, "-c", "from aider.coders import Coder; print('aider ok')",
                 timeout=120)
    except Exception as exc:
        return False, str(exc)[:200]
    if r.returncode == 0:
        return True, "import ok"
    out = ((r.stderr or "") + "\n" + (r.stdout or "")).strip().splitlines()
    return False, (out[-1] if out else "unknown error")[:200]


def install_aider_guarded(python: str) -> tuple[bool, str]:
    """Install aider with both metadata lies bypassed. Returns (ok, note)."""
    r = _run(python, "-m", "pip", "install", "--no-deps",
             "--ignore-requires-python", "aider-chat==0.82.3", timeout=900)
    if r.returncode != 0:
        tail = ((r.stderr or "").strip().splitlines() or ["pip failed"])[-1][:200]
        return False, f"pip failed: {tail}"
    return True, "wheel installed"

def repair_aider_import(python: str) -> tuple[bool, str]:
    """Iterative self-healing resolver (max 10 rounds).

    Each round: try import -> parse the missing top-level module from the
    error -> pip install its package -> retry. Then the 3.13 audioop shim.
    Bounded and version-agnostic (no pinned tree to rot).
    """
    import re as _re

    _pip(python, "install", "--quiet", *AIDER_EXTRA_DEPS, timeout=600)
    for _round in range(10):
        ok, note = aider_import_check(python)
        if ok:
            return True, "repaired via extras" if _round == 0 else f"repaired round {_round}"
        m = _re.search(r"No module named '([\w\.]+)'", note)
        if not m:
            break
        top = m.group(1).split(".")[0]
        if top == "audioop":
            break  # handled below, not a pip package
        pkg = _MODULE_TO_PACKAGE.get(top, top)
        print(f"  [mac_env] missing module '{top}' -> pip install {pkg} ...", flush=True)
        _pip(python, "install", "--quiet", pkg, timeout=600)
    ok, note = aider_import_check(python)
    if ok:
        return True, "repaired iteratively"
    if "audioop" not in note:
        return False, note
    sp = site_packages(python)
    if not sp:
        return False, note
    try:
        probe = _run(python, "-c", "import audioop", timeout=60)
        if probe.returncode == 0:
            return False, note  # audioop exists; something else is wrong
        Path(sp, "audioop.py").write_text(
            "# audioop shim for Python >= 3.13 (stdlib module removed).\n"
            "# Backed by pydub's vendored fallback implementation.\n"
            "from pydub.pyaudioop import *\n", encoding="utf-8")
    except OSError as exc:
        return False, f"shim write failed: {exc}"
    ok2, note2 = aider_import_check(python)
    return (True, "repaired via audioop shim") if ok2 else (False, note2)


def ensure_aider(python: str) -> tuple[bool, str]:
    """Full flow: check -> install -> repair -> check. Never raises."""
    ok, note = aider_import_check(python)
    if ok:
        return True, "already importable"
    ok, note = install_aider_guarded(python)
    if not ok:
        return False, note
    ok, note = aider_import_check(python)
    if ok:
        return True, "installed"
    return repair_aider_import(python)


if __name__ == "__main__":
    py = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    ok, note = ensure_aider(py)
    print(f"aider usable: {ok} ({note})")
