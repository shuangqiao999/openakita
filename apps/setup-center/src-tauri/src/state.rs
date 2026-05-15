use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::Mutex;
use std::sync::OnceLock;

use crate::util;

/// === Startup / auto-start constants ===
pub const AUTO_START_TIMEOUT_MS: u64 = 180_000;
pub const BACKEND_BOOT_GRACE_SEC: u64 = 150;
pub const BACKEND_BOOT_GRACE_PID_DEAD_SEC: u64 = 30;
pub const SERVICE_START_DEDUPE_MS: u64 = 3_000;
pub const SELF_HEAL_COOLDOWN_MS: u64 = 30_000;
pub const OPENAKITA_ROOT_MARKER: &str = ".openakita-root";

/// === Global managed child process ===
pub struct ManagedProcess {
    pub child: std::process::Child,
    pub workspace_id: String,
    pub pid: u32,
    pub started_at: u64,
}

pub static MANAGED_CHILD: Lazy<Mutex<Option<ManagedProcess>>> = Lazy::new(|| Mutex::new(None));

pub static AUTO_START_IN_PROGRESS: AtomicBool = AtomicBool::new(false);
pub static AUTO_START_STARTED_AT_MS: AtomicU64 = AtomicU64::new(0);

pub static SERVICE_START_LAST_AT: Lazy<Mutex<HashMap<String, u64>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

pub static BOOT_GRACE_CACHE: Lazy<Mutex<HashMap<String, (u32, u64)>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// === Root / state file locks ===
pub static ROOT_CONFIG_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));
pub static STATE_FILE_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

/// === Caches ===
pub static CACHED_ROOT_DIR: Lazy<Mutex<Option<PathBuf>>> = Lazy::new(|| Mutex::new(None));
pub static CACHED_ROOT_CONFIG: Lazy<Mutex<Option<RootConfig>>> = Lazy::new(|| Mutex::new(None));
pub static CACHED_BUNDLED_BACKEND_DIR: OnceLock<Option<PathBuf>> = OnceLock::new();
pub static BLOCKING_HTTP_CLIENT: Lazy<reqwest::blocking::Client> = Lazy::new(|| {
    reqwest::blocking::Client::builder()
        .no_proxy()
        .pool_max_idle_per_host(4)
        .build()
        .expect("build blocking HTTP client")
});
pub static LOG_BUF: Lazy<Mutex<Option<std::io::BufWriter<std::fs::File>>>> =
    Lazy::new(|| Mutex::new(None));

/// === Migrations ===
pub const CURRENT_CONFIG_VERSION: u32 = crate::migrations::CURRENT_CONFIG_VERSION;

/// === Data types ===
#[derive(Debug, Serialize, Deserialize, Default, Clone)]
pub struct RootConfig {
    #[serde(default)]
    pub custom_root: Option<String>,
}

fn default_config_version() -> u32 {
    CURRENT_CONFIG_VERSION
}

