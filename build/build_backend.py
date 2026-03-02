#!/usr/bin/env python3
"""
OpenAkita Python Backend Build Script

Usage:
  python build/build_backend.py --mode core    # Core package (~100-150MB)
  python build/build_backend.py --mode full    # Full package (~600-800MB)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "build" / "openakita.spec"
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_DIR = DIST_DIR / "openakita-server"


def run_cmd(cmd: list[str], env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run command and print output"""
    print(f"  $ {' '.join(cmd)}")
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, env=merged_env, **kwargs)
    if result.returncode != 0:
        print(f"  [ERROR] Command failed (exit {result.returncode})")
        sys.exit(1)
    return result


def verify_bundled_python_contract(output_dir: Path) -> None:
    """Verify Contract A: onedir backend must include _internal/python* with pip."""
    internal_dir = output_dir / "_internal"
    if sys.platform == "win32":
        candidates = [internal_dir / "python.exe"]
    else:
        candidates = [internal_dir / "python3", internal_dir / "python"]

    py_path = next((p for p in candidates if p.exists()), None)
    if py_path is None:
        expected = ", ".join(str(p) for p in candidates)
        print(f"  [ERROR] Bundled Python missing. Expected one of: {expected}")
        sys.exit(1)

    print(f"  [OK] Bundled Python found: {py_path}")
    try:
        result = subprocess.run(
            [str(py_path), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        print(f"  [ERROR] Failed to run bundled Python pip check: {exc}")
        sys.exit(1)

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"  [ERROR] Bundled Python pip check failed (exit {result.returncode})")
        if stderr:
            print(f"    stderr: {stderr[:500]}")
        sys.exit(1)
    print("  [OK] Bundled Python pip check passed")


def normalize_macos_bundled_python(output_dir: Path) -> None:
    """Normalize macOS bundled python entrypoint to framework interpreter.

    On macOS, ensure a valid fallback entrypoint exists when framework assets are
    present. Keep the original bundled launcher intact to avoid changing Python's
    expected startup behavior.
    """
    if sys.platform != "darwin":
        return

    internal_dir = output_dir / "_internal"
    framework_candidates = sorted(
        internal_dir.glob("Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python")
    )
    if not framework_candidates:
        print("  [WARN] macOS framework interpreter not found; skip python entrypoint normalization")
        return

    # Prefer the same major.minor as current build interpreter, fallback to latest.
    target = framework_candidates[-1]
    wanted = f"{sys.version_info.major}.{sys.version_info.minor}"
    for cand in framework_candidates:
        if f"/Versions/{wanted}/" in str(cand):
            target = cand
            break

    try:
        target.chmod(target.stat().st_mode | 0o111)
    except Exception:
        # Keep best effort; verification step will catch non-executable targets.
        pass

    rel_target = os.path.relpath(target, internal_dir)
    touched = False
    for entry_name in ("python3", "python"):
        entry = internal_dir / entry_name
        # Do not overwrite existing launcher binaries; only fill missing entrypoints.
        if entry.exists() or entry.is_symlink():
            continue
        entry.symlink_to(rel_target)
        touched = True

    if touched:
        print(f"  [OK] Added fallback macOS bundled python entrypoint -> {target}")
    else:
        print("  [OK] Existing macOS bundled python launcher preserved")


def check_pyinstaller():
    """Check if PyInstaller is installed"""
    try:
        import PyInstaller  # noqa: F401
        print(f"  [OK] PyInstaller {PyInstaller.__version__} installed")
    except ImportError:
        print("  [WARN] PyInstaller not installed, installing...")
        run_cmd([sys.executable, "-m", "pip", "install", "pyinstaller"])


def clean_dist():
    """Clean previous build output"""
    # Clean dist output directory
    if OUTPUT_DIR.exists():
        print(f"  Cleaning old build output: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)

    # Clean entire dist directory to avoid symlink conflicts on macOS
    if DIST_DIR.exists():
        print(f"  Cleaning dist directory: {DIST_DIR}")
        shutil.rmtree(DIST_DIR)

    # Clean build temp directory
    build_tmp = PROJECT_ROOT / "build" / "openakita-server"
    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    # Clean PyInstaller work directory (fixes macOS symlink FileExistsError)
    pyinstaller_work = PROJECT_ROOT / "build" / "pyinstaller_work"
    if pyinstaller_work.exists():
        print(f"  Cleaning PyInstaller work directory: {pyinstaller_work}")
        shutil.rmtree(pyinstaller_work)


def ensure_playwright_chromium():
    """Ensure Playwright Chromium is installed for bundling"""
    try:
        import playwright
        pw_dir = Path(playwright.__file__).parent
        local_browsers = pw_dir / ".local-browsers"
        if local_browsers.exists():
            print("  [OK] Playwright Chromium already installed (local-browsers)")
            return

        # Check default system path
        if sys.platform == "win32":
            pw_cache = Path.home() / "AppData" / "Local" / "ms-playwright"
        elif sys.platform == "darwin":
            pw_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
        else:
            pw_cache = Path.home() / ".cache" / "ms-playwright"

        chromium_found = False
        if pw_cache.exists():
            for d in pw_cache.iterdir():
                if d.is_dir() and "chromium" in d.name.lower():
                    chromium_found = True
                    break

        if chromium_found:
            print(f"  [OK] Playwright Chromium found at {pw_cache}")
        else:
            print("  [INFO] Installing Playwright Chromium for bundling...")
            run_cmd([sys.executable, "-m", "playwright", "install", "chromium"])
    except ImportError:
        print("  [WARN] playwright not installed, installing...")
        run_cmd([sys.executable, "-m", "pip", "install", "playwright", "browser-use", "langchain-openai"])
        print("  [INFO] Installing Playwright Chromium...")
        run_cmd([sys.executable, "-m", "playwright", "install", "chromium"])


def build_backend(mode: str):
    """Execute PyInstaller packaging"""
    print(f"\n{'='*60}")
    print(f"  OpenAkita Backend Build - Mode: {mode.upper()}")
    print(f"{'='*60}\n")

    print("[1/5] Checking dependencies...")
    check_pyinstaller()

    print("\n[2/5] Ensuring Playwright Chromium for bundling...")
    ensure_playwright_chromium()

    print("\n[3/5] Cleaning old build...")
    clean_dist()

    print("\n[4/5] Running PyInstaller...")
    env = {"OPENAKITA_BUILD_MODE": mode}
    
    run_cmd(
        [
            sys.executable, "-m", "PyInstaller",
            str(SPEC_FILE),
            "--distpath", str(DIST_DIR),
            "--workpath", str(PROJECT_ROOT / "build" / "pyinstaller_work"),
            "--noconfirm",
            "--clean",  # Force clean build to avoid symlink conflicts on macOS
        ],
        env=env,
    )

    print("\n[5/5] Verifying build output...")
    
    if sys.platform == "win32":
        exe_path = OUTPUT_DIR / "openakita-server.exe"
    else:
        exe_path = OUTPUT_DIR / "openakita-server"

    if not exe_path.exists():
        print(f"  [ERROR] Executable not found: {exe_path}")
        sys.exit(1)

    # Test executable
    try:
        result = subprocess.run(
            [str(exe_path), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"  [OK] Executable verified: {exe_path}")
        else:
            print(f"  [WARN] Executable returned non-zero exit code: {result.returncode}")
            print(f"    stderr: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        print("  [WARN] Executable timed out (may be normal, continuing)")
    except Exception as e:
        print(f"  [WARN] Exception during verification: {e}")

    normalize_macos_bundled_python(OUTPUT_DIR)
    verify_bundled_python_contract(OUTPUT_DIR)

    # Calculate size
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"\n  Build completed!")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Total size: {size_mb:.1f} MB")
    print(f"  Mode: {mode.upper()}")


def main():
    parser = argparse.ArgumentParser(description="OpenAkita backend build script")
    parser.add_argument(
        "--mode",
        choices=["core", "full"],
        default="core",
        help="Build mode: core=minimal(exclude heavy deps), full=complete(all deps)",
    )
    args = parser.parse_args()
    build_backend(args.mode)


if __name__ == "__main__":
    main()
