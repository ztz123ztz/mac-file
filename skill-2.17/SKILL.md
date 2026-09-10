---
name: mac
description: Run the full MAC (Multi-Agent CAD) text-to-CAD pipeline from the terminal — from-scratch generation or modification — ending in STEP plus optional DXF/BOM/SPEC. Installing this skill is enough to run the whole system. Use when asked to build, modify, or document a CAD model with MAC, or when a STEP-first parametric CAD task arrives with no project set up.
---

# MAC end-to-end runner

Provenance: [Pan-Chera/Multi-Agent-CAD](https://github.com/Pan-Chera/Multi-Agent-CAD).
This skill wraps the entire project: first run bootstraps (git clone +
venv + pip), subsequent runs reuse both. Needs Python 3.11+, git, network,
and a `DASHSCOPE_API_KEY` (any OpenAI-compatible key) in the environment.

## Install = copy this skill
No MAC checkout needed. The manifests (`.claude-plugin/`, `.codex-plugin/`)
or any Skills-CLI-compatible installer pick up `skills/` automatically;
manual install is `cp -r skills/mac <anywhere>`. Everything else happens on
first run. Requirements for the driver itself: Python 3.11 stdlib only.

## First run for non-coders (recommended flow)

The agent runs setup once, which asks the user exactly ONE question:

```bash
python scripts/mac_setup.py
# checks Python/git/openssl -> asks for YOUR OWN model key -> remembers it
# (~/.config/mac-skill/env, owner-only) -> installs the CAD runtime (~1.5GB once)
```

After that, every run is fully automatic — the key is never asked for again
(env var > remembered file > interactive prompt). If the user has no key yet,
point them at SiliconFlow console (API keys → create → top up a few yuan);
any OpenAI-compatible key works.

## Windows notes

- Run everything from **PowerShell** with `python scripts/...` (no shebang reliance).
- Decrypt/unpack/process-spawn are pure-Python (`cryptography` + `tarfile` +
  `CREATE_NEW_PROCESS_GROUP`) — no openssl CLI, no `tar` binary, no POSIX calls.
- Native `pip install build123d` may fail on Windows (trimesh/rtree/OCP wheels
  unreliable) — setup detects this and prints the **conda route**
  (`conda env create -f environment.yml`, then `--python <conda python>`).
  WSL2 works with zero changes if conda is not an option.
- `~/.config/mac-skill/env` gets best-effort 0600; NTFS ignores POSIX modes,
  so treat a shared Windows box as semi-trusted (home use: fine).

## Private distribution: skill-bound key (recommended)

The key travels **inside a private skill folder** — whoever holds the folder
can download + decrypt; the `.enc` file itself may live anywhere public:

```bash
# Distributor (needs the repo):
python scripts/mkdist.py --out mac-1.0 --key "$(cat key.txt)" \
  --skill-out ./mac-1.0-skill --bundle-url https://your-server/mac-1.0.tar.gz.enc
# Send ./mac-1.0-skill ONLY through private channels (mail/DM/drive-share).
# It contains dist_bundle.json {bundle_url, key, sha256}.
```

```bash
# Recipient (no git, no GitHub account, no key typing):
python <skill>/scripts/mac_run.py --prompt "..." --skill <skill> --bom
```

Semantics: skill possession == download permission. No separate key to
lose; rotation = new skill folder + new bundle. Limitation (shared with all
static-file schemes): already-distributed folders cannot be remotely revoked —
for revocation/expiry, serve the `.enc` behind an authenticated endpoint.

## Updating an installed copy (for users who installed earlier)

1. Replace the skill folder with the newly sent one (it carries the new
   `--update` flag; old folders predate it).
2. Run once (no pipeline starts, no tokens):
   ```bash
   python <skill>/scripts/mac_run.py --prompt "x" --skill <skill> \
     --repo <existing-install-dir> --update
   ```
   This re-downloads the latest bundle (authenticated by the embedded key),
   swaps the install atomically (previous copy kept as `<dir>.bak`), and
   refuses while a `job.log` was touched in the last 10 minutes.
   Delete `<dir>.bak` when happy. Your remembered API key is untouched.

## Key continuity rule (agents: read this before asking the user anything)

Users panic ("did the key change?") on every update. Answer it with data,
not guesses — run this first (read-only, no tokens, no downloads):

```bash
python <skill>/scripts/mac_run.py --skill <skill> --repo <install-dir> --check-update
```

- `distribution key: UNCHANGED` → tell the user "same key, nothing to do",
  keep using the saved key, **do NOT ask for a new one**.
- `distribution key: ROTATED` → the user needs the newest skill folder from
  the distributor (it carries the current key). Say exactly that; do not
  ask the user to invent, find, or re-paste a key.
- Anything else fails → fall back to trying the saved key; ask for a new one
  only if decrypt actually fails.

## Manual key mode (no skill folder needed)

```bash
# Distributor: pack + encrypt (key printed once, never stored — only its fingerprint)
python scripts/mkdist.py --out mac-1.0                  # auto-generates key
python scripts/mkdist.py --out mac-1.0 --key "$(cat key.txt)"
# -> mac-1.0.tar.gz.enc + mac-1.0.sha256 + mac-1.0.dist.json
# Share the FILE freely; share the KEY only with authorized people.

# Recipient (no git, no GitHub account needed):
python scripts/mac_run.py --prompt "..." \
  --bundle https://your-server/mac-1.0.tar.gz.enc --key "THEIR-KEY" --bom
# or: --bundle /path/to/mac-1.0.tar.gz.enc
# or: MAC_BUNDLE_KEY=... (env instead of --key)
```

Wrong key / corrupted bundle fails closed (`decrypt failed`). Key rotation =
new bundle + new key (old bundles stay readable by old keys; for revocation
or expiry, serve the file behind an authenticated endpoint instead).

## Model keys, tiers and busy hours (ops advice)

- Use a **paid-tier** key. Free/shared tiers rate-limit hard at peak hours
  (SiliconFlow `50609: System is too busy now`); a full from-scratch run is
  ~200k tokens, a modify run ~50–200k depending on repair rounds.
- `50609` / 429 / 5xx / timeouts are **transient provider congestion**, not a
  broken model or a broken skill: wait 1–5 minutes and retry the same command.
  402 = out of balance (top up); 401 = bad key (re-issue).
- Re-runs skip cached planner/architect stages, so a killed run costs at most
  the in-flight repair round. (Transient-error backoff inside the pipeline:
  see `multi_agent_cad/nodes.py::_call_llm_json_with_retry` + repair path.)

## Bring your own key (required)

This skill ships **zero credentials**. Before the first run, export your own
model key — any OpenAI-compatible provider works:

```bash
export DASHSCOPE_API_KEY="sk-..."   # your own key, never shared, never committed
```

- Default models live on SiliconFlow (`deepseek-ai/DeepSeek-V3`, …): get a key
  at their console, top up, export, done.
- Other providers (OpenAI / DeepSeek direct / Gemini / local Ollama): after
  the repo is cloned, edit `<repo>/multi_agent_cad/config.py` (`DS_BASE_URL`
  + per-stage `*_MODEL`, set `*_KWARGS = {}` unless Qwen) — see the repo
  README "Use any LLM provider" section. Your key stays in your environment;
  nothing is written to disk by the driver (config snapshots always drop
  `DS_API_KEY`).
- Without a key the driver refuses to start (`DASHSCOPE_API_KEY is not set`).

## Usage

```bash
export DASHSCOPE_API_KEY="sk-..."
python scripts/mac_run.py --prompt "Create a 50x50x6mm base plate with a 20mm central hole." --bom
python scripts/mac_run.py --prompt "Move the lip to the plate bottom edge" --design ./design.py   # modify
python scripts/mac_run.py --prompt "..." --dxf --spec --out ./deliverables                          # full doc set
python scripts/mac_run.py --prompt "..." --dry-run                                                  # setup only
python scripts/mac_run.py --skill <skill> --web                                                      # built-in Web UI + STEP viewer; no model key needed
```

Flags: `--workflow aider|original` (default aider = modify; original builds
from zero), `--repo` (checkout dir, cloned if missing), `--python` (reuse an
interpreter, else a venv is created), `--workdir` (per-run folder; one task
per directory, never share), `--design` (existing `.py` for aider),
`--bom/--dxf/--spec` (P3/P4/P5 doc stages, default off = 3D only).

Outputs in `--out`: `model.step` / `model.stl` / `BOM.csv` / `model.dxf` /
`SPEC.md` (only what was requested); per-run folder keeps `prompt.txt`,
`config.json` (effective snapshot), `job.log` (token bill at the tail),
`run-summary.json`, and raw `temp_*` for audit.

The built-in Web UI accepts an existing `.step`/`.stp` file, including a
downloaded issue-report attachment, and converts it to GLB with the bundled
cadpy runtime for in-browser review. Uploaded views are temporary and expire
after six hours.

Costs: from-scratch runs take 30–60 min and six-figure tokens; parameter-free
deterministic stages (BOM/DXF/SPEC) cost ~zero. Never run two tasks in one
`--workdir` (`temp_*` names collide).

## Troubleshooting

**Reuse an existing CAD stack (skip the 1.5GB reinstall).** If the machine
already has a Python with build123d/OCP/ezdxf (conda env, system interpreter,
another venv), point at it instead of creating a fresh venv:

```bash
python scripts/mac_run.py --prompt "..." --python /path/to/that/python --bom
# or: python scripts/mac_setup.py --python /path/to/that/python
```

Rules: the target interpreter must be Python ≥ 3.11; `--system-site-packages`
venvs inherit the base env (exactly how you avoid reinstalling OCP);
`mac_setup`/`mac_run` probe imports first and install only what's missing;
aider is verified on every setup (`from aider.coders import Coder` must pass)
and otherwise degrades to direct-API repair automatically.

**aider install fights back.** Known hostilities and their automatic fixes
(`mac_env.py`, all best-effort, never fatal):
- wheel claims `requires-python <3.13` (false) → `--ignore-requires-python`
- hard `numpy==1.26.4` pin (would downgrade CAD) → `--no-deps`
- missing runtime deps (`Pillow`, `mixpanel`, `json5`, …) → iterative resolver
  (reads the missing module from the error, installs, retries, ≤10 rounds)
- Python ≥ 3.13 removed stdlib `audioop` → shim from pydub's vendored fallback
- still broken → engine runs WITHOUT aider (direct-API repair path)

**pip looks hung.** Un-quieted on purpose: watch the output stream. Hundreds
of MB with no progress bar for minutes is normal on slow links; Ctrl-C is
safe (re-run resumes: venv probe skips finished parts, pip cache keeps wheels).

**Windows.** Prefer the conda route; raw `pip install build123d` often fails
(trimesh/rtree/OCP wheels). See Windows notes above. WSL2 works unchanged.

## MANDATORY follow-up rule (do not skip)

**After every run that produces a STEP, you MUST tell the user** — in your
reply text, not just in logs — that these three follow-ups are available,
with one line each, and **ask which (if any) to generate**:

1. **DXF** (`--dxf`): engineering drawing, machinable.
2. **BOM** (`--bom`): 8 locked columns, quantities from geometry only.
3. **SPEC + Freeze** (`--spec`): cited technical agreement + version stamp.

Re-running with the same prompt plus flags regenerates only the requested
documents (the frozen STEP is reused). If the user picks none, say so and
stop — do not generate unasked.

See `references/usage.md` for exit codes, layout, and the no-concurrency rule.