#[derive(Debug, Serialize, Deserialize, Default, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AppStateFile {
    #[serde(default = "default_config_version")]
    pub config_version: u32,
    #[serde(default)]
    pub current_workspace_id: Option<String>,
    #[serde(default)]
    pub workspaces: Vec<WorkspaceMeta>,
    #[serde(default)]
    pub auto_start_backend: Option<bool>,
    #[serde(default)]
    pub last_installed_version: Option<String>,
    #[serde(default)]
    pub install_mode: Option<String>,
    #[serde(default)]
    pub auto_update: Option<bool>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceMeta {
    pub id: String,
    pub name: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceSummary {
    pub id: String,
    pub name: String,
    pub path: String,
    pub is_current: bool,
}

/// === Root dir ===
pub fn default_root_dir() -> PathBuf {
    dirs_next::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita")
}

pub fn root_config_path() -> PathBuf {
    default_root_dir().join("root_config.json")
}

pub fn read_root_config() -> RootConfig {
    {
        let cache = CACHED_ROOT_CONFIG.lock().unwrap();
        if let Some(ref cfg) = *cache {
            return cfg.clone();
        }
    }
    let p = root_config_path();
    let cfg = if let Ok(content) = fs::read_to_string(&p) {
        match serde_json::from_str(&content) {
            Ok(cfg) => cfg,
            Err(e) => {
                eprintln!("warning: failed to parse {}: {e}, using defaults", p.display());
                RootConfig::default()
            }
        }
    } else {
        RootConfig::default()
    };
    let mut cache = CACHED_ROOT_CONFIG.lock().unwrap();
    *cache = Some(cfg.clone());
    cfg
}

pub fn write_root_config(config: &RootConfig) -> Result<(), String> {
    let default_dir = default_root_dir();
    fs::create_dir_all(&default_dir).map_err(|e| format!("create default root dir failed: {e}"))?;
    crate::workspace::write_root_marker(&default_dir)?;
    let p = root_config_path();
    let data =
        serde_json::to_string_pretty(config).map_err(|e| format!("serialize root config failed: {e}"))?;
    util::atomic_write_with_backup(&p, data.as_bytes())?;
    let txt_path = default_dir.join("custom_root.txt");
    match &config.custom_root {
        Some(path) if !path.is_empty() => {
            let trimmed = path.trim();
            let mut bytes: Vec<u8> = Vec::with_capacity(2 + trimmed.len() * 2);
            bytes.extend_from_slice(&[0xFF, 0xFE]);
            for code_unit in trimmed.encode_utf16() {
                bytes.extend_from_slice(&code_unit.to_le_bytes());
            }
            fs::write(&txt_path, bytes)
                .map_err(|e| format!("write custom_root.txt failed: {e}"))?;
        }
        _ => {
            let _ = fs::remove_file(&txt_path);
        }
    }
    Ok(())
}

pub fn openakita_root_dir() -> PathBuf {
    {
        let cache = CACHED_ROOT_DIR.lock().unwrap();
        if let Some(ref p) = *cache {
            return p.clone();
        }
    }
    let result = compute_openakita_root_dir();
    let mut cache = CACHED_ROOT_DIR.lock().unwrap();
    *cache = Some(result.clone());
    result
}

fn compute_openakita_root_dir() -> PathBuf {
    if let Ok(val) = std::env::var("OPENAKITA_ROOT") {
        if !val.is_empty() {
            return PathBuf::from(val);
        }
    }
    let config = read_root_config();
    if let Some(ref custom) = config.custom_root {
        if !custom.is_empty() {
            let p = PathBuf::from(custom);
            if !is_safe_openakita_data_root(&p) {
                eprintln!("WARNING: custom root dir '{}' is unsafe, falling back to default", custom);
                return default_root_dir();
            }
            if p.exists() || p.parent().map(|parent| parent.exists()).unwrap_or(false) {
                return p;
            }
            eprintln!("WARNING: custom root dir '{}' is not accessible, falling back to default", custom);
        }
    }
    default_root_dir()
}

pub fn invalidate_root_cache() {
    *CACHED_ROOT_DIR.lock().unwrap() = None;
    *CACHED_ROOT_CONFIG.lock().unwrap() = None;
}

pub fn is_safe_openakita_data_root(path: &PathBuf) -> bool {
    if !path.is_absolute() || util::is_path_root(path) {
        return false;
    }
    let target = util::comparable_path(path);
    if let Some(home) = dirs_next::home_dir() {
        if target == util::comparable_path(&home) {
            return false;
        }
    }
    for protected in [
        dirs_next::desktop_dir(),
        dirs_next::download_dir(),
        dirs_next::document_dir(),
        dirs_next::data_dir(),
        dirs_next::data_local_dir(),
    ]
    .into_iter()
    .flatten()
    {
        if target == util::comparable_path(&protected) {
            return false;
        }
    }
    true
}

pub fn ensure_safe_openakita_data_root(path: &PathBuf) -> Result<(), String> {
    if is_safe_openakita_data_root(path) {
        Ok(())
    } else {
        Err("数据目录不能设置为磁盘根目录、用户主目录或系统常用目录。请使用专用目录，例如 D:\\OpenAkitaData\\.openakita".into())
    }
}

/// === State file ===
pub fn state_file_path() -> PathBuf {
    openakita_root_dir().join("state.json")
}

pub fn run_dir() -> PathBuf {
    openakita_root_dir().join("run")
}

pub fn workspaces_dir() -> PathBuf {
    openakita_root_dir().join("workspaces")
}

pub fn setup_logs_dir() -> PathBuf {
    openakita_root_dir().join("logs")
}

pub fn workspace_dir(id: &str) -> PathBuf {
    workspaces_dir().join(id)
}

pub fn service_pid_file(workspace_id: &str) -> PathBuf {
    run_dir().join(format!("openakita-{}.pid", workspace_id))
}

pub fn read_state_file() -> AppStateFile {
    let p = state_file_path();
    if let Ok(content) = fs::read_to_string(&p) {
        if let Ok(state) = serde_json::from_str::<AppStateFile>(&content) {
            if !state.workspaces.is_empty() {
                return state;
            }
            let recovered = rebuild_state_from_disk(Some(state));
            if !recovered.workspaces.is_empty() {
                eprintln!(
                    "state.json had empty workspaces but {} workspace dir(s) found on disk — recovered",
                    recovered.workspaces.len()
                );
                let _ = write_state_file(&recovered);
            }
            return recovered;
        }
        eprintln!("warning: state.json is corrupted, attempting disk recovery");
    }
    let recovered = rebuild_state_from_disk(None);
    if !recovered.workspaces.is_empty() {
        eprintln!(
            "state.json missing but {} workspace dir(s) found on disk — recovered",
            recovered.workspaces.len()
        );
        let _ = write_state_file(&recovered);
    }
    recovered
}

pub fn read_state_file_cached(cache: &mut (Option<u64>, Option<AppStateFile>)) -> AppStateFile {
    let p = state_file_path();
    let current_mtime = p.metadata().ok().and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs());
    if let (Some(cached_mtime), Some(ref cached_state)) = (cache.0, &cache.1) {
        if current_mtime == Some(cached_mtime) {
            return cached_state.clone();
        }
    }
    let state = read_state_file();
    cache.0 = current_mtime;
    cache.1 = Some(state.clone());
    state
}

