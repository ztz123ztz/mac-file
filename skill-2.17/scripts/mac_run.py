#!/usr/bin/env python3
"""mac_run.py — one-command MAC pipeline driver for the `mac` skill.

Clones (if needed) + configures + runs the MAC text-to-CAD pipeline and
harvests all artifacts into an output directory. No MAC checkout required
up front; no conda required (plain venv + pip works).

Usage::

    python scripts/mac_run.py --prompt "Create a 50x50x6mm base plate..." \\
        --repo ~/mac-work/Multi-Agent-CAD --python ~/mac-work/.venv/bin/python \\
        --out ./out --bom --dxf --spec

First run bootstraps: git clone (unless --repo exists), venv + pip install
(unless --python given), then runs. Subsequent runs reuse both.

Exit codes: 0 = pipeline done (see run-summary.json); 2 = setup/pipeline error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows cp936 garbles CJK output
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WINDOWS = os.name == "nt"


def _decrypt_aes256cbc(data: bytes, password: str) -> bytes:
    """Decrypt openssl-enc-compatible AES-256-CBC/PBKDF2 payload.

    Pure-Python path first (Windows-safe, no openssl CLI needed), CLI fallback.
    Layout produced by ``openssl enc -aes-256-cbc -pbkdf2``: b"Salted__" +
    8-byte salt + ciphertext, key/IV = PBKDF2-HMAC-SHA256(pass, salt, 10k).
    """
    if not data.startswith(b"Salted__"):
        raise ValueError("not an openssl-enc payload")
    salt = data[8:16]
    ciphertext = data[16:]
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=48, salt=salt,
                         iterations=10000)
        key_iv = kdf.derive(password.encode("utf-8"))
        decryptor = Cipher(algorithms.AES(key_iv[:32]),
                           modes.CBC(key_iv[32:])).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        pad_len = padded[-1]
        if pad_len < 1 or pad_len > 16:
            raise ValueError("bad padding (wrong key?)")
        return padded[:-pad_len]
    except ImportError:
        pass
    if shutil.which("openssl") is None:
        raise RuntimeError("need `cryptography` (pip install cryptography) or openssl CLI")
    r = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
         "-pass", f"pass:{password}"],
        input=data, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"openssl decrypt failed: {r.stderr.decode()[-300:]}")
    return r.stdout

REPO_URL = "https://github.com/ztz123ztz/mac-text"
MAC_DEPS = ["build123d>=0.8", "langgraph>=0.2,<0.3",
            "langgraph-checkpoint>=2.0,<3.0", "pydantic>=2.5",
            "openai>=1.20.0", "ezdxf>=1.0", "fastapi>=0.110",
            "uvicorn>=0.27"]
# NOTE: aider-chat is deliberately ABSENT above. Its wheel metadata lies in
# two ways (requires-python <3.13; hard numpy==1.26.4 pin) and would poison
# the whole resolve. It is installed separately below with both guards off,
# then import-verified (see _ensure_aider_importable).

# aider runtime extras that pip --no-deps skips but `from aider.coders`
# actually needs at import time.
AIDER_EXTRA_DEPS = ["json5", "Pillow", "pypandoc", "pydub"]


def _site_packages(python: str) -> str | None:
    try:
        r = subprocess.run(
            [python, "-c", "import site; print(site.getsitepackages()[0])"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _ensure_aider_importable(python: str) -> tuple[bool, str]:
    """Verify `from aider.coders import Coder`, repairing known env issues.

    Returns (ok, note). Repairs, in order: (1) missing runtime deps from
    --no-deps installs; (2) Python >= 3.13 removed stdlib ``audioop`` — shim
    it from pydub's vendored fallback. Never raises; failure just means the
    engine will use direct-API repair instead.
    """
    def _try() -> tuple[bool, str]:
        r = subprocess.run(
            [python, "-c", "from aider.coders import Coder; print('aider ok')"],
            capture_output=True, text=True, timeout=120)
        out = (r.stderr or "") + (r.stdout or "")
        return r.returncode == 0, out

    ok, out = _try()
    if ok:
        return True, "import ok"
    if "ModuleNotFoundError" in out or "ImportError" in out:
        subprocess.run(
            [python, "-m", "pip", "install", "--quiet"] + AIDER_EXTRA_DEPS,
            capture_output=True, timeout=600)
        if "audioop" in out:
            sp = _site_packages(python)
            if sp:
                try:
                    audioop_probe = subprocess.run(
                        [python, "-c", "import audioop"],
                        capture_output=True, timeout=60)
                    if audioop_probe.returncode != 0:
                        # stdlib audioop is gone (>= 3.13): alias pydub's
                        # vendored fallback under the stdlib name.
                        with open(Path(sp) / "audioop.py", "w",
                                   encoding="utf-8") as f:
                            f.write("# audioop shim for Python >= 3.13 "
                                    "(stdlib module removed).\n"
                                    "# Backed by pydub's vendored fallback implementation.\n"
                                    "from pydub.pyaudioop import *\n")
                except OSError:
                    pass
        ok, out = _try()
        if ok:
            return True, "repaired (deps/shim)"
    tail = (out.strip().splitlines() or ["unknown error"])[-1][:200]
    return False, tail


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def _saved_keys() -> dict[str, str]:
    """Keys remembered by mac_setup.py (~/.config/mac-skill/env)."""
    out: dict[str, str] = {}
    try:
        for line in Path.home().joinpath(".config", "mac-skill", "env").read_text(
                encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return out


def _resolve_key(name: str, purpose: str) -> str:
    """env var -> remembered config -> interactive prompt -> clear error."""
    key = os.environ.get(name, "").strip() or _saved_keys().get(name, "")
    if key:
        return key
    if sys.stdin.isatty():
        try:
            key = input(f"{name} ({purpose}): ").strip()
        except EOFError:
            key = ""
        if key:
            return key
    raise SystemExit(
        f"{name} is not set. Run one of:\n"
        f"  python scripts/mac_setup.py   (asks once, remembers)\n"
        f"  export {name}=\"...\"            (this shell only)")


def _resolve_latest_bundle(bundle: str | None, manifest_url: str | None
                         ) -> tuple[str | None, str | None]:
    """Resolve the newest published bundle via the public versions manifest.

    Returns (bundle_url, version). On any failure returns (bundle, None) so
    the caller falls back to the skill-pinned URL (old behavior).
    """
    import urllib.request as _url

    manifest = manifest_url or os.environ.get("MAC_MANIFEST_URL")
    if not manifest and bundle:
        manifest = bundle.rsplit("/", 1)[0] + "/versions.json"
    if not manifest:
        manifest = DEFAULT_MANIFEST_URL
    try:
        with _url.urlopen(manifest, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("latest")
        name = data.get("bundle")
        if not latest or not name:
            return bundle, None
        base = manifest.rsplit("/", 1)[0]
        fresh_url = base + "/" + name
        if bundle and fresh_url == bundle:
            return bundle, latest  # already latest
        print(f"update channel: {latest} ({name})")
        return fresh_url, latest
    except Exception as exc:
        print(f"manifest unreachable ({manifest}), falling back to pinned bundle: {exc}")
        return bundle, None


def _do_update(repo: Path, bundle: str | None, key: str | None,
               version: str | None = None, manifest_url: str | None = None) -> int:
    """Replace an installed repo with a fresh bundle (old users' path).

    Refuses when a job.log under the repo was touched in the last 10 minutes
    (a pipeline may be writing temp_* right now). Prints the new engine
    version marker afterwards.
    """
    import time as _time

    if not bundle:
        print("update needs a bundle source: --skill DIR (with dist_bundle.json) "
              "or --bundle URL", file=sys.stderr)
        return 2
    # Always resolve the newest published bundle first: the skill-pinned URL
    # goes stale after the next release, and --update must track latest.
    bundle, manifest_version = _resolve_latest_bundle(bundle, manifest_url)
    if manifest_version:
        version = manifest_version
    if repo.exists():
        cutoff = _time.time() - 600
        for log in repo.rglob("job.log"):
            try:
                if log.stat().st_mtime > cutoff:
                    print(f"refusing update: {log} modified <10min ago "
                          f"(a run may be in flight)", file=sys.stderr)
                    return 3
            except OSError:
                pass
    fresh = repo.parent / (repo.name + ".new")
    if fresh.exists():
        shutil.rmtree(fresh)
    _install_from_bundle(fresh, bundle, key, version)
    if not ((fresh / "multi_agent_cad" / "graph.py").exists()):
        print("update failed: fresh bundle has no engine", file=sys.stderr)
        shutil.rmtree(fresh, ignore_errors=True)
        return 2
    backup = repo.parent / (repo.name + ".bak")
    if backup.exists():
        shutil.rmtree(backup)
    if repo.exists():
        repo.rename(backup)
    fresh.rename(repo)
    print(f"updated: {repo} (previous install kept at {backup} — delete when happy)")
    return 0


def ensure_repo(repo: Path, bundle: str | None = None,
                key: str | None = None, version: str | None = None) -> Path:
    if (repo / "multi_agent_cad" / "graph.py").exists():
        print(f"repo exists: {repo}")
        return repo
    if bundle:
        return _install_from_bundle(repo, bundle, key, version)
    repo.parent.mkdir(parents=True, exist_ok=True)
    r = sh(["git", "clone", "--depth", "1", REPO_URL, str(repo)])
    if r.returncode != 0:
        raise SystemExit("git clone failed")
    return repo


def _install_from_bundle(repo: Path, bundle: str, key: str | None,
                         version: str | None = None) -> Path:
    """Install from a key-gated bundle (file path, or http(s) URL).

    Anyone with the bundle FILE still needs the KEY to decrypt it — this is
    what makes private distribution work without GitHub access.
    """
    import tempfile
    import urllib.request

    key = key or os.environ.get("MAC_BUNDLE_KEY", "") or _saved_keys().get("MAC_BUNDLE_KEY", "")
    if not key:
        raise SystemExit("bundle install needs --key, MAC_BUNDLE_KEY, or a remembered key "
                         "(run scripts/mac_setup.py --bundle URL once)")
    tmpdir = Path(tempfile.mkdtemp(prefix="macbundle_"))
    enc_path = tmpdir / "bundle.tar.gz.enc"
    if bundle.startswith("file://"):
        shutil.copy2(bundle[len("file://"):], enc_path)
    elif bundle.startswith(("http://", "https://")):
        req = urllib.request.Request(bundle, headers={"User-Agent": "mac-skill/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(enc_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    else:
        shutil.copy2(bundle, enc_path)
    try:
        plain = _decrypt_aes256cbc(enc_path.read_bytes(), key)
    except Exception as exc:
        raise SystemExit(
            f"decrypt failed (wrong key or corrupted bundle): {exc}\n"
            f"If the distributor rotated keys, ask for the newest skill folder "
            f"(it carries the current key) and retry.")
    repo.parent.mkdir(parents=True, exist_ok=True)
    try:
        import tarfile
        import io as _io
        with tarfile.open(fileobj=_io.BytesIO(plain), mode="r:gz") as tar:
            tar.extractall(path=str(repo.parent))
    except Exception as exc:
        raise SystemExit(f"untar failed: {exc}")
    extracted = repo.parent / "mac-dist"
    if not (extracted / "multi_agent_cad" / "graph.py").exists():
        raise SystemExit("bundle contents invalid (no engine found)")
    if repo.exists():
        shutil.rmtree(repo)
    extracted.rename(repo)
    if version:
        try:
            (repo / ".mac-version").write_text(str(version).strip() + "\n", encoding="utf-8")
        except OSError:
            pass
    print(f"installed from bundle: {repo}")
    return repo


DEFAULT_MANIFEST_URL = ("https://raw.githubusercontent.com/ztz123ztz/"
                        "mac-file/main/versions.json")


def _do_update_skills(skill_dir: str, sklib_url: str | None) -> int:
    """Refresh sub-skills from the public library manifest.

    ``skill_dir`` is THIS installer skill's folder (sibling skills live next
    to it: <parent>/mac-bom, ...). Fetches ``<base>/skills.json`` (default
    base: the public file repo), hash-compares every tracked file, downloads
    changed/new ones. Never deletes. Prints orphans (local files the manifest
    no longer lists).
    """
    import hashlib as _hl
    import urllib.request as _url

    PUBLIC = ("mac-bom", "mac-drafting", "mac-qa-kernel", "mac-spec")
    # base = repo root: manifest lives at <base>/skills.json,
    # skill files at <base>/skills/<rel>.
    base = (sklib_url or os.environ.get("MAC_SKLIB_URL",
            "https://raw.githubusercontent.com/ztz123ztz/mac-file/main")).rstrip("/")
    parent = Path(skill_dir).expanduser().resolve().parent

    def _get(path: str) -> bytes:
        with _url.urlopen(base + "/" + path.lstrip("/"), timeout=60) as resp:
            return resp.read()

    try:
        manifest = json.loads(_get("skills.json").decode("utf-8"))
    except Exception as exc:
        print(f"skill library unreachable ({base}): {exc}", file=sys.stderr)
        return 2
    updated, current, orphans = [], [], []
    for skill in PUBLIC:
        entry = (manifest.get("skills", {}) or {}).get(skill)
        if not entry:
            print(f"- {skill}: not in manifest, skipped")
            continue
        tracked = (entry.get("files", {}) or {})
        local_root = parent / skill
        local_files: dict[str, str] = {}
        if local_root.is_dir():
            for p in sorted(local_root.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts \
                        and p.suffix != ".pyc":
                    local_files[str(p.relative_to(parent))] = _hl.sha256(
                        p.read_bytes()).hexdigest()
        for rel, want in sorted(tracked.items()):
            lp = parent / rel
            if lp.is_file() and _hl.sha256(lp.read_bytes()).hexdigest() == want:
                continue
            try:
                data = _get("skills/" + rel)
            except Exception as exc:
                print(f"- {rel}: download failed ({exc})")
                continue
            if _hl.sha256(data).hexdigest() != want:
                print(f"- {rel}: checksum mismatch, skipped")
                continue
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_bytes(data)
            updated.append(rel)
        for rel in sorted(set(local_files) - set(tracked)):
            if rel.startswith(skill + "/"):
                orphans.append(rel)
        if not any(r.startswith(skill + "/") for r in updated):
            current.append(skill)
    print(f"sub-skills updated: {len(updated)} file(s)")
    for r in updated:
        print(f"  + {r}")
    if orphans:
        print(f"orphans (local only, kept): {len(orphans)}")
        for r in orphans[:10]:
            print(f"  ? {r}")
    if current and not updated:
        print("all sub-skills already current.")
    return 0


def _do_check_update(skill_dir: str | None, repo: str | None,
                     manifest_url: str | None) -> int:
    """Compare installed versions against the public manifest. Read-only.

    Also answers the #1 user confusion — "did the key change?": compares the
    manifest's key fingerprint against the installed skill's. Agents MUST run
    this before asking the user anything about keys.
    """
    import urllib.request

    local_fp = None
    if skill_dir:
        try:
            dist = json.loads((Path(skill_dir).expanduser() / "dist_bundle.json")
                              .read_text(encoding="utf-8"))
            print(f"skill bundle version: {dist.get('version', '?')}")
            local_fp = dist.get("key_fingerprint")
        except (OSError, ValueError) as exc:
            print(f"skill unreadable: {exc}")
    if repo:
        marker = Path(repo).expanduser() / ".mac-version"
        print(f"installed engine: {marker.read_text(encoding='utf-8').strip()}"
              if marker.is_file() else "installed engine: unknown (pre-version installs)")
    url = manifest_url or os.environ.get("MAC_MANIFEST_URL", DEFAULT_MANIFEST_URL)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"manifest unreachable ({url}): {exc}")
        return 2
    print(f"latest published: {manifest.get('latest', '?')} "
          f"({manifest.get('bundle', '?')})")
    remote_fp = manifest.get("key_fingerprint")
    if remote_fp:
        if local_fp and remote_fp == local_fp:
            print("distribution key: UNCHANGED — keep using the saved key, "
                  "do NOT ask the user for a new one.")
        elif manifest.get("key_changed"):
            print("distribution key: ROTATED — the user needs the newest skill "
                  "folder (it carries the current key). Ask the distributor, "
                  "not the user, for nothing else.")
        else:
            print("distribution key: status unknown (old skill folder without "
                  "fingerprint) — try the saved key first; only ask the user "
                  "for a new one if decrypt fails.")
    elif local_fp:
        print("distribution key: manifest predates fingerprints — try the saved "
              "key first; only ask the user for a new one if decrypt fails.")
    return 0


def _venv_python(venv: Path) -> Path:
    """Interpreter path inside a venv on any OS."""
    if IS_WINDOWS:
        for cand in (venv / "Scripts" / "python.exe", venv / "Scripts" / "python"):
            if cand.exists():
                return cand
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _venv_marker(venv: Path) -> Path:
    return venv / ".mac-deps-ready"


def ensure_python(repo: Path, python: str | None, venv_dir: str | None = None) -> str:
    if python and Path(python).exists():
        return python
    # Explicit venv location wins; default sits next to the repo checkout.
    # (On Windows avoid OneDrive/Desktop synced folders — locking breaks pip.)
    venv = Path(venv_dir).expanduser() if venv_dir else repo.parent / ".venv"
    py = _venv_python(venv)
    if not py.exists():
        print(f"creating venv: {venv}", flush=True)
        r = sh([sys.executable, "-m", "venv", str(venv)])
        if r.returncode != 0:
            raise SystemExit(
                "venv creation failed. Windows Store Python often blocks venvs — "
                "use python.org python or conda instead, then --python <it>.")
    else:
        print(f"venv exists: {venv}", flush=True)
    # Idempotent: probe first, install only what's missing.
    probe = subprocess.run(
        [str(py), "-c", "import build123d, langgraph, pydantic, openai, ezdxf, fastapi, uvicorn; print('deps ok')"],
        capture_output=True, text=True, timeout=120)
    marker_ok = _venv_marker(venv).is_file()
    if probe.returncode == 0 and marker_ok:
        print("dependencies present, skipping pip install.", flush=True)
        return str(py)
    print("installing CAD runtime (~1.5GB first time, progress below) ...", flush=True)
    # aider pins numpy 1.x; --no-deps skips the pin (it imports fine on numpy 2.x).
    r = sh([str(py), "-m", "pip", "install",
            "numpy>=2,<2.3", "scipy>=1.10", "scikit-learn>=1.3",
            "trimesh>=4.0", "rtree>=1.1"] + MAC_DEPS)
    if r.returncode != 0:
        if IS_WINDOWS:
            print("pip install failed — trying conda-forge fallback is recommended "
                  "on Windows (see SKILL.md Windows notes).", file=sys.stderr)
        raise SystemExit("pip install failed (output above — no truncation, check the error)")
    # Aider is OPTIONAL: the engine falls back to direct-API repair without it.
    # Shared helper (mac_env.py) handles the hostile wheel: --no-deps (skip
    # numpy==1.26.4 pin) + --ignore-requires-python (metadata falsely claims
    # <3.13) + missing extras + 3.13 audioop shim + import verification.
    try:
        from mac_env import ensure_aider
    except ImportError:
        ensure_aider = None  # type: ignore[assignment]
    if ensure_aider is None:
        r = sh([str(py), "-m", "pip", "install", "--no-deps",
                "--ignore-requires-python", "aider-chat==0.82.3"])
        aider_ok, aider_note = (r.returncode == 0, "legacy path")
    else:
        aider_ok, aider_note = ensure_aider(str(py))
    try:
        _venv_marker(venv).write_text("aider=" + ("yes" if aider_ok else "no") + "\n",
                                      encoding="utf-8")
    except OSError:
        pass
    if not aider_ok:
        print(f"WARNING: aider unavailable ({aider_note}) — continuing WITHOUT aider. "
              "The pipeline auto-falls-back to direct-API repair (works, costs more tokens).",
              flush=True)
    else:
        print(f"aider ready ({aider_note}).", flush=True)
    return str(py)


def _load_stage0(repo: Path):
    """Load MAC Stage 0 modules by file path (no package import => no heavy deps)."""
    import importlib.util as _ilu

    def _load(name: str):
        spec = _ilu.spec_from_file_location(
            f"mac_stage0_{name}", str(repo / "multi_agent_cad" / f"{name}.py"))
        if spec is None or spec.loader is None:
            raise ImportError(f"Stage 0 module missing: {name}.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    return _load("prompt_norm"), _load("vision")


def _run_stage0(repo: Path, prompt: str, image: str | None, workdir: Path,
                api_key: str) -> tuple[str, dict]:
    """Stage 0: vision (if --image) + normalize into the effective prompt.

    Provider split (vision model lives on a different provider than the
    pipeline LLM): vision key resolution is MAC_VISION_KEY -> SILICONFLOW_API_KEY
    -> remembered config; vision base URL MAC_VISION_BASE default siliconflow.
    Never raises: any failure returns (raw_prompt, fallback info).
    """
    norm_info: dict = {"enabled": False}
    try:
        prompt_norm, vision = _load_stage0(repo)
    except Exception as exc:
        return prompt, {"enabled": False, "fallback": True,
                        "error": f"stage0 modules missing: {exc}"[:300]}
    # -- vision model credentials: SEPARATE provider from the pipeline LLM --
    vkey = (os.environ.get("MAC_VISION_KEY", "").strip()
            or os.environ.get("SILICONFLOW_API_KEY", "").strip()
            or _saved_keys().get("MAC_VISION_KEY", "")
            or _saved_keys().get("SILICONFLOW_API_KEY", ""))
    vurl = os.environ.get("MAC_VISION_BASE",
                          "https://api.siliconflow.cn/v1")
    vmodel = os.environ.get("MAC_VISION_MODEL",
                            "Qwen/Qwen3-VL-32B-Instruct")
    analysis = None
    # No-image guard (input side): a prompt with almost no numbers and no
    # reference photo means every dimension downstream is a guess. Warn loudly
    # but proceed — the gap report inside the spec carries the details.
    if not image:
        import re as _re2
        _nums = _re2.findall(r"\d+(?:\.\d+)?", prompt)
        if len(_nums) < 3:
            hint = (f"WARNING: prompt contains only {len(_nums)} numeric value(s) "
                    f"and no --image was given — downstream geometry will be "
                    f"mostly guessed. Re-run with --image <photo> for measured "
                    f"dimensions.")
            print(hint, flush=True)
            norm_info["low_coverage_warning"] = hint
    if image:
        vkey = (os.environ.get("MAC_VISION_KEY", "").strip()
                or os.environ.get("SILICONFLOW_API_KEY", "").strip()
                or _saved_keys().get("MAC_VISION_KEY", "")
                or _saved_keys().get("SILICONFLOW_API_KEY", ""))
        vurl = os.environ.get("MAC_VISION_BASE",
                              "https://api.siliconflow.cn/v1")
        vmodel = os.environ.get("MAC_VISION_MODEL",
                                "Qwen/Qwen3-VL-32B-Instruct")
        if not vkey:
            norm_info["vision_error"] = (
                "no vision key: set MAC_VISION_KEY or SILICONFLOW_API_KEY")
        else:
            try:
                use_cache = os.environ.get("MAC_DISABLE_CACHE", "").lower() \
                    not in ("1", "true", "yes")
                r = vision.analyze_image(image, api_key=vkey,
                                         base_url=vurl, model=vmodel,
                                         use_cache=use_cache)
                analysis = r["analysis"]
                norm_info["vision"] = {"model": r.get("model"),
                                       "cached": r.get("cached"),
                                       "usage": r.get("usage")}
            except Exception as exc:
                norm_info["vision_error"] = str(exc)[:300]
    try:
        r = prompt_norm.normalize_prompt(prompt, image_analysis=analysis,
                                         api_key=api_key)
        (workdir / "normalized_prompt.txt").write_text(r["normalized"], encoding="utf-8")
        norm_info.update({"enabled": True, "model": r.get("model"),
                          "elapsed_s": r.get("elapsed_s"), "usage": r.get("usage"),
                          "gap_report": r.get("gap_report", "")[:2000]})
        # No-image guard: thin specs without a reference photo get flagged,
        # because every missing slot becomes a guess downstream.
        if not image:
            import re as _re
            m = _re.search(r"Spec coverage:\s*(\d+)\s*/\s*(\d+)", r["normalized"])
            if m and int(m.group(1)) < 5:
                hint = (f"WARNING: only {m.group(1)}/{m.group(2)} spec slots filled "
                        f"and no reference image was given — downstream geometry will "
                        f"be mostly guessed. Re-run with --image <photo> for "
                        f"measured dimensions.")
                print(hint, flush=True)
                norm_info["low_coverage_warning"] = hint
        return r["normalized"], norm_info
    except Exception as exc:
        norm_info.update({"enabled": True, "fallback": True, "error": str(exc)[:500]})
        return prompt, norm_info


def dump_config(python: str, repo: Path, out: Path, prompt: str,
                workflow: str, bom: bool, dxf: bool, spec: bool) -> Path:
    # Parse config.py via AST (no import => no dependency tree needed).
    import ast as _ast
    tree = _ast.parse((repo / "multi_agent_cad" / "config.py").read_text(encoding="utf-8"))
    cfg: dict = {}
    for node in tree.body:
        if isinstance(node, _ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], _ast.Name):
            name = node.targets[0].id
            if name.isupper() and not name.startswith("_"):
                try:
                    cfg[name] = _ast.literal_eval(node.value)
                except Exception:
                    pass
    cfg.pop("DS_API_KEY", None)
    cfg["USER_REQUEST"] = prompt
    cfg["WORKFLOW_ID"] = workflow
    cfg["BOM_ENABLED"] = bom
    cfg["DRAWING_ENABLED"] = dxf
    cfg["SPEC_ENABLED"] = spec
    path = out / "config.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the MAC pipeline end-to-end (skill driver).")
    ap.add_argument("--prompt", required=False, default=None,
                    help="natural-language CAD request (required unless --check-update)")
    ap.add_argument("--workflow", default="aider", choices=["aider", "original"],
                    help="aider=modify existing design.py in --workdir; original=from scratch")
    ap.add_argument("--repo", default=None, help="MAC checkout dir (cloned if missing)")
    ap.add_argument("--bundle", default=None,
                    help="key-gated bundle (.tar.gz.enc path or URL) instead of git clone")
    ap.add_argument("--key", default=None, help="bundle decryption key (or MAC_BUNDLE_KEY)")
    ap.add_argument("--skill", default=None,
                    help="private skill folder holding dist_bundle.json "
                         "(fills --bundle/--key automatically)")
    ap.add_argument("--python", default=None, help="python with MAC deps (venv created if missing)")
    ap.add_argument("--venv", default=None,
                    help="explicit venv directory (default: <repo-parent>/.venv). "
                         "Use a short ASCII path; on Windows avoid synced folders.")
    ap.add_argument("--workdir", default=None, help="run directory (MAC temp_* land here)")
    ap.add_argument("--out", default="out", help="harvested artifacts go here")
    ap.add_argument("--design", default=None, help="existing design .py for aider workflow")
    ap.add_argument("--bom", action="store_true")
    ap.add_argument("--dxf", action="store_true")
    ap.add_argument("--spec", action="store_true")
    ap.add_argument("--image", default=None,
                    help="reference product photo (read by vision model, text fed to Stage 0)")
    ap.add_argument("--no-norm", action="store_true",
                    help="skip Stage 0 normalization (use raw prompt)")
    ap.add_argument("--no-cache", action="store_true",
                    help="disable vision, planner and architect caches for this run")
    ap.add_argument("--viewer-url", default="",
                    help="CAD Viewer base URL, e.g. http://127.0.0.1:3245 — "
                         "prints a clickable review link per artifact")
    ap.add_argument("--web", action="store_true",
                    help="start MAC's built-in Web UI and STEP viewer (no prompt/API key needed)")
    ap.add_argument("--dry-run", action="store_true", help="setup only, do not run pipeline")
    ap.add_argument("--check-update", action="store_true",
                    help="compare installed skill/engine versions against the "
                         "public manifest (read-only, no downloads)")
    ap.add_argument("--update-skills", action="store_true",
                    help="refresh sub-skills (mac-bom/-drafting/-qa-kernel/-spec) "
                         "from the public skill library (hash compare; add/update "
                         "only, never deletes; reports orphans)")
    ap.add_argument("--sklib-url", default=None,
                    help="skill library base URL (default: public file repo)")
    ap.add_argument("--manifest-url", default=None,
                    help="override version manifest URL")
    ap.add_argument("--update", action="store_true",
                    help="update an installed repo in place: re-download the bundle "
                         "(via --skill dist_bundle.json or --bundle/--key) and replace "
                         "--repo. Refuses while a job.log was modified in the last "
                         "10 minutes (a run may be in flight).")
    args = ap.parse_args(argv)

    if args.no_cache:
        os.environ["MAC_DISABLE_CACHE"] = "1"

    if args.check_update:
        return _do_check_update(
            args.skill, str(Path(args.repo).expanduser()) if args.repo else None,
            args.manifest_url)
    if args.update_skills:
        if not args.skill:
            print("--update-skills needs --skill DIR (sibling skills live next to it)",
                  file=sys.stderr)
            return 2
        return _do_update_skills(args.skill, args.sklib_url)
    if not args.prompt and not args.update and not args.web:
        ap.error("--prompt is required unless running a maintenance command")

    base = (Path(args.repo).expanduser() if args.repo
            else Path.cwd() / "mac-work" / "Multi-Agent-CAD").resolve()
    bundle, key, dist_version = args.bundle, args.key, None
    if args.skill and (not bundle or not key):
        try:
            skill_dir = Path(args.skill).expanduser().resolve()
            dist = json.loads((skill_dir / "dist_bundle.json").read_text(encoding="utf-8"))
            bundle = bundle or dist.get("bundle_url") or None
            key = key or dist.get("key") or None
            dist_version = dist.get("version")
        except (OSError, ValueError) as exc:
            print(f"cannot read dist_bundle.json: {exc}", file=sys.stderr)
            return 2
    if args.update:
        return _do_update(base, bundle, key, dist_version, args.manifest_url)
    api_key = ""
    if not args.dry_run and not args.web:
        api_key = _resolve_key("DASHSCOPE_API_KEY", "your own model key")
    repo = ensure_repo(base, bundle=bundle, key=key, version=dist_version)
    cadpy_src = (repo / "packages" / "cadpy" / "src").resolve()
    if not cadpy_src.is_dir():
        raise SystemExit(
            "MAC bundle incomplete: packages/cadpy/src is missing. Update the MAC "
            "bundle; do not install the unrelated PyPI package named 'cadpy'.")
    python = ensure_python(repo, args.python, args.venv)
    workdir = (Path(args.workdir).expanduser() if args.workdir
               else Path.cwd() / "mac-work" / "run").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir = Path(args.out).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if args.web:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(cadpy_src), str(repo.resolve()), env.get("PYTHONPATH", "")])
        print("Starting MAC Web UI with built-in STEP viewer...")
        return subprocess.call(
            [python, "-u", "-m", "multi_agent_cad.web"],
            cwd=str(workdir), env=env)

    if args.workflow == "aider":
        src = Path(args.design).expanduser().resolve() if args.design else None
        if src is None:
            cands = sorted(workdir.glob("temp_design*.py"),
                           key=lambda p: p.stat().st_mtime_ns)
            src = cands[-1] if cands else None
        if src is None or not src.is_file():
            print("aider workflow needs --design <file.py> (or a temp_design*.py in --workdir)", file=sys.stderr)
            return 2
        if src.resolve() != (workdir / "temp_design_aider_0.py").resolve():
            shutil.copy2(src, workdir / "temp_design_aider_0.py")

    cfg_path = dump_config(python, repo, workdir, args.prompt, args.workflow,
                           args.bom, args.dxf, args.spec).resolve()
    (workdir / "prompt.txt").write_text(args.prompt, encoding="utf-8")
    # Stage 0 (default on): vision (if --image) + normalize into the effective
    # prompt. Never blocks: failures fall back to the raw prompt.
    norm_info: dict = {"enabled": False}
    effective_prompt = args.prompt
    if not args.no_norm and not args.dry_run:
        print("Stage 0: normalizing prompt...", flush=True)
        effective_prompt, norm_info = _run_stage0(
            repo, args.prompt,
            str(Path(args.image).expanduser().resolve()) if args.image else None,
            workdir, api_key)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["USER_REQUEST"] = effective_prompt
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"Stage 0: {'normalized' if norm_info.get('enabled') and not norm_info.get('fallback') else 'fallback(raw)'} "
              f"({norm_info.get('elapsed_s', '?')}s)")
        if norm_info.get("fallback"):
            print(f"Stage 0 warning: {norm_info.get('error', 'unknown error')}",
                  flush=True)
        if norm_info.get("vision_error"):
            print(f"Stage 0 vision warning: {norm_info['vision_error']}", flush=True)
    (workdir / "norm.json").write_text(json.dumps(norm_info, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    module = "multi_agent_cad.graph_aider" if args.workflow == "aider" else "multi_agent_cad.graph"
    print(f"repo: {repo}\npython: {python}\nworkdir: {workdir}\nmodule: {module}")
    if args.dry_run:
        print("dry-run: setup complete, pipeline NOT started")
        return 0

    env = dict(os.environ)
    env["MAC_CONFIG_FILE"] = str(cfg_path)
    env["DASHSCOPE_API_KEY"] = api_key
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(cadpy_src), str(repo.resolve()), env.get("PYTHONPATH", "")])
    log_path = workdir / "job.log"
    start = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        popen_kw: dict = dict(cwd=str(workdir), stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT, env=env)
        if IS_WINDOWS:
            popen_kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen([python, "-u", "-m", module], **popen_kw)
        rc = proc.wait()
    elapsed = round(time.time() - start, 1)

    harvested: list[str] = []
    for pattern, dest in (("temp_output*.step", "model.step"),
                          ("temp_output*.stl", "model.stl"),
                          ("temp_BOM_*.csv", "BOM.csv"),
                          ("temp_drawing_*.dxf", "model.dxf"),
                          ("temp_SPEC_*.md", "SPEC.md")):
        cands = sorted(workdir.glob(pattern),
                       key=lambda p: p.stat().st_mtime_ns) if "*" in pattern else []
        if cands:
            shutil.copy2(cands[-1], outdir / dest)
            harvested.append(dest)
    summary = {"prompt": args.prompt[:300], "workflow": args.workflow,
               "elapsed_s": elapsed, "exit_code": rc,
               "artifacts": harvested, "out": str(outdir)}
    (workdir / "run-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if rc == 0:
        print("\nSTEP complete. You can still generate (re-run with flags):")
        print("  --bom   BOM.csv: 8 locked columns, Qty from geometry, ISO cross-check")
        print("  --dxf   model.dxf: TOP+FRONT wireframe, overall dims, hole table")
        print("  --spec  SPEC.md + PART.md/CHANGES.md freeze (version triad gate)")
        print("Tell the user these three options and ask which to generate.")
        if args.viewer_url:
            import urllib.parse as _up
            base = args.viewer_url.rstrip("/")
            out_abs = str(outdir.resolve())
            print("\nReview links:")
            for name in harvested:
                print(f"  {base}{_up.quote(out_abs)}?file={_up.quote(name)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
