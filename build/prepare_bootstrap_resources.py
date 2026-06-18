#!/usr/bin/env python3
"""Prepare lightweight Tauri bootstrap resources for the dual-venv runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BOOTSTRAP_DIR = ROOT / "apps" / "setup-center" / "src-tauri" / "resources" / "bootstrap"
BIN_DIR = BOOTSTRAP_DIR / "bin"
WHEELS_DIR = BOOTSTRAP_DIR / "wheels"
WHEELHOUSE_DIR = BOOTSTRAP_DIR / "wheelhouse"
DIST_DIR = ROOT / "dist"

UV_RELEASES = {
    ("Windows", "AMD64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
    ("Windows", "ARM64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-pc-windows-msvc.zip",
    ("Darwin", "arm64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz",
    ("Darwin", "x86_64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-apple-darwin.tar.gz",
    ("Linux", "x86_64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz",
    ("Linux", "aarch64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pyproject() -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - Python 3.11+ is required by OpenAkita.
        import tomli as tomllib

    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def build_wheel() -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix="openakita-wheel-"))
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=ROOT,
        check=True,
    )
    wheels = sorted(out_dir.glob("openakita-*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        raise RuntimeError("python -m build --wheel completed but no openakita wheel was found")
    return wheels[-1]


def copy_wheel(wheel: Path) -> Path:
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    for old in WHEELS_DIR.glob("openakita-*.whl"):
        old.unlink()
    target = WHEELS_DIR / wheel.name
    shutil.copy2(wheel, target)
    return target


def download_uv(url: str) -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    uv_name = "uv.exe" if platform.system() == "Windows" else "uv"
    uv_target = BIN_DIR / uv_name
    if uv_target.exists():
        return uv_target

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / url.rsplit("/", 1)[-1]
        urllib.request.urlretrieve(url, archive)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                candidate = next(name for name in zf.namelist() if name.endswith(uv_name))
                with zf.open(candidate) as src, uv_target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            import tarfile

            with tarfile.open(archive) as tf:
                candidate = next(member for member in tf.getmembers() if member.name.endswith("/uv"))
                src = tf.extractfile(candidate)
                if src is None:
                    raise RuntimeError("uv archive did not contain an executable")
                with src, uv_target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    if platform.system() != "Windows":
        uv_target.chmod(0o755)
    return uv_target


def find_uv_url() -> str:
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    if key not in UV_RELEASES:
        raise RuntimeError(f"Unsupported platform for uv bootstrap: {system} {machine}")
    return UV_RELEASES[key]


def write_manifest(app_version: str, wheel: Path, uv: Path) -> None:
    manifest = {
        "schema_version": 1,
        "app_name": "openakita",
        "app_version": app_version,
        "python_version": "3.12",
        "wheel": {
            "name": f"wheels/{wheel.name}",
            "sha256": sha256(wheel),
        },
        "uv": {
            "path": "bin/uv",
            "windows_path": "bin/uv.exe",
            "version": "",
            "sha256": sha256(uv),
        },
        "python_seed": None,
        "default_pip_index": {
            "id": "aliyun",
            "url": "https://mirrors.aliyun.com/pypi/simple/",
            "trusted_host": "mirrors.aliyun.com",
        },
    }
    (BOOTSTRAP_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-wheel-build", action="store_true")
    parser.add_argument("--uv-url", default=os.environ.get("OPENAKITA_UV_URL"))
    args = parser.parse_args()

    project = load_pyproject()
    app_version = project["project"]["version"]

    BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
    WHEELHOUSE_DIR.mkdir(parents=True, exist_ok=True)

    wheel = sorted(DIST_DIR.glob("openakita-*.whl"), key=lambda item: item.stat().st_mtime)[-1] if args.skip_wheel_build else build_wheel()
    packaged_wheel = copy_wheel(wheel)
    uv = download_uv(args.uv_url or find_uv_url())
    write_manifest(app_version, packaged_wheel, uv)

    print(f"Prepared bootstrap resources in {BOOTSTRAP_DIR}")
    print(f"Wheel: {packaged_wheel}")
    print(f"uv: {uv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