pub fn write_state_file(state: &AppStateFile) -> Result<(), String> {
    let p = state_file_path();
    if let Some(parent) = p.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create_dir_all failed: {e}"))?;
    }
    let data = serde_json::to_string(state).map_err(|e| format!("serialize failed: {e}"))?;
    util::atomic_write_with_backup(&p, data.as_bytes())
}

fn rebuild_state_from_disk(partial: Option<AppStateFile>) -> AppStateFile {
    let mut state = partial.unwrap_or_default();
    let ws_dir = workspaces_dir();
    let Ok(entries) = fs::read_dir(&ws_dir) else {
        return state;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if !path.join("data").exists() {
            continue;
        }
        let id = entry.file_name().to_string_lossy().to_string();
        if state.workspaces.iter().any(|w| w.id == id) {
            continue;
        }
        state.workspaces.push(WorkspaceMeta {
            id: id.clone(),
            name: id.clone(),
        });
    }
    if state.current_workspace_id.is_none() && !state.workspaces.is_empty() {
        let preferred = state
            .workspaces
            .iter()
            .find(|w| w.id == "default")
            .unwrap_or(&state.workspaces[0]);
        state.current_workspace_id = Some(preferred.id.clone());
    }
    state
}

/// === Pid file ===
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PidFileData {
    pub pid: u32,
    #[serde(default = "default_started_by")]
    pub started_by: String,
    #[serde(default)]
    pub started_at: u64,
}

fn default_started_by() -> String {
    "tauri".to_string()
}

pub fn write_pid_file(workspace_id: &str, pid: u32, started_by: &str) -> Result<(), String> {
    let started_at = util::now_epoch_secs();
    let data = PidFileData {
        pid,
        started_by: started_by.to_string(),
        started_at,
    };
    let json = serde_json::to_string_pretty(&data).map_err(|e| format!("serialize pid: {e}"))?;
    let path = service_pid_file(workspace_id);
    fs::write(&path, json).map_err(|e| format!("write pid file: {e}"))?;
    BOOT_GRACE_CACHE.lock().unwrap().insert(workspace_id.to_string(), (pid, started_at));
    Ok(())
}

pub fn read_pid_file(workspace_id: &str) -> Option<PidFileData> {
    let path = service_pid_file(workspace_id);
    let content = fs::read_to_string(&path).ok()?;
    let trimmed = content.trim();
    if let Ok(data) = serde_json::from_str::<PidFileData>(trimmed) {
        if data.pid > 0 {
            return Some(data);
        }
    }
    if let Ok(pid) = trimmed.parse::<u32>() {
        if pid > 0 {
            return Some(PidFileData {
                pid,
                started_by: "tauri".to_string(),
                started_at: 0,
            });
        }
    }
    None
}

/// === Heartbeat file ===
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct HeartbeatData {
    pub pid: u32,
    pub timestamp: f64,
    #[serde(default)]
    pub phase: String,
    #[serde(default)]
    pub http_ready: bool,
    #[serde(default)]
    pub im_ready: bool,
    #[serde(default)]
    pub ready: bool,
}

pub fn service_heartbeat_file(workspace_id: &str) -> PathBuf {
    workspace_dir(workspace_id).join("data").join("backend.heartbeat")
}

pub fn read_heartbeat_file(workspace_id: &str) -> Option<HeartbeatData> {
    let path = service_heartbeat_file(workspace_id);
    let content = fs::read_to_string(&path).ok()?;
    serde_json::from_str::<HeartbeatData>(content.trim()).ok()
}

pub fn remove_heartbeat_file(workspace_id: &str) {
    let _ = fs::remove_file(service_heartbeat_file(workspace_id));
}

pub fn is_heartbeat_stale(workspace_id: &str, max_age_secs: u64) -> Option<bool> {
    let hb = read_heartbeat_file(workspace_id)?;
    let now = util::now_epoch_secs() as f64;
    let age = now - hb.timestamp;
    Some(age > max_age_secs as f64)
}

