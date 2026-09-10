#!/usr/bin/env python3
"""mac_setup.py — friendly first-run wizard for the `mac` skill.

Run once after installing the skill. It checks the machine, asks for the
user's own AI key (once), stores it with owner-only permissions, prepares
the Python environment, and verifies the CAD kernel imports.

    python scripts/mac_setup.py [--bundle URL-or-path] [--key BUNDLE-KEY]

Non-coders: your AI assistant runs this and relays the ONE question below.
After this, `mac_run.py` never asks again.

Key storage: ~/.config/mac-skill/env (mode 0600, KEY=VALUE lines).
Resolution order everywhere: env var > config file > interactive prompt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows cp936 garbles CJK output
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WINDOWS = os.name == "nt"

CONFIG_DIR = Path.home() / ".config" / "mac-skill"
CONFIG_FILE = CONFIG_DIR / "env"


def say(msg: str) -> None:
    print(msg, flush=True)


def load_saved() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return out


def save_kv(updates: dict[str, str]) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    current = load_saved()
    current.update({k: v for k, v in updates.items() if v})
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# mac-skill private config. DO NOT share this file.\n")
        for k, v in current.items():
            f.write(f"{k}={v}\n")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass  # Windows NTFS ignores POSIX modes; see SKILL.md
    return CONFIG_FILE


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def check_python() -> str:
    if sys.version_info < (3, 11):
        raise SystemExit(f"need Python >= 3.11, found {sys.version}")
    say(f"Python OK: {sys.version.split()[0]}")
    return sys.executable


def check_tool(name: str, required: bool = True) -> bool:
    found = shutil.which(name) is not None
    say(f"{'OK' if found else 'MISSING'}: {name}")
    if not found and required:
        raise SystemExit(f"please install {name} first (https://{name}.scm.io  or apt/brew)")
    return found


def ensure_ai_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip() or load_saved().get("DASHSCOPE_API_KEY", "")
    if not key:
        say("")
        say("I need YOUR OWN AI model key (one time only).")
        say("Any OpenAI-compatible key works. Easiest: a SiliconFlow key")
        say("(console.siliconflow.cn → API keys → create → top up a few yuan).")
        say("Paste it below (input hidden if your terminal supports it):")
        try:
            import getpass
            key = getpass.getpass("DASHSCOPE_API_KEY: ").strip()
        except Exception:
            key = ask("DASHSCOPE_API_KEY: ")
        if not key:
            raise SystemExit("no key provided — run me again when ready.")
    save_kv({"DASHSCOPE_API_KEY": key})
    say(f"Key remembered in {CONFIG_FILE} (only you can read it).")
    return key


def ensure_bundle_key(bundle: str | None, key: str | None) -> str | None:
    if not bundle:
        return None
    key = key or load_saved().get("MAC_BUNDLE_KEY", "")
    if key:
        say("Bundle key: found (stored).")
        return key
    say("This bundle needs its distribution key (sent to you privately).")
    key = ask("Bundle key: ")
    if not key:
        raise SystemExit("no bundle key — ask whoever sent you the skill.")
    save_kv({"MAC_BUNDLE_KEY": key})
    return key


def ensure_cad_stack(python: str) -> None:
    """Install/heal the CAD runtime idempotently (pip streams progress, minutes).

    Windows: native pip wheels for trimesh/rtree/OCP are unreliable — if pip
    fails, follow the conda route (printed) instead of retrying pip.
    """
    say("Checking CAD runtime (build123d + OCP + ezdxf + web viewer)...")
    probe = subprocess.run(
        [python, "-c", "import build123d, ezdxf, fastapi, uvicorn; print('runtime ok')"],
        capture_output=True, text=True, timeout=120)
    if probe.returncode == 0:
        say("CAD runtime: already installed.")
        return
    say("Installing CAD runtime (one time, ~1.5GB, a few minutes)...")
    say("pip output streams below — long silence with network activity is normal, do NOT Ctrl-C.")
    r = subprocess.run(
        [python, "-m", "pip", "install",
         "numpy>=2,<2.3", "scipy>=1.10", "scikit-learn>=1.3",
         "trimesh>=4.0", "rtree>=1.1",
         "build123d>=0.8", "langgraph>=0.2,<0.3",
          "langgraph-checkpoint>=2.0,<3.0", "pydantic>=2.5",
          "openai>=1.20.0", "ezdxf>=1.0",
          "fastapi>=0.110", "uvicorn>=0.27",
          "cryptography>=41"],
        timeout=1800)
    if r.returncode != 0:
        if IS_WINDOWS:
            raise SystemExit(
                "pip install failed on Windows (expected: native trimesh/rtree/OCP "
                "wheels are unreliable). Use the conda route instead:\n"
                "  conda env create -f environment.yml   (from the MAC repo)\n"
                "  conda activate multi_agent_cad\n"
                "then re-run me with --python <conda python>.")
        raise SystemExit("pip install failed — check network, then re-run me.")
    r = subprocess.run(
        [python, "-m", "pip", "install", "--no-deps",
         "--ignore-requires-python", "aider-chat==0.82.3"], timeout=600)
    if r.returncode != 0:
        say("WARNING: aider install failed — continuing WITHOUT it. "
            "The pipeline auto-falls-back to direct-API repair (works, costs more tokens).")
        return
    # Aider is OPTIONAL (shared helper handles hostile wheel + 3.13 shim).
    try:
        from mac_env import ensure_aider
    except ImportError:
        ensure_aider = None  # type: ignore[assignment]
    if ensure_aider is None:
        say("WARNING: mac_env helper missing — skipping aider verification. "
            "The pipeline auto-falls-back to direct-API repair.")
        return
    ok, note = ensure_aider(python)
    if ok:
        say(f"CAD runtime: installed (aider verified: {note}).")
    else:
        say(f"WARNING: aider unavailable ({note}) — continuing WITHOUT it. "
            "The pipeline auto-falls-back to direct-API repair (works, costs more tokens).")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="First-run setup for the mac skill.")
    ap.add_argument("--bundle", default=None)
    ap.add_argument("--key", default=None)
    ap.add_argument("--python", default=None, help="interpreter to prepare (default: this one)")
    args = ap.parse_args(argv)

    say("== mac-skill setup ==")
    python = args.python or check_python()
    check_tool("git", required=False)
    check_tool("openssl", required=False)
    ensure_ai_key()
    ensure_bundle_key(args.bundle, args.key)
    ensure_cad_stack(python)
    say("")
    say("Setup complete. Next, run a model, e.g.:")
    say('  python scripts/mac_run.py --prompt "Create a 50x50x6mm base plate." --bom')
    say("After STEP completes you will be offered DXF / BOM / SPEC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
