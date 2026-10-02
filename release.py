"""Create a source-only ZIP and SHA256 manifest alongside a built universal wheel."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent


def build_source_release(wheel: Path, output: Path) -> dict:
    if not (ROOT / "pyproject.toml").is_file():
        raise ValueError("release packaging must run from the source checkout")
    if not wheel.name.endswith("-py3-none-any.whl"):
        raise ValueError("expected the universal py3-none-any wheel")
    output.mkdir(parents=True, exist_ok=True)
    wheel_target = output / wheel.name
    if wheel.resolve() != wheel_target.resolve():
        shutil.copyfile(wheel, wheel_target)
    allowed = list(ROOT.glob("*.py")) + list(ROOT.glob("*.schema.json"))
    allowed += [ROOT / "README.md", ROOT / "requirements.txt", ROOT / "pyproject.toml", ROOT / ".gitignore"]
    allowed += list((ROOT / "examples").glob("*.json"))
    allowed += list((ROOT / "client-configs").glob("*.json")) + list((ROOT / "client-configs").glob("*.toml"))
    source = output / f"habits-desktop-mcp-{VERSION}-source.zip"
    prefix = f"habits-desktop-mcp-{VERSION}"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(allowed):
            if path.is_symlink() or not path.is_file():
                raise ValueError("source release inputs must be regular files")
            relative = path.relative_to(ROOT).as_posix()
            entry = zipfile.ZipInfo(f"{prefix}/{relative}", (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
    files = [{"name": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (wheel_target, source)]
    manifest = {"package": "habits-desktop-mcp", "version": VERSION, "files": files, "containsCredentials": False}
    (output / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['name']}\n" for item in files), encoding="utf-8")
    (output / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Bundle source and hashes; no publishing or credentials.")
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    print(json.dumps(build_source_release(args.wheel, args.output), indent=2))


if __name__ == "__main__":
    main()