/// === Service lock ===
pub fn service_lock_file(workspace_id: &str) -> PathBuf {
    run_dir().join(format!("openakita-{}.lock", workspace_id))
}

pub fn try_acquire_start_lock(workspace_id: &str) -> bool {
    let lock_path = service_lock_file(workspace_id);
    let _ = fs::create_dir_all(lock_path.parent().unwrap_or(std::path::Path::new(".")));
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock_path)
        .is_ok()
}

pub fn release_start_lock(workspace_id: &str) {
    let _ = fs::remove_file(service_lock_file(workspace_id));
}

/// === Restart marker ===
pub fn restart_marker_path() -> PathBuf {
    let base = dirs_next::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita");
    let _ = fs::create_dir_all(&base);
    base.join("restart.marker")
}

/// === Logging ===
pub fn log_to_file(msg: &str) {
    let secs = util::now_epoch_secs();
    let line = format!("[{}] {}\n", secs, msg);
    let mut guard = LOG_BUF.lock().unwrap();
    if guard.is_none() {
        let log_dir = setup_logs_dir();
        let _ = fs::create_dir_all(&log_dir);
        let path = log_dir.join("autostart.log");
        if let Ok(f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            *guard = Some(std::io::BufWriter::new(f));
        }
    }
    if let Some(ref mut writer) = *guard {
        let _ = writer.write_all(line.as_bytes());
        let _ = writer.flush();
    }
}

pub fn write_crash_log(message: &str, show_dialog: bool) -> PathBuf {
    let log_dir = setup_logs_dir();
    let _ = fs::create_dir_all(&log_dir);
    let crash_path = log_dir.join("crash.log");
    let timestamp = util::now_epoch_secs();
    let exe = util::cached_exe_str();
    let cwd = util::cached_cwd_str();
    let home = util::cached_home_dir().to_string_lossy().to_string();
    let entry = format!("[{timestamp}] exe={exe} cwd={cwd} home={home}\n{message}\n---\n");
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&crash_path)
        .and_then(|mut f| f.write_all(entry.as_bytes()));

    if show_dialog {
        #[cfg(windows)]
        {
            use std::ffi::OsStr;
            use std::iter::once;
            use std::os::windows::ffi::OsStrExt;

            extern "system" {
                fn MessageBoxW(
                    hwnd: *mut std::ffi::c_void,
                    text: *const u16,
                    caption: *const u16,
                    typ: u32,
                ) -> i32;
            }

            fn to_wide(s: &str) -> Vec<u16> {
                OsStr::new(s).encode_wide().chain(once(0)).collect()
            }

            let body = format!(
                "OpenAkita Desktop 启动失败 (startup failed)\n\n\
                 {message}\n\n\
                 崩溃日志已写入 (crash log): {}\n\
                 请将此日志发送给开发者以帮助诊断问题。",
                crash_path.display()
            );
            let caption = "OpenAkita \u{2013} Crash";
            let wb = to_wide(&body);
            let wc = to_wide(caption);
            unsafe {
                MessageBoxW(std::ptr::null_mut(), wb.as_ptr(), wc.as_ptr(), 0x10);
            }
        }
    }
    crash_path
}

pub fn backend_in_boot_grace(workspace_id: &str) -> bool {
    let cache = BOOT_GRACE_CACHE.lock().unwrap();
    if let Some(&(cached_pid, started_at)) = cache.get(workspace_id) {
        if started_at == 0 {
            return false;
        }
        let age = util::now_epoch_secs().saturating_sub(started_at);
        if age >= BACKEND_BOOT_GRACE_SEC {
            return false;
        }
        if crate::process::is_pid_running(cached_pid) {
            return true;
        }
        return age < BACKEND_BOOT_GRACE_PID_DEAD_SEC;
    }
    drop(cache);
    let Some(data) = read_pid_file(workspace_id) else {
        return false;
    };
    if data.started_at == 0 {
        return false;
    }
    let age = util::now_epoch_secs().saturating_sub(data.started_at);
    if age >= BACKEND_BOOT_GRACE_SEC {
        return false;
    }
    if crate::process::is_pid_running(data.pid) {
        return true;
    }
    age < BACKEND_BOOT_GRACE_PID_DEAD_SEC
}

pub fn is_backend_http_healthy(port: Option<u16>) -> bool {
    let effective_port = port.unwrap_or(18900);
    BLOCKING_HTTP_CLIENT
        .get(format!("http://127.0.0.1:{}/api/health", effective_port))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .ok()
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

pub fn should_cleanup_stale_heartbeat(heartbeat_stale: Option<bool>, http_healthy: bool) -> bool {
    matches!(heartbeat_stale, Some(true)) && !http_healthy
}
