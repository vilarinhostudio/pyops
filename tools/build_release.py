"""Builds the release artifacts into dist/: the extension zip, the Extensions
repository index, and the two folders users install separately (ops/, drawing-bonsai/).

Why not `blender --command extension build/server-generate`: CI would need a
Blender install just to zip a folder and hash it. The index.json written here
matches what `server-generate` produces (checked against Blender 5.2).

Usage:  python tools/build_release.py
"""
import hashlib
import json
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SKIP = {"__pycache__"}


def zip_dir(src: Path, dest: Path, root_in_zip: str = ""):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not SKIP & set(p.relative_to(src).parts) and p.suffix != ".pyc":
                z.write(p, Path(root_in_zip, p.relative_to(src)).as_posix())


def main():
    DIST.mkdir(exist_ok=True)
    manifest = tomllib.loads((ROOT / "pyops" / "blender_manifest.toml").read_text(encoding="utf-8"))
    ext_zip = DIST / f"{manifest['id']}-{manifest['version']}.zip"
    zip_dir(ROOT / "pyops", ext_zip)

    entry = {k: v for k, v in manifest.items() if k not in ("build",)}
    entry["archive_url"] = f"./{ext_zip.name}"
    entry["archive_size"] = ext_zip.stat().st_size
    entry["archive_hash"] = "sha256:" + hashlib.sha256(ext_zip.read_bytes()).hexdigest()
    index = {"version": "v1", "blocklist": [], "data": [entry]}
    (DIST / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    # Unversioned names so releases/latest/download/<name> is a stable link.
    zip_dir(ROOT / "ops", DIST / "ops.zip", "ops")
    zip_dir(ROOT / "drawing-bonsai", DIST / "drawing-bonsai.zip", "drawing-bonsai")
    print(f"[PyOps.release] {manifest['version']}: {sorted(p.name for p in DIST.iterdir())}")


if __name__ == "__main__":
    main()
