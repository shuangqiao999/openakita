use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

use crate::state;

static CACHED_BUNDLED_BACKEND_DIR: OnceLock<Option<PathBuf>> = OnceLock::new();

#[cfg(windows)]
pub fn apply_no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
pub fn apply_no_window(_cmd: &mut Command) {}

pub fn strip_harmful_python_env(cmd: &mut Command) {
    cmd.env_remove("PYTHONPATH");
    cmd.env_remove("PYTHONHOME");
    cmd.env_remove("PYTHONSTARTUP");
    cmd.env_remove("VIRTUAL_ENV");
    cmd.env_remove("CONDA_PREFIX");
    cmd.env_remove("CONDA_DEFAULT_ENV");
    cmd.env_remove("CONDA_SHLVL");
    cmd.env_remove("CONDA_PYTHON_EXE");
    cmd.env_remove("PIP_TARGET");
    cmd.env_remove("PIP_PREFIX");
    cmd.env_remove("PIP_USER");
    cmd.env_remove("PIP_INDEX_URL");
    cmd.env_remove("PIP_REQUIRE_VIRTUALENV");
}

pub fn is_harmful_python_env_key(key: &str) -> bool {
    key.eq_ignore_ascii_case("PYTHONPATH")
        || key.eq_ignore_ascii_case("PYTHONHOME")
        || key.eq_ignore_ascii_case("PYTHON_VENV_PATH")
        || key.eq_ignore_ascii_case("PYTHON_EXECUTABLE")
        || key.eq_ignore_ascii_case("PYTHONSTARTUP")
        || key.eq_ignore_ascii_case("VIRTUAL_ENV")
        || key.eq_ignore_ascii_case("CONDA_PREFIX")
        || key.eq_ignore_ascii_case("CONDA_DEFAULT_ENV")
        || key.eq_ignore_ascii_case("CONDA_SHLVL")
        || key.eq_ignore_ascii_case("CONDA_PYTHON_EXE")
}

pub fn apply_bundled_python_env(cmd: &mut Command, internal_dir: &Path) {
    ensure_bundled_pth_file(internal_dir);
    strip_harmful_python_env(cmd);
    #[cfg(target_os = "windows")]
    cmd.env("PYTHONHOME", internal_dir);
    #[cfg(not(target_os = "windows"))]
    {
        cmd.env_remove("PYTHONHOME");
        cmd.env("PYTHONNOUSERSITE", "1");
    }
    let mut parts: Vec<PathBuf> = vec![];
    let base_lib = internal_dir.join("base_library.zip");
    if base_lib.exists() {
        parts.push(base_lib);
    }
    parts.push(internal_dir.to_path_buf());
    let lib = internal_dir.join("Lib");
    if lib.is_dir() {
        parts.push(lib);
    }
    let dlls = internal_dir.join("DLLs");
    if dlls.is_dir() {
        parts.push(dlls);
    }
    if let Ok(joined) = std::env::join_paths(&parts) {
        cmd.env("PYTHONPATH", joined);
    }
}

pub fn apply_python_env_for(cmd: &mut Command, py: &Path) {
    let internal_dir = bundled_backend_dir().join("_internal");
    if py.starts_with(&internal_dir) {
        apply_bundled_python_env(cmd, &internal_dir);
    } else {
        strip_harmful_python_env(cmd);
    }
}

pub fn bundled_backend_dir() -> PathBuf {
    // Use module-local cache first
    if let Some(cached) = CACHED_BUNDLED_BACKEND_DIR.get() {
        if let Some(ref p) = cached {
            return p.clone();
        }
    }
    let result = compute_bundled_backend_dir();
    CACHED_BUNDLED_BACKEND_DIR.get_or_init(|| Some(result.clone()));
    result
}

