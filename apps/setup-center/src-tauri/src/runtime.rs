use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

use crate::python_env;
use crate::state;

/// === Data types ===
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RuntimePipIndex {
    pub id: String,
    pub url: String,
    #[serde(default)]
    pub trusted_host: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RuntimeEnvState {
    pub path: String,
    pub status: String,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub last_verified_at: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RuntimeManifest {
    pub schema_version: u32,
    pub app_version: String,
    pub wheel_hash: String,
    pub python_version: String,
    pub app_venv: RuntimeEnvState,
    pub agent_venv: RuntimeEnvState,
    pub pip_index: RuntimePipIndex,
    pub legacy_mode: bool,
    pub last_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct BootstrapWheel {
    pub name: String,
    #[serde(default)]
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct BootstrapManifest {
    #[serde(default = "default_python_version")]
    pub python_version: String,
    pub wheel: BootstrapWheel,
    #[serde(default)]
    pub default_pip_index: Option<RuntimePipIndex>,
}

#[derive(Clone, Debug)]
pub struct RuntimeEnvInfo {
    pub app_python: PathBuf,
    pub agent_python: PathBuf,
    pub app_venv: PathBuf,
    pub agent_venv: PathBuf,
    pub pip_index: RuntimePipIndex,
}

fn default_python_version() -> String {
    "3.12".to_string()
}

/// === Path helpers ===
pub fn runtime_root_dir() -> PathBuf {
    state::openakita_root_dir().join("runtime")
}

pub fn runtime_manifest_path() -> PathBuf {
    runtime_root_dir().join("manifest.json")
}

pub fn app_venv_dir() -> PathBuf {
    runtime_root_dir().join("app-venv")
}

pub fn agent_venv_dir() -> PathBuf {
    runtime_root_dir().join("agent-venv")
}

pub fn runtime_logs_dir() -> PathBuf {
    runtime_root_dir().join("logs")
}

fn runtime_cache_dir() -> PathBuf {
    runtime_root_dir().join("cache")
}

pub fn modules_dir() -> PathBuf {
    state::openakita_root_dir().join("modules")
}

pub fn runtime_venv_python_path(venv_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        venv_dir.join("Scripts").join("python.exe")
    } else {
        venv_dir.join("bin").join("python")
    }
}

fn runtime_venv_home_python_path(venv_dir: &Path) -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    let cfg_path = venv_dir.join("pyvenv.cfg");
    let content = fs::read_to_string(cfg_path).ok()?;
    for line in content.lines() {
        let Some(home) = line.strip_prefix("home = ") else {
            continue;
        };
        let py = PathBuf::from(home.trim()).join("python.exe");
        if py.exists() {
            return Some(py);
        }
    }
    None
}

pub fn runtime_venv_site_packages_dir(venv_dir: &Path) -> Option<PathBuf> {
    if cfg!(windows) {
        let sp = venv_dir.join("Lib").join("site-packages");
        return sp.exists().then_some(sp);
    }
    None
}

fn python_string_literal(value: &Path) -> String {
    format!("{:?}", value.to_string_lossy().to_string())
}

pub fn runtime_venv_backend_args(venv_dir: &Path) -> Vec<String> {
    if cfg!(windows) && runtime_venv_home_python_path(venv_dir).is_some() {
        if let Some(site_packages) = runtime_venv_site_packages_dir(venv_dir) {
            let venv_python = runtime_venv_python_path(venv_dir);
            let code = format!(
                "import runpy, site, sys; sys.prefix = sys.exec_prefix = {}; sys.executable = {}; site.addsitedir({}); runpy.run_module('openakita.main', run_name='__main__')",
                python_string_literal(venv_dir),
                python_string_literal(&venv_python),
                python_string_literal(&site_packages)
            );
            return vec!["-u".into(), "-c".into(), code, "serve".into()];
        }
    }
    vec!["-u".into(), "-m".into(), "openakita.main".into(), "serve".into()]
}

pub fn runtime_venv_backend_python_path(venv_dir: &Path) -> PathBuf {
    if let Some(py) = runtime_venv_home_python_path(venv_dir) {
        return py;
    }
    runtime_venv_python_path(venv_dir)
}

pub fn runtime_venv_bin_dir(venv_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        venv_dir.join("Scripts")
    } else {
        venv_dir.join("bin")
    }
}

/// === Pip index ===
pub fn default_pip_index() -> RuntimePipIndex {
    RuntimePipIndex {
        id: "aliyun".into(),
        url: "https://mirrors.aliyun.com/pypi/simple/".into(),
        trusted_host: "mirrors.aliyun.com".into(),
    }
}

fn trusted_host_for_url(url: &str) -> String {
    url.split_once("://")
        .map(|(_, rest)| rest.split('/').next().unwrap_or("").to_string())
        .unwrap_or_default()
}

pub fn read_runtime_manifest() -> Option<RuntimeManifest> {
    let content = fs::read_to_string(runtime_manifest_path()).ok()?;
    serde_json::from_str::<RuntimeManifest>(&content).ok()
}

pub fn resolve_runtime_pip_index() -> RuntimePipIndex {
    if let Some(manifest) = read_runtime_manifest() {
        if !manifest.pip_index.url.trim().is_empty() {
            return manifest.pip_index;
        }
    }
    if let Ok(url) = std::env::var("OPENAKITA_PIP_INDEX_URL") {
        if !url.trim().is_empty() {
            let trusted_host = std::env::var("OPENAKITA_PIP_TRUSTED_HOST")
                .unwrap_or_else(|_| trusted_host_for_url(&url));
            return RuntimePipIndex {
                id: "env-openakita".into(),
                url,
                trusted_host,
            };
        }
    }
    if let Ok(url) = std::env::var("PIP_INDEX_URL") {
        if !url.trim().is_empty() {
            let trusted_host =
                std::env::var("PIP_TRUSTED_HOST").unwrap_or_else(|_| trusted_host_for_url(&url));
            return RuntimePipIndex {
                id: "env-pip".into(),
                url,
                trusted_host,
            };
        }
    }
    default_pip_index()
}

pub fn read_bootstrap_manifest() -> Result<BootstrapManifest, String> {
    let path = python_env::bootstrap_resource_dir().join("manifest.json");
    let content = fs::read_to_string(&path)
        .map_err(|e| format!("read bootstrap manifest {} failed: {e}", path.display()))?;
    serde_json::from_str(&content)
        .map_err(|e| format!("parse bootstrap manifest {} failed: {e}", path.display()))
}

fn bootstrap_uv_path() -> PathBuf {
    let bootstrap = python_env::bootstrap_resource_dir();
    let local = if cfg!(windows) {
        bootstrap.join("bin").join("uv.exe")
    } else {
        bootstrap.join("bin").join("uv")
    };
    if local.exists() {
        local
    } else {
        PathBuf::from("uv")
    }
}

fn run_and_log(mut cmd: Command, log_path: &Path) -> Result<(), String> {
    let output = cmd.output().map_err(|e| format!("run command failed: {e}"))?;
    let mut log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .map_err(|e| format!("open runtime log {} failed: {e}", log_path.display()))?;
    let _ = writeln!(log, "\n$ {:?}", cmd);
    let _ = log.write_all(&output.stdout);
    let _ = log.write_all(&output.stderr);
    if output.status.success() {
        Ok(())
    } else {
        Err(format!("command failed with status {}", output.status))
    }
}

fn health_check_python(py: &Path, code: &str, log_path: &Path) -> bool {
    if !py.exists() {
        return false;
    }
    let mut cmd = Command::new(py);
    cmd.args(["-c", code]);
    python_env::apply_no_window(&mut cmd);
    match cmd.output() {
        Ok(output) if output.status.success() => true,
        Ok(output) => {
            let mut log = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(log_path)
                .ok();
            if let Some(ref mut log) = log {
                let _ = writeln!(log, "health check failed for {}", py.display());
                let _ = log.write_all(&output.stdout);
                let _ = log.write_all(&output.stderr);
            }
            false
        }
        Err(_) => false,
    }
}

fn venv_is_real_isolated(venv_dir: &Path, py: &Path, log_path: &Path) -> bool {
    if !py.exists() {
        return false;
    }
    if !venv_dir.join("pyvenv.cfg").exists() {
        return false;
    }
    health_check_python(
        py,
        "import sys, pip; assert sys.prefix != sys.base_prefix, 'venv launcher fell back to base interpreter'",
        log_path,
    )
}

fn ensure_venv(venv_dir: &Path, python_version: &str, log_path: &Path) -> Result<PathBuf, String> {
    let py = runtime_venv_python_path(venv_dir);
    if venv_is_real_isolated(venv_dir, &py, log_path) {
        return Ok(py);
    }
    if venv_dir.exists() {
        if let Err(e) = fs::remove_dir_all(venv_dir) {
            if let Ok(mut log) = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(log_path)
            {
                let _ = writeln!(
                    log,
                    "warning: pre-clean of {} failed: {} (will fall back to `uv venv --clear`)",
                    venv_dir.display(),
                    e
                );
            }
        }
    }
    let uv = bootstrap_uv_path();
    let mut cmd = Command::new(&uv);
    cmd.args(["venv", "--python", python_version, "--seed", "--clear"]);
    cmd.arg(venv_dir);
    python_env::apply_no_window(&mut cmd);
    run_and_log(cmd, log_path)?;
    if venv_is_real_isolated(venv_dir, &py, log_path) {
        Ok(py)
    } else {
        let has_cfg = venv_dir.join("pyvenv.cfg").exists();
        Err(format!(
            "venv health check failed after creation: {} (pyvenv.cfg present={}, see {} for details)",
            py.display(),
            has_cfg,
            log_path.display()
        ))
    }
}

fn ensure_app_venv(bootstrap: &BootstrapManifest, pip_index: &RuntimePipIndex) -> Result<PathBuf, String> {
    let started = Instant::now();
    let log_path = runtime_logs_dir().join("app-venv.log");
    let app_py = runtime_venv_python_path(&app_venv_dir());
    let expected_version = env!("CARGO_PKG_VERSION");
    let manifest_ok = read_runtime_manifest()
        .map(|m| {
            m.app_version == expected_version
                && m.wheel_hash == bootstrap.wheel.sha256
                && !m.legacy_mode
        })
        .unwrap_or(false);
    if manifest_ok && health_check_python(&app_py, "import openakita, pip, certifi", &log_path) {
        state::log_to_file(&format!(
            "[runtime] ensure_app_venv reused existing env in {}ms",
            started.elapsed().as_millis()
        ));
        return Ok(app_py);
    }
    state::log_to_file("[runtime] ensure_app_venv rebuilding app runtime");
    let app_py = ensure_venv(&app_venv_dir(), &bootstrap.python_version, &log_path)?;
    let wheel_path = python_env::bootstrap_resource_dir().join(&bootstrap.wheel.name);
    if !wheel_path.exists() {
        return Err(format!("bootstrap wheel not found: {}", wheel_path.display()));
    }
    let wheel_arg = format!("{}[desktop]", wheel_path.display());
    let mut cmd = Command::new(bootstrap_uv_path());
    cmd.args(["pip", "install", "--python"]);
    cmd.arg(&app_py);
    cmd.arg(wheel_arg);
    cmd.arg("certifi");
    cmd.args(["--reinstall-package", "openakita"]);
    cmd.args(["--index-url", &pip_index.url]);
    if !pip_index.trusted_host.trim().is_empty() {
        cmd.args(["--trusted-host", &pip_index.trusted_host]);
    }
    python_env::apply_no_window(&mut cmd);
    let install_started = Instant::now();
    run_and_log(cmd, &log_path)?;
    state::log_to_file(&format!(
        "[runtime] app wheel install finished in {}ms",
        install_started.elapsed().as_millis()
    ));
    if health_check_python(&app_py, "import openakita, pip, certifi", &log_path) {
        state::log_to_file(&format!(
            "[runtime] ensure_app_venv ready in {}ms",
            started.elapsed().as_millis()
        ));
        Ok(app_py)
    } else {
        Err("app venv health check failed after OpenAkita install".into())
    }
}

fn ensure_agent_venv(bootstrap: &BootstrapManifest, _pip_index: &RuntimePipIndex) -> Result<PathBuf, String> {
    let started = Instant::now();
    let log_path = runtime_logs_dir().join("agent-venv.log");
    let result = ensure_venv(&agent_venv_dir(), &bootstrap.python_version, &log_path);
    state::log_to_file(&format!(
        "[runtime] ensure_agent_venv finished in {}ms status={}",
        started.elapsed().as_millis(),
        if result.is_ok() { "ok" } else { "error" }
    ));
    result
}

fn write_runtime_manifest(info: &RuntimeEnvInfo, bootstrap: &BootstrapManifest) {
    let now = crate::util::now_epoch_secs().to_string();
    let manifest = RuntimeManifest {
        schema_version: 1,
        app_version: env!("CARGO_PKG_VERSION").into(),
        wheel_hash: bootstrap.wheel.sha256.clone(),
        python_version: bootstrap.python_version.clone(),
        app_venv: RuntimeEnvState {
            path: info.app_venv.to_string_lossy().to_string(),
            status: "ready".into(),
            created_at: now.clone(),
            last_verified_at: now.clone(),
        },
        agent_venv: RuntimeEnvState {
            path: info.agent_venv.to_string_lossy().to_string(),
            status: "ready".into(),
            created_at: now.clone(),
            last_verified_at: now,
        },
        pip_index: info.pip_index.clone(),
        legacy_mode: false,
        last_error: None,
    };
    if let Ok(content) = serde_json::to_string_pretty(&manifest) {
        let _ = fs::write(runtime_manifest_path(), content);
    }
}

pub fn mark_legacy_runtime_mode(error: &str) {
    let pip_index = resolve_runtime_pip_index();
    let now = crate::util::now_epoch_secs().to_string();
    let (wheel_hash, python_version) = match read_bootstrap_manifest() {
        Ok(b) => (b.wheel.sha256, b.python_version),
        Err(_) => (String::new(), "3.12".to_string()),
    };
    let manifest = RuntimeManifest {
        schema_version: 1,
        app_version: env!("CARGO_PKG_VERSION").into(),
        wheel_hash,
        python_version,
        app_venv: RuntimeEnvState {
            path: app_venv_dir().to_string_lossy().to_string(),
            status: "failed".into(),
            created_at: now.clone(),
            last_verified_at: now.clone(),
        },
        agent_venv: RuntimeEnvState {
            path: agent_venv_dir().to_string_lossy().to_string(),
            status: "unknown".into(),
            created_at: now.clone(),
            last_verified_at: now,
        },
        pip_index,
        legacy_mode: true,
        last_error: Some(error.to_string()),
    };
    if let Ok(content) = serde_json::to_string_pretty(&manifest) {
        let _ = fs::write(runtime_manifest_path(), content);
    }
}

fn ensure_runtime_layout() -> Result<(), String> {
    let root = runtime_root_dir();
    for dir in [
        root.clone(),
        app_venv_dir(),
        agent_venv_dir(),
        runtime_logs_dir(),
        runtime_cache_dir().join("wheels"),
        runtime_cache_dir().join("uv"),
        runtime_cache_dir().join("python"),
    ] {
        fs::create_dir_all(&dir)
            .map_err(|e| format!("create runtime dir {} failed: {e}", dir.display()))?;
    }
    Ok(())
}

pub fn ensure_dual_runtime_env() -> Result<RuntimeEnvInfo, String> {
    let started = Instant::now();
    ensure_runtime_layout()?;
    let bootstrap = read_bootstrap_manifest()?;
    let pip_index = resolve_runtime_pip_index();
    let app_python = ensure_app_venv(&bootstrap, &pip_index)?;
    let agent_python = ensure_agent_venv(&bootstrap, &pip_index)?;
    let info = RuntimeEnvInfo {
        app_python,
        agent_python,
        app_venv: app_venv_dir(),
        agent_venv: agent_venv_dir(),
        pip_index,
    };
    write_runtime_manifest(&info, &bootstrap);
    state::log_to_file(&format!(
        "[runtime] ensure_dual_runtime_env finished in {}ms",
        started.elapsed().as_millis()
    ));
    Ok(info)
}

pub fn apply_dual_runtime_env(cmd: &mut Command) {
    python_env::strip_harmful_python_env(cmd);
    let pip_index = resolve_runtime_pip_index();
    cmd.env("PYTHONNOUSERSITE", "1");
    cmd.env("OPENAKITA_RUNTIME_ROOT", runtime_root_dir());
    cmd.env("OPENAKITA_APP_PYTHON", runtime_venv_python_path(&app_venv_dir()));
    cmd.env("OPENAKITA_AGENT_PYTHON", runtime_venv_python_path(&agent_venv_dir()));
    cmd.env("OPENAKITA_AGENT_BIN", runtime_venv_bin_dir(&agent_venv_dir()));
    cmd.env("PIP_INDEX_URL", &pip_index.url);
    cmd.env("UV_INDEX_URL", &pip_index.url);
    if !pip_index.trusted_host.trim().is_empty() {
        cmd.env("PIP_TRUSTED_HOST", &pip_index.trusted_host);
    }
    python_env::prepend_path(cmd, &runtime_venv_bin_dir(&agent_venv_dir()));

    if let Some(sp) = runtime_venv_site_packages_dir(&app_venv_dir()) {
        let cacert = sp.join("certifi").join("cacert.pem");
        if cacert.exists() {
            cmd.env("SSL_CERT_FILE", &cacert);
            cmd.env("REQUESTS_CA_BUNDLE", &cacert);
            cmd.env("CURL_CA_BUNDLE", &cacert);
            if let Some(parent) = cacert.parent() {
                cmd.env("SSL_CERT_DIR", parent);
            }
        }
    }
}

pub fn get_backend_executable(venv_dir: &str) -> (PathBuf, Vec<String>) {
    match ensure_dual_runtime_env() {
        Ok(runtime) => {
            let backend_python = runtime_venv_backend_python_path(&runtime.app_venv);
            state::log_to_file(&format!(
                "[runtime] dual venv ready: app_python={}, backend_python={}, agent_python={}",
                runtime.app_python.display(),
                backend_python.display(),
                runtime.agent_python.display()
            ));
            return (backend_python, runtime_venv_backend_args(&runtime.app_venv));
        }
        Err(e) => {
            state::log_to_file(&format!(
                "[runtime] dual venv unavailable, fallback to legacy: {e}"
            ));
            mark_legacy_runtime_mode(&e);
        }
    }
    let bundled_dir = python_env::bundled_backend_dir();
    let bundled_exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    if bundled_exe.exists() {
        return (bundled_exe, vec!["serve".to_string()]);
    }
    eprintln!(
        "[backend] dual runtime and bundled openakita-server unavailable at: {}\n\
         [backend] current_exe: {:?}\n\
         [backend] falling back to venv python in: {}",
        bundled_exe.display(),
        std::env::current_exe().ok().map(|p| p.display().to_string()),
        venv_dir,
    );
    let py = python_env::venv_pythonw_path(venv_dir);
    (py, vec!["-m".into(), "openakita.main".into(), "serve".into()])
}

pub fn build_modules_pythonpath() -> Option<String> {
    let base = modules_dir();
    if !base.exists() {
        return None;
    }
    let mut paths = Vec::new();
    for (module_id, _, _, _, _, _) in module_definitions() {
        let sp = base.join(module_id).join("site-packages");
        if sp.exists() {
            paths.push(sp.to_string_lossy().to_string());
        }
    }
    if paths.is_empty() {
        return None;
    }
    let sep = if cfg!(windows) { ";" } else { ":" };
    Some(paths.join(sep))
}

pub fn module_definitions() -> Vec<(
    &'static str,
    &'static str,
    &'static str,
    &'static [&'static str],
    u32,
    &'static str,
)> {
    vec![
        ("vector-memory", "向量记忆增强", "让 Akita 拥有长期记忆，能根据语义搜索历史对话。体积较大（约 2.5GB，含 PyTorch），安装耗时较长", &["sentence-transformers", "chromadb", "regex>=2023.6.3"], 2500, "core"),
    ]
}

pub fn runtime_wheel_hash_matches_bootstrap() -> bool {
    let bootstrap_hash = match read_bootstrap_manifest() {
        Ok(b) => b.wheel.sha256,
        Err(e) => {
            state::log_to_file(&format!("[version_check] bootstrap manifest unavailable: {e}"));
            return true;
        }
    };
    if bootstrap_hash.trim().is_empty() {
        return true;
    }
    read_runtime_manifest()
        .map(|m| {
            if m.legacy_mode {
                return true;
            }
            m.wheel_hash == bootstrap_hash
        })
        .unwrap_or(false)
}
