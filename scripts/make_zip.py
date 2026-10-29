"""Build a clean ZIP of the project to copy to another computer.

    python scripts/make_zip.py            -> ../library-claim-agent.zip

Leaves out things that must not travel or are rebuilt on the other computer:
the private Python environment (.venv), your API key (.env), caches, logs,
certificates and recorded sweeps. Add --with-sweeps to include data/sweeps."""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".git", "cache"}
SKIP_FILES = {".env", "app.log", "models.json", "cert.pem", "key.pem", "ca-bundle.pem"}


def main():
    with_sweeps = "--with-sweeps" in sys.argv
    out = ROOT.parent / f"{ROOT.name}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if any(part in SKIP_DIRS for part in rel.parts) or path.name in SKIP_FILES or path.is_dir():
                continue
            if rel.parts[:2] == ("data", "sweeps") and not with_sweeps:
                continue
            info = zipfile.ZipInfo.from_file(path, Path(ROOT.name) / rel)
            if path.suffix == ".sh":
                info.external_attr = 0o755 << 16  # keep start.sh executable on macOS/Linux
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
            count += 1
    print(f"Wrote {out} ({count} files, {out.stat().st_size // 1024} KB). Your API key (.env) is NOT included.")


if __name__ == "__main__":
    main()