fn compute_bundled_backend_dir() -> PathBuf {
    let exe_path = std::env::current_exe().ok();
    let exe_dir = exe_path
        .as_ref()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    #[cfg(target_os = "macos")]
    {
        if let Some(contents_dir) = exe_dir.parent() {
            let primary = contents_dir.join("Resources").join("resources").join("openakita-server");
            if primary.exists() {
                return primary;
            }
            let fallback = contents_dir.join("Resources").join("openakita-server");
            if fallback.exists() {
                return fallback;
            }
        }
    }

    let primary = exe_dir.join("resources").join("openakita-server");
    if primary.exists() {
        return primary;
    }

    #[cfg(target_os = "linux")]
    {
        let mut candidates: Vec<PathBuf> = vec![];
        let exe_name = exe_path
            .as_ref()
            .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()));
        let static_names: &[&str] = &[
            "openakita-setup-center",
            "openakita-desktop",
            "open-akita-desktop",
        ];
        if let Some(ref name) = exe_name {
            candidates.push(PathBuf::from(format!("/usr/lib/{name}/resources/openakita-server")));
        }
        for app_name in static_names {
            candidates.push(PathBuf::from(format!("/usr/lib/{app_name}/resources/openakita-server")));
        }
        if let Some(usr_dir) = exe_dir.parent() {
            if let Some(ref name) = exe_name {
                candidates.push(
                    usr_dir.join("lib").join(name).join("resources").join("openakita-server"),
                );
            }
            for app_name in static_names {
                candidates.push(
                    usr_dir.join("lib").join(app_name).join("resources").join("openakita-server"),
                );
            }
        }
        if let Some(mount_root) = exe_dir.parent().and_then(|p| p.parent()) {
            if let Some(ref name) = exe_name {
                candidates.push(
                    mount_root.join("lib").join(name).join("resources").join("openakita-server"),
                );
            }
            for app_name in static_names {
                candidates.push(
                    mount_root.join("lib").join(app_name).join("resources").join("openakita-server"),
                );
            }
            candidates.push(mount_root.join("resources").join("openakita-server"));
        }
        for c in &candidates {
            if c.exists() {
                eprintln!("[bundled_backend_dir] found at Linux fallback: {}", c.display());
                return c.clone();
            }
        }
        eprintln!(
            "[bundled_backend_dir] not found. exe_dir={}, exe_name={:?}, checked {} Linux fallback paths",
            exe_dir.display(),
            exe_name,
            candidates.len()
        );
    }

    primary
}

pub fn bootstrap_resource_dir() -> PathBuf {
    let exe_path = std::env::current_exe().ok();
    let exe_dir = exe_path
        .as_ref()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    #[cfg(target_os = "macos")]
    {
        if let Some(contents_dir) = exe_dir.parent() {
            let primary = contents_dir.join("Resources").join("resources").join("bootstrap");
            if primary.exists() {
                return primary;
            }
            let fallback = contents_dir.join("Resources").join("bootstrap");
            if fallback.exists() {
                return fallback;
            }
        }
    }

    let primary = exe_dir.join("resources").join("bootstrap");
    if primary.exists() {
        return primary;
    }

    #[cfg(target_os = "linux")]
    {
        let static_names: &[&str] = &[
            "openakita-setup-center",
            "openakita-desktop",
            "open-akita-desktop",
        ];
        let exe_name = exe_path
            .as_ref()
            .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()));
        let mut candidates: Vec<PathBuf> = vec![];
        if let Some(ref name) = exe_name {
            candidates.push(PathBuf::from(format!("/usr/lib/{name}/resources/bootstrap")));
        }
        for app_name in static_names {
            candidates.push(PathBuf::from(format!("/usr/lib/{app_name}/resources/bootstrap")));
        }
        if let Some(usr_dir) = exe_dir.parent() {
            if let Some(ref name) = exe_name {
                candidates.push(usr_dir.join("lib").join(name).join("resources").join("bootstrap"));
            }
            for app_name in static_names {
                candidates.push(usr_dir.join("lib").join(app_name).join("resources").join("bootstrap"));
            }
        }
        for c in candidates {
            if c.exists() {
                return c;
            }
        }
    }

    primary
}

pub fn bundles_internal_python_path() -> Option<PathBuf> {
    let bir = bundled_backend_dir();
    if !bir.exists() {
        return None;
    }
    let candidates: Vec<PathBuf> = if cfg!(windows) {
        vec![bir.join("_internal").join("python.exe")]
    } else {
        vec![
            bir.join("_internal").join("python3"),
            bir.join("_internal").join("python"),
        ]
    };
    let internal_dir = bir.join("_internal");
    for internal_py in candidates {
        if !internal_py.exists() {
            continue;
        }
        let mut c = Command::new(&internal_py);
        c.args(["-c", "import pip; print(pip.__version__)"]);
        apply_bundled_python_env(&mut c, &internal_dir);
        apply_no_window(&mut c);
        if let Ok(output) = c.output() {
            if output.status.success() {
                return Some(internal_py);
            }
        }
    }
    None
}

pub fn bundled_backend_version() -> Option<String> {
    let version_file = bundled_backend_dir()
        .join("_internal")
        .join("openakita")
        .join("_bundled_version.txt");
    std::fs::read_to_string(&version_file)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

pub fn ensure_bundled_pth_file(internal_dir: &Path) {
    let detected_ver: Option<u32> = (8..=15).find(|minor| {
        let dil = internal_dir.join(format!("python3{minor}.dll"));
        if dil.exists() {
            return true;
        }
        if let Ok(entries) = std::fs::read_dir(internal_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.starts_with(&format!("libpython3.{minor}")) && name.contains(".so") {
                    return true;
                }
            }
        }
        false
    });
    let Some(minor) = detected_ver else { return };

    let pth_name = format!("python3{minor}._pth");
    let pth_path = internal_dir.join(&pth_name);

    if pth_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&pth_path) {
            if content.contains("base_library.zip") {
                return;
            }
        }
    }

    let mut lines = vec![];
    if internal_dir.join("base_library.zip").exists() {
        lines.push("base_library.zip".to_string());
    }
    let zip_name = format!("python3{minor}.zip");
    if internal_dir.join(&zip_name).exists() {
        lines.push(zip_name);
    }
    lines.push(".".to_string());
    if internal_dir.join("Lib").is_dir() {
        lines.push("Lib".to_string());
    }
    if internal_dir.join("DLLs").is_dir() {
        lines.push("DLLs".to_string());
    }
    lines.push("import site".to_string());
    let content = lines.join("\n") + "\n";
    let _ = std::fs::write(&pth_path, content);
}

