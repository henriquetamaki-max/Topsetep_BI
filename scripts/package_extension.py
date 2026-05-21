"""
Empacota a pasta `extension/` em `extension-latest.zip` para distribuicao
via Supabase Storage (bucket publico `extension/`).

Uso:
    .venv/Scripts/python.exe scripts/package_extension.py
    # ou via CLI:
    .venv/Scripts/python.exe scripts/package_extension.py --supabase-url https://X --anon-key YYY

Comportamento:
- Le `extension/manifest.json` para extrair a versao.
- Substitui SUPABASE_URL e SUPABASE_ANON_KEY em `extension/config.js` se as
  flags --supabase-url / --anon-key forem passadas (uma copia temporaria do
  config.js e' gerada dentro do zip; o arquivo original NAO e' modificado).
- Gera 2 zips: `extension-latest.zip` e `extension-<version>.zip` em
  `dist/extension/`.
- Upload manual: subir `extension-latest.zip` no Storage do Supabase
  (bucket publico `extension`, path `extension-latest.zip`).

NAO usar este script para CI por enquanto. Backlog: integrar com supabase CLI
para upload automatico.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
DIST_DIR = ROOT / "dist" / "extension"

INCLUDE_PATTERNS = [
    "manifest.json",
    "config.js",
    "background.js",
    "content.js",
    "popup.html",
    "popup.js",
    "popup.css",
    "selectors.json",
    "icons/**/*",
    "_locales/**/*",
]


def _read_version() -> str:
    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest.get("version", "0.0.0"))


def _rewrite_config(content: str, supabase_url: str | None, anon_key: str | None) -> str:
    if supabase_url:
        content = re.sub(
            r'SUPABASE_URL:\s*"[^"]*"',
            f'SUPABASE_URL: "{supabase_url.rstrip("/")}"',
            content,
        )
    if anon_key:
        content = re.sub(
            r'SUPABASE_ANON_KEY:\s*"[^"]*"',
            f'SUPABASE_ANON_KEY: "{anon_key}"',
            content,
        )
    return content


def _collect_files() -> list[Path]:
    files: list[Path] = []
    for pattern in INCLUDE_PATTERNS:
        if "*" in pattern:
            files.extend(p for p in EXT_DIR.glob(pattern) if p.is_file())
        else:
            p = EXT_DIR / pattern
            if p.exists() and p.is_file():
                files.append(p)
    return files


def _write_zip(out_path: Path, supabase_url: str | None, anon_key: str | None) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in _collect_files():
            arcname = f.relative_to(EXT_DIR).as_posix()
            if arcname == "config.js" and (supabase_url or anon_key):
                rewritten = _rewrite_config(
                    f.read_text(encoding="utf-8"), supabase_url, anon_key,
                )
                zf.writestr(arcname, rewritten)
            else:
                zf.write(f, arcname)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Package BI TopStep Live Monitor extension.")
    ap.add_argument("--supabase-url", default=None, help="Inject SUPABASE_URL into config.js")
    ap.add_argument("--anon-key", default=None, help="Inject SUPABASE_ANON_KEY into config.js")
    args = ap.parse_args()

    if not EXT_DIR.exists():
        print(f"[error] extension dir not found: {EXT_DIR}", file=sys.stderr)
        return 1

    version = _read_version()
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    latest = DIST_DIR / "extension-latest.zip"
    versioned = DIST_DIR / f"extension-{version}.zip"

    n = _write_zip(latest, args.supabase_url, args.anon_key)
    _write_zip(versioned, args.supabase_url, args.anon_key)

    print(f"[ok] packaged {n} files (v{version})")
    print(f"  -> {latest}")
    print(f"  -> {versioned}")
    print()
    print("Next step: upload extension-latest.zip to Supabase Storage:")
    print("  Bucket: extension (public)")
    print("  Path:   extension-latest.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
