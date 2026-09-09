# mac-file — encrypted MAC distribution files

This repo hosts **ciphertext only**. Every `.tar.gz.enc` here is
AES-256-CBC/PBKDF2 encrypted: downloading it without the distribution key
yields random bytes. Keys travel privately with the `mac` skill package
(`dist_bundle.json` inside the skill folder).

## Current release

| File | SHA-256 (see `.sha256`) | Contents |
|---|---|---|
| `mac-2.1.tar.gz.enc` | `mac-2.1.sha256` | MAC engine + skills + docs |

## Recipient quickstart

1. Receive the private skill folder (`skill/`) from the distributor.
2. Get your own model key ready (any OpenAI-compatible key).
3. Run (the skill already points here):
   ```bash
   python <skill>/scripts/mac_setup.py        # one question: your key
   python <skill>/scripts/mac_run.py --prompt "..." --skill <skill> --bom
   ```

Raw download: `https://raw.githubusercontent.com/ztz123ztz/mac-file/main/<file>`