pub fn prepend_path(cmd: &mut Command, dir: &Path) {
    let current = std::env::var_os("PATH").unwrap_or_default();
    let mut paths = vec![dir.to_path_buf()];
    paths.extend(std::env::split_paths(&current));
    if let Ok(joined) = std::env::join_paths(paths) {
        cmd.env("PATH", joined);
    }
}

pub fn find_pip_python() -> Option<PathBuf> {
    let root = state::openakita_root_dir();
    let venv_py = if cfg!(windows) {
        root.join("venv").join("Scripts").join("python.exe")
    } else {
        root.join("venv").join("bin").join("python")
    };
    if venv_py.exists() {
        return Some(venv_py);
    }
    if let Some(py) = bundles_internal_python_path() {
        return Some(py);
    }
    None
}

pub fn venv_python_path(venv_dir: &str) -> PathBuf {
    let v = PathBuf::from(venv_dir);
    if cfg!(windows) {
        v.join("Scripts").join("python.exe")
    } else {
        v.join("bin").join("python")
    }
}

pub fn venv_pythonw_path(venv_dir: &str) -> PathBuf {
    let v = PathBuf::from(venv_dir);
    if cfg!(windows) {
        let p = v.join("Scripts").join("pythonw.exe");
        if p.exists() {
            return p;
        }
        v.join("Scripts").join("python.exe")
    } else {
        v.join("bin").join("python")
    }
}

pub fn resolve_python(venv_dir: &str) -> Result<(PathBuf, Option<String>), String> {
    let venv_py = venv_python_path(venv_dir);
    if venv_py.exists() {
        return Ok((venv_py, None));
    }
    let py = find_pip_python().ok_or_else(|| {
        "未找到可用 Python 解释器（venv/bundled）。请重新安装 OpenAkita 以恢复内置 Python。".to_string()
    })?;
    let bir = bundled_backend_dir();
    let internal_dir = bir.join("_internal");
    let pythonpath = if py.starts_with(&internal_dir) {
        let mut parts: Vec<PathBuf> = vec![];
        let base_lib = internal_dir.join("base_library.zip");
        if base_lib.exists() {
            parts.push(base_lib);
        }
        parts.push(internal_dir.clone());
        let lib = internal_dir.join("Lib");
        if lib.is_dir() {
            parts.push(lib);
        }
        let dlls = internal_dir.join("DLLs");
        if dlls.is_dir() {
            parts.push(dlls);
        }
        let joined = std::env::join_paths(parts)
            .map_err(|e| format!("构建 bundled PYTHONPATH 失败: {e}"))?;
        Some(joined.to_string_lossy().to_string())
    } else {
        None
    };
    Ok((py, pythonpath))
}

pub fn run_python_module_json(
    venv_dir: &str,
    module: &str,
    args: &[&str],
    extra_env: &[(&str, &str)],
) -> Result<String, String> {
    let (py, pythonpath) = resolve_python(venv_dir)?;
    let mut c = Command::new(&py);
    apply_no_window(&mut c);
    strip_harmful_python_env(&mut c);
    c.env("PYTHONUTF8", "1");
    c.env("PYTHONIOENCODING", "utf-8");
    if let Some(ref pp) = pythonpath {
        c.env("PYTHONPATH", pp);
    }
    c.arg("-m").arg(module);
    c.args(args);
    for (k, v) in extra_env {
        c.env(k, v);
    }
    let out = c.output().map_err(|e| format!("failed to run python: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8(out.stderr)
            .unwrap_or_else(|e| String::from_utf8_lossy(&e.into_bytes()).into_owned());
        let stdout = String::from_utf8(out.stdout)
            .unwrap_or_else(|e| String::from_utf8_lossy(&e.into_bytes()).into_owned());
        return Err(format!(
            "python failed: {}\nstdout:\n{}\nstderr:\n{}",
            out.status, stdout, stderr
        ));
    }
    let stdout = out.stdout;
    let trimmed = match String::from_utf8(stdout) {
        Ok(s) => s.trim().to_string(),
        Err(e) => String::from_utf8_lossy(&e.into_bytes()).trim().to_string(),
    };
    Ok(trimmed)
}
