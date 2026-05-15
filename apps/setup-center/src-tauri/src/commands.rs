use std::collections::HashMap;
use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::Ordering;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use tauri::Emitter;
use tauri::Manager;
#[cfg(desktop)]
use tauri_plugin_autostart::ManagerExt as AutostartManagerExt;

use crate::cli;
use crate::process;
use crate::python_env;
use crate::runtime;
use crate::state;
use crate::util;
use crate::workspace;

// ============================================================
//  Data types
// ============================================================

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub pid_file: String,
    #[serde(default)]
    pub heartbeat_phase: String,
    #[serde(default)]
    pub heartbeat_http_ready: bool,
    #[serde(default)]
    pub heartbeat_im_ready: bool,
    #[serde(default)]
    pub heartbeat_ready: bool,
    #[serde(default)]
    pub heartbeat_stale: Option<bool>,
    #[serde(default)]
    pub heartbeat_age_secs: Option<f64>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct ServiceLogChunk { pub path: String, pub content: String, pub truncated: bool }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct BackendAvailability { pub bundled: bool, pub venv_ready: bool, pub exe_path: String, pub bundled_checked: String, pub venv_checked: String }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentCheck { pub openakita_root: String, pub has_old_venv: bool, pub has_old_runtime: bool, pub has_old_workspaces: bool, pub old_version: Option<String>, pub current_version: String, pub running_processes: Vec<String>, pub disk_usage_mb: u64, pub conflicts: Vec<String> }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PythonDiagnostic { pub summary: String, pub contracts: Vec<PythonContractResult>, pub environment: PythonEnvironmentSnapshot, pub trace_id: String, pub generated_at: String }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PythonContractResult { pub id: String, pub title: String, pub status: String, pub code: String, pub evidence: Vec<String>, pub auto_fix: bool, pub fix_hint: Option<String> }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PythonEnvironmentSnapshot { pub platform: String, pub bundled_python_path: Option<String>, pub openakita_version: Option<String> }

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PythonCandidate { pub command: Vec<String>, pub version_text: String, pub is_usable: bool }

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct BundledPythonInstallResult { pub python_command: Vec<String>, pub python_path: String, pub install_dir: String, pub asset_name: String, pub tag: String }

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct OpenAkitaProcess { pub pid: u32, pub cmd: String }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct MigratePreflightInfo { pub source_path: String, pub source_size_mb: f64, pub target_path: String, pub target_free_mb: f64, pub entries: Vec<MigrateEntry>, pub can_migrate: bool, pub reason: String }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct MigrateEntry { pub name: String, pub size_mb: f64, pub exists_at_target: bool, pub is_dir: bool }

#[derive(Debug, Serialize, Clone)]
pub struct PlatformInfo { pub os: String, pub arch: String, pub home_dir: String, pub openakita_root_dir: String }

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct RootDirInfo { pub default_root: String, pub current_root: String, pub custom_root: Option<String> }

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data", rename_all = "camelCase")]
pub enum BackendFetchEvent { Chunk { text: String }, Done, Error { message: String } }

// ============================================================
//  Async helper
// ============================================================

async fn spawn_blocking_result<R: Send + 'static>(
    f: impl FnOnce() -> Result<R, String> + Send + 'static,
) -> Result<R, String> {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| format!("background task failed (join error): {e}"))?
}

// ============================================================
//  Path / workspace helpers
// ============================================================

fn workspace_file_path(workspace_id: &str, relative: &str) -> Result<PathBuf, String> {
    let base = state::workspace_dir(workspace_id);
    let rel = Path::new(relative);
    if rel.is_absolute() {
        return Err("relative path must not be absolute".into());
    }
    use std::path::Component;
    if rel.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err("relative path must not contain parent directory references (..)".into());
    }
    Ok(base.join(rel))
}

fn frontend_log_path() -> PathBuf {
    state::setup_logs_dir().join("frontend.log")
}

fn maybe_rotate_frontend_log(path: &Path) {
    const MAX_BYTES: u64 = 5 * 1024 * 1024;
    const TRUNCATE_TO: u64 = 2 * 1024 * 1024;
    let meta = match fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return,
    };
    if meta.len() <= MAX_BYTES {
        return;
    }
    let mut f = match fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return,
    };
    let start = meta.len().saturating_sub(TRUNCATE_TO);
    if f.seek(SeekFrom::Start(start)).is_err() {
        return;
    }
    let mut tail = Vec::new();
    if f.read_to_end(&mut tail).is_err() {
        return;
    }
    drop(f);
    let offset = tail
        .iter()
        .position(|&b| b == b'\n')
        .map(|i| i + 1)
        .unwrap_or(0);
    let _ = fs::write(path, &tail[offset..]);
}

// ============================================================
//  build_service_status
// ============================================================

fn build_service_status(
    workspace_id: &str,
    running: bool,
    pid: Option<u32>,
    pid_file_str: String,
) -> ServiceStatus {
    let (heartbeat_phase, heartbeat_http_ready, heartbeat_im_ready, heartbeat_ready, heartbeat_stale, heartbeat_age_secs) =
        if let Some(hb) = state::read_heartbeat_file(workspace_id) {
            let now = crate::util::now_epoch_secs() as f64;
            let age = now - hb.timestamp;
            let stale = age > 30.0;
            (hb.phase, hb.http_ready, hb.im_ready, hb.ready, Some(stale), Some(age))
        } else {
            (String::new(), false, false, false, None, None)
        };
    ServiceStatus {
        running,
        pid,
        pid_file: pid_file_str,
        heartbeat_phase,
        heartbeat_http_ready,
        heartbeat_im_ready,
        heartbeat_ready,
        heartbeat_stale,
        heartbeat_age_secs,
    }
}

// ============================================================
//  Platform / Window
// ============================================================

#[tauri::command]
pub fn get_platform_info() -> PlatformInfo {
    let home = dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."));
    PlatformInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        home_dir: home.to_string_lossy().to_string(),
        openakita_root_dir: state::openakita_root_dir().to_string_lossy().to_string(),
    }
}

#[tauri::command]
pub fn toggle_pet_window(app_handle: tauri::AppHandle, show: bool) -> Result<(), String> {
    if let Some(window) = app_handle.get_webview_window("pet_window") {
        if show {
            window.show().map_err(|e| e.to_string())?;
        } else {
            window.hide().map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub fn start_dragging(window: tauri::Window) -> Result<(), String> {
    window.start_dragging().map_err(|e| e.to_string())
}

// ============================================================
//  Root dir
// ============================================================

#[tauri::command]
pub fn get_root_dir_info() -> RootDirInfo {
    RootDirInfo {
        default_root: state::default_root_dir().to_string_lossy().to_string(),
        current_root: state::openakita_root_dir().to_string_lossy().to_string(),
        custom_root: state::read_root_config().custom_root,
    }
}

#[tauri::command]
pub fn set_custom_root_dir(path: Option<String>, migrate: bool) -> Result<RootDirInfo, String> {
    let _lock = state::ROOT_CONFIG_LOCK
        .lock()
        .map_err(|e| format!("lock failed: {e}"))?;
    let clean_path = path
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(String::from);

    if let Some(ref p) = clean_path {
        let target = PathBuf::from(p);
        if !target.is_absolute() {
            return Err("please use an absolute path (e.g. D:\\MyData\\.openakita or /data/openakita)".into());
        }
        state::ensure_safe_openakita_data_root(&target)?;
        if target.exists() && !target.is_dir() {
            return Err("specified path exists but is not a directory".into());
        }
        fs::create_dir_all(&target).map_err(|e| format!("cannot create target directory: {e}"))?;
        workspace::write_root_marker(&target)?;
        let test_file = target.join(".openakita_write_test");
        fs::write(&test_file, "test").map_err(|e| format!("target directory not writable: {e}"))?;
        let _ = fs::remove_file(&test_file);
    }

    let migrate_old_root: Option<PathBuf> = if migrate {
        let old_root = state::openakita_root_dir();
        let new_root_path = match &clean_path {
            Some(p) => PathBuf::from(p),
            None => state::default_root_dir(),
        };
        if old_root != new_root_path && old_root.exists() {
            if !new_root_path.exists() {
                fs::create_dir_all(&new_root_path)
                    .map_err(|e| format!("cannot create target directory: {e}"))?;
            }
            let critical_dirs = ["workspaces"];
            let optional_dirs = ["venv", "runtime", "run", "logs", "modules", "bin"];
            let mut errors: Vec<String> = Vec::new();

            for entry_name in critical_dirs.iter().chain(optional_dirs.iter()) {
                let src = old_root.join(entry_name);
                let dst = new_root_path.join(entry_name);
                if src.exists() && src.is_dir() && !dst.exists() {
                    if let Err(e) = workspace::copy_dir_recursive(&src, &dst) {
                        let msg = format!("{}: {}", entry_name, e);
                        eprintln!("migrate dir {}", msg);
                        if critical_dirs.contains(entry_name) {
                            let _ = fs::remove_dir_all(&dst);
                            return Err(format!(
                                "critical dir {} copy failed, migration aborted. error: {}",
                                entry_name, e
                            ));
                        }
                        errors.push(msg);
                    }
                }
            }
            for file_name in &["state.json", "cli.json"] {
                let src = old_root.join(file_name);
                let dst = new_root_path.join(file_name);
                if src.exists() && src.is_file() && !dst.exists() {
                    if let Err(e) = fs::copy(&src, &dst) {
                        errors.push(format!("{}: {}", file_name, e));
                        eprintln!("migrate file {}: {}", file_name, e);
                    }
                }
            }
            if !errors.is_empty() {
                eprintln!(
                    "migration completed with {} non-critical errors",
                    errors.len()
                );
            }
            if !new_root_path.exists() || !new_root_path.is_dir() {
                return Err(
                    "after migration, target directory not accessible. please check disk connection and retry."
                        .into(),
                );
            }
            Some(old_root)
        } else {
            None
        }
    } else {
        None
    };

    let config = state::RootConfig {
        custom_root: clean_path,
    };
    state::write_root_config(&config)?;
    state::invalidate_root_cache();

    if let Some(ref old_root) = migrate_old_root {
        if state::is_safe_openakita_data_root(old_root) {
            let dir_names = [
                "workspaces", "venv", "runtime", "run", "logs", "modules", "bin",
            ];
            let file_names = ["state.json", "cli.json"];
            for name in &dir_names {
                let p = old_root.join(name);
                if p.exists() && p.is_dir() {
                    if let Err(e) = fs::remove_dir_all(&p) {
                        eprintln!("cleanup old {}: {e}", p.display());
                    }
                }
            }
            for name in &file_names {
                let p = old_root.join(name);
                if p.exists() && p.is_file() {
                    let _ = fs::remove_file(&p);
                }
            }
        }
    }

    Ok(RootDirInfo {
        default_root: state::default_root_dir().to_string_lossy().to_string(),
        current_root: state::openakita_root_dir().to_string_lossy().to_string(),
        custom_root: config.custom_root,
    })
}

// ============================================================
//  Migrate preflight
// ============================================================

fn available_space_mb(path: &Path) -> f64 {
    #[cfg(target_os = "windows")]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        let fallback = path.ancestors().last().map(|r| r.to_string_lossy().to_string()).unwrap_or_else(|| "C:\\".to_string());
        let wide: Vec<u16> = OsStr::new(path.to_str().unwrap_or(&fallback)).encode_wide().chain(std::iter::once(0)).collect();
        let mut free_bytes: u64 = 0;
        unsafe {
            #[link(name = "kernel32")]
            extern "system" {
                fn GetDiskFreeSpaceExW(lpDirectoryName: *const u16, lpFreeBytesAvailableToCaller: *mut u64, lpTotalNumberOfBytes: *mut u64, lpTotalNumberOfFreeBytes: *mut u64) -> i32;
            }
            GetDiskFreeSpaceExW(wide.as_ptr(), &mut free_bytes, std::ptr::null_mut(), std::ptr::null_mut());
        }
        free_bytes as f64 / 1024.0 / 1024.0
    }
    #[cfg(not(target_os = "windows"))]
    {
        use std::mem::MaybeUninit;
        let c_path = std::ffi::CString::new(path.to_str().unwrap_or("/")).unwrap_or_default();
        let mut stat = MaybeUninit::<libc::statvfs>::uninit();
        let ok = unsafe { libc::statvfs(c_path.as_ptr(), stat.as_mut_ptr()) };
        if ok == 0 {
            let stat = unsafe { stat.assume_init() };
            (stat.f_bavail as f64) * (stat.f_frsize as f64) / 1024.0 / 1024.0
        } else {
            0.0
        }
    }
}

#[tauri::command]
pub fn preflight_migrate_root(target_path: String) -> Result<MigratePreflightInfo, String> {
    let target = PathBuf::from(target_path.trim());
    if !target.is_absolute() {
        return Err("please use an absolute path".into());
    }
    state::ensure_safe_openakita_data_root(&target)?;

    let source = state::openakita_root_dir();
    if source == target {
        return Ok(MigratePreflightInfo {
            source_path: source.to_string_lossy().to_string(),
            source_size_mb: 0.0,
            target_path: target.to_string_lossy().to_string(),
            target_free_mb: 0.0,
            entries: vec![],
            can_migrate: false,
            reason: "target path is same as current path".into(),
        });
    }

    let dir_names: &[&str] = &["workspaces", "venv", "runtime", "run", "logs", "modules", "bin"];
    let file_names: &[&str] = &["state.json", "cli.json"];

    let mut entries = Vec::new();
    let mut total_size: u64 = 0;

    for name in dir_names {
        let src = source.join(name);
        if src.exists() && src.is_dir() {
            let size = util::dir_size_bytes(&src);
            total_size += size;
            entries.push(MigrateEntry { name: name.to_string(), size_mb: size as f64 / 1024.0 / 1024.0, exists_at_target: target.join(name).exists(), is_dir: true });
        }
    }
    for name in file_names {
        let src = source.join(name);
        if src.exists() && src.is_file() {
            let size = src.metadata().map(|m| m.len()).unwrap_or(0);
            total_size += size;
            entries.push(MigrateEntry { name: name.to_string(), size_mb: size as f64 / 1024.0 / 1024.0, exists_at_target: target.join(name).exists(), is_dir: false });
        }
    }

    let free_space_path = if target.exists() { target.clone() } else { target.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| target.clone()) };
    let target_free_mb = available_space_mb(&free_space_path);
    let source_size_mb = total_size as f64 / 1024.0 / 1024.0;

    let has_conflicts = entries.iter().any(|e| e.exists_at_target);
    let enough_space = target_free_mb > source_size_mb * 1.1 + 100.0;

    let (can_migrate, reason) = if entries.is_empty() {
        (false, "current data directory is empty, nothing to migrate".into())
    } else if !enough_space {
        (false, format!("target disk space insufficient (need {:.0} MB, available {:.0} MB)", source_size_mb * 1.1, target_free_mb))
    } else if has_conflicts {
        (true, "some data already exists at target, existing data will be skipped".into())
    } else {
        (true, "ready to migrate".into())
    };

    Ok(MigratePreflightInfo { source_path: source.to_string_lossy().to_string(), source_size_mb, target_path: target.to_string_lossy().to_string(), target_free_mb, entries, can_migrate, reason })
}

// ============================================================
//  Workspace management
// ============================================================

#[tauri::command]
pub fn list_workspaces() -> Result<Vec<state::WorkspaceSummary>, String> {
    let root = state::openakita_root_dir();
    fs::create_dir_all(&root).map_err(|e| format!("create root failed: {e}"))?;
    fs::create_dir_all(state::workspaces_dir()).map_err(|e| format!("create workspaces dir failed: {e}"))?;
    let state_file = state::read_state_file();
    let current = state_file.current_workspace_id.clone();
    let mut out = vec![];
    for w in state_file.workspaces {
        let dir = state::workspace_dir(&w.id);
        workspace::ensure_workspace_scaffold(&dir)?;
        out.push(state::WorkspaceSummary { id: w.id.clone(), name: w.name.clone(), path: dir.to_string_lossy().to_string(), is_current: current.as_deref() == Some(&w.id) });
    }
    Ok(out)
}

#[tauri::command]
pub fn create_workspace(id: String, name: String, set_current: bool) -> Result<state::WorkspaceSummary, String> {
    let (summary, _state) = workspace::create_workspace_impl(id, name, set_current)?;
    Ok(summary)
}

#[tauri::command]
pub fn set_current_workspace(id: String) -> Result<(), String> {
    workspace::set_current_workspace_impl(id)?;
    Ok(())
}

#[tauri::command]
pub fn get_current_workspace_id() -> Result<Option<String>, String> {
    let s = state::read_state_file();
    Ok(s.current_workspace_id)
}

#[tauri::command]
pub fn workspace_read_file(workspace_id: String, relative_path: String) -> Result<String, String> {
    let path = workspace_file_path(&workspace_id, &relative_path)?;
    fs::read_to_string(&path).map_err(|e| format!("read failed: {e}"))
}

#[tauri::command]
pub fn workspace_write_file(workspace_id: String, relative_path: String, content: String) -> Result<(), String> {
    let path = workspace_file_path(&workspace_id, &relative_path)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create parent dir failed: {e}"))?;
    }
    fs::write(&path, content).map_err(|e| format!("write failed: {e}"))
}

#[tauri::command]
pub fn workspace_update_env(workspace_id: String, entries: Vec<workspace::EnvEntry>) -> Result<(), String> {
    let dir = state::workspace_dir(&workspace_id);
    workspace::ensure_workspace_scaffold(&dir)?;
    let env_path = dir.join(".env");
    let existing = util::read_text_lossy(&env_path);
    let updated = workspace::update_env_content(&existing, &entries);
    fs::write(&env_path, updated).map_err(|e| format!("write .env failed: {e}"))
}

// ============================================================
//  Workspace backup
// ============================================================

fn export_workspace_backup_native(workspace_id: &str, output_dir: &str, include_userdata: bool, include_media: bool) -> Result<serde_json::Value, String> {
    let ws = state::workspace_dir(workspace_id);
    if !ws.exists() {
        return Err("workspace directory not found".into());
    }
    let out = PathBuf::from(output_dir);
    fs::create_dir_all(&out).map_err(|e| format!("create output dir: {e}"))?;
    let ts = crate::util::chrono_like_timestamp();
    let zip_name = format!("openakita-backup-{}-{}.zip", workspace_id, ts);
    let zip_path = out.join(&zip_name);
    let file = fs::File::create(&zip_path).map_err(|e| format!("create zip: {e}"))?;
    let mut zw = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let always_dirs = ["identity", "data/agents", "data/sessions", "data/scheduler", "data/mcp", "data/telegram", "skills", "mcps"];
    let always_files = [".env", "data/llm_endpoints.json", "data/skills.json", "data/disabled_views.json", "data/runtime_state.json", "data/proactive_feedback.json", "data/sub_agent_states.json"];
    let userdata_dirs = ["data/memory", "data/retrospects", "data/plans", "data/docs", "data/reports", "data/research"];
    let userdata_files = ["data/agent.db"];
    let media_dirs = ["data/generated_images", "data/sticker", "data/media", "data/output", "data/screenshots"];
    let exclude_dirs = ["logs", "data/llm_debug", "data/delegation_logs", "data/traces", "data/react_traces", "data/temp", "data/tool_overflow", "data/selfcheck", "data/openakita_docs", "identity/runtime", "node_modules", "Lib", "__pycache__"];
    let mut file_count: u64 = 0;
    for entry in crate::zip_utils::walkdir(&ws) {
        let full = entry.path().to_path_buf();
        if !full.is_file() { continue; }
        let rel = match full.strip_prefix(&ws) {
            Ok(r) => r.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };
        if exclude_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/"))) { continue; }
        if rel == "data/backend.heartbeat" || rel == "package.json" || rel == "package-lock.json" { continue; }
        let included = always_files.contains(&rel.as_str())
            || always_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/")))
            || (include_userdata && (userdata_files.contains(&rel.as_str()) || userdata_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/")))))
            || (include_media && media_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/"))));
        if !included { continue; }
        if let Ok(mut f) = fs::File::open(&full) {
            let _ = zw.start_file(&rel, options);
            let mut buf = Vec::new();
            if f.read_to_end(&mut buf).is_ok() { let _ = zw.write_all(&buf); file_count += 1; }
        }
    }
    let manifest = serde_json::json!({"format_version": 1, "created_at": crate::util::chrono_like_timestamp(), "workspace_id": workspace_id, "include_userdata": include_userdata, "include_media": include_media, "file_count": file_count});
    let _ = zw.start_file("manifest.json", options);
    let _ = zw.write_all(serde_json::to_string_pretty(&manifest).unwrap_or_default().as_bytes());
    zw.finish().map_err(|e| format!("finalize zip: {e}"))?;
    let size = fs::metadata(&zip_path).map(|m| m.len()).unwrap_or(0);
    Ok(serde_json::json!({"status": "ok", "path": zip_path.to_string_lossy(), "filename": zip_name, "size_bytes": size}))
}

fn import_workspace_backup_native(workspace_id: &str, zip_path: &str) -> Result<serde_json::Value, String> {
    let zp = PathBuf::from(zip_path);
    if !zp.exists() { return Err("backup file not found".into()); }
    let ws = state::workspace_dir(workspace_id);
    fs::create_dir_all(&ws).map_err(|e| format!("create workspace dir: {e}"))?;
    let file = fs::File::open(&zp).map_err(|e| format!("open zip: {e}"))?;
    let mut archive = zip::ZipArchive::new(file).map_err(|e| format!("read zip: {e}"))?;
    let mut restored = 0u64;
    for i in 0..archive.len() {
        let mut entry = archive.by_index(i).map_err(|e| format!("zip entry: {e}"))?;
        let name = entry.name().to_string();
        if name == "manifest.json" { continue; }
        let norm = PathBuf::from(&name);
        if norm.components().any(|c| matches!(c, std::path::Component::ParentDir)) { continue; }
        let target = ws.join(&name);
        if entry.is_dir() { let _ = fs::create_dir_all(&target); continue; }
        if let Some(parent) = target.parent() { let _ = fs::create_dir_all(parent); }
        let mut buf = Vec::new();
        if entry.read_to_end(&mut buf).is_ok() { if fs::write(&target, &buf).is_ok() { restored += 1; } }
    }
    Ok(serde_json::json!({"status": "ok", "restored_count": restored}))
}

#[tauri::command]
pub fn export_workspace_backup(workspace_id: String, output_dir: String, include_userdata: bool, include_media: bool, api_port: u16) -> Result<serde_json::Value, String> {
    let url = format!("http://127.0.0.1:{}/api/workspace/export", api_port);
    let body = serde_json::json!({"output_dir": output_dir, "include_userdata": include_userdata, "include_media": include_media});
    let client = reqwest::blocking::Client::builder().timeout(std::time::Duration::from_secs(300)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    match client.post(&url).json(&body).send() {
        Ok(r) if r.status().is_success() => r.json().map_err(|e| format!("parse response: {e}")),
        Ok(r) => { let status = r.status(); let text = r.text().unwrap_or_default(); Err(format!("backend returned {status}: {text}")) }
        Err(_) => export_workspace_backup_native(&workspace_id, &output_dir, include_userdata, include_media),
    }
}

#[tauri::command]
pub fn import_workspace_backup(workspace_id: String, zip_path: String, api_port: u16) -> Result<serde_json::Value, String> {
    let url = format!("http://127.0.0.1:{}/api/workspace/import", api_port);
    let body = serde_json::json!({"zip_path": zip_path});
    let client = reqwest::blocking::Client::builder().timeout(std::time::Duration::from_secs(300)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    match client.post(&url).json(&body).send() {
        Ok(r) if r.status().is_success() => r.json().map_err(|e| format!("parse: {e}")),
        Ok(r) => { let status = r.status(); let text = r.text().unwrap_or_default(); Err(format!("backend returned {status}: {text}")) }
        Err(_) => import_workspace_backup_native(&workspace_id, &zip_path),
    }
}
// ============================================================
//  Python detection
// ============================================================

#[tauri::command]
pub fn detect_python() -> Vec<PythonCandidate> {
    let mut out = vec![];
    let root = state::openakita_root_dir();
    let venv_py = if cfg!(windows) { root.join("venv").join("Scripts").join("python.exe") } else { root.join("venv").join("bin").join("python") };
    if venv_py.exists() {
        let c = vec![venv_py.to_string_lossy().to_string()];
        let mut cmd = c.clone();
        cmd.push("--version".into());
        let version_text = util::run_capture(&cmd).unwrap_or_else(|e| e);
        let is_usable = crate::util::python_version_ok(&version_text);
        out.push(PythonCandidate { command: c, version_text, is_usable });
    }
    if let Some(bundled_py) = python_env::bundles_internal_python_path() {
        let c = vec![bundled_py.to_string_lossy().to_string()];
        let mut cmd = c.clone();
        cmd.push("--version".into());
        let version_text = util::run_capture(&cmd).unwrap_or_else(|e| e);
        let is_usable = crate::util::python_version_ok(&version_text);
        out.push(PythonCandidate { command: c, version_text, is_usable });
    }
    if out.is_empty() {
        out.push(PythonCandidate { command: vec![], version_text: "no usable project-bundled Python detected".to_string(), is_usable: false });
    }
    out
}


// ============================================================
//  Python diagnostics
// ============================================================

fn python_diag_trace_id() -> String {
    let now_ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis();
    format!("pydiag-{}", now_ms)
}

fn python_diag_generated_at() -> String {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs().to_string()
}

fn make_backend_starting_diagnostic(trace_id: String, port: u16, phase: &str) -> PythonDiagnostic {
    PythonDiagnostic {
        summary: "healthy".into(),
        contracts: vec![PythonContractResult {
            id: "C0_BACKEND_STARTING".into(), title: "backend service".into(), status: "warn".into(),
            code: "BACKEND_STARTING".into(), evidence: vec![format!("phase: {}, port {}", phase, port)],
            auto_fix: false, fix_hint: Some("backend is starting, please try again later".into()),
        }],
        environment: PythonEnvironmentSnapshot {
            platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
            bundled_python_path: None, openakita_version: None,
        },
        trace_id, generated_at: python_diag_generated_at(),
    }
}

fn make_backend_api_unreachable_diagnostic(trace_id: String, port: u16) -> PythonDiagnostic {
    PythonDiagnostic {
        summary: "healthy".into(),
        contracts: vec![PythonContractResult {
            id: "C0_BACKEND_OFFLINE".into(), title: "backend service".into(), status: "warn".into(),
            code: "BACKEND_API_UNREACHABLE".into(),
            evidence: vec![format!("heartbeat ok, port {} API unreachable - retrying may help", port)],
            auto_fix: false, fix_hint: Some("backend is running but API is temporarily unreachable, please retry".into()),
        }],
        environment: PythonEnvironmentSnapshot {
            platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
            bundled_python_path: None, openakita_version: None,
        },
        trace_id, generated_at: python_diag_generated_at(),
    }
}

fn parse_diagnostics_json(json: &serde_json::Value) -> Option<PythonDiagnostic> {
    let summary = json.get("summary").and_then(|v| v.as_str()).unwrap_or("healthy").to_string();
    let mut contracts: Vec<PythonContractResult> = vec![];
    if let Some(checks) = json.get("checks").and_then(|v| v.as_array()) {
        for c in checks {
            contracts.push(PythonContractResult {
                id: c.get("id").and_then(|v| v.as_str()).unwrap_or("").into(),
                title: c.get("title").and_then(|v| v.as_str()).unwrap_or("").into(),
                status: c.get("status").and_then(|v| v.as_str()).unwrap_or("pass").into(),
                code: c.get("code").and_then(|v| v.as_str()).unwrap_or("").into(),
                evidence: c.get("evidence").and_then(|v| v.as_array()).map(|arr| arr.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default(),
                auto_fix: c.get("autoFix").and_then(|v| v.as_bool()).unwrap_or(false),
                fix_hint: c.get("fixHint").and_then(|v| v.as_str()).map(String::from),
            });
        }
    }
    let env_obj = json.get("environment");
    let environment = PythonEnvironmentSnapshot {
        platform: env_obj.and_then(|e| e.get("platform")).and_then(|v| v.as_str()).unwrap_or("").to_string(),
        bundled_python_path: None,
        openakita_version: env_obj.and_then(|e| e.get("openakitaVersion")).and_then(|v| v.as_str()).map(String::from),
    };
    Some(PythonDiagnostic { summary, contracts, environment, trace_id: String::new(), generated_at: String::new() })
}

fn diagnose_via_backend_api(port: u16) -> Option<PythonDiagnostic> {
    {
        use std::net::TcpStream;
        let addr = format!("127.0.0.1:{}", port);
        if TcpStream::connect_timeout(&addr.parse().ok()?, std::time::Duration::from_secs(2)).is_err() { return None; }
    }
    let client = &*state::BLOCKING_HTTP_CLIENT;
    let url = format!("http://127.0.0.1:{}/api/diagnostics", port);
    let max_attempts: u8 = 2;
    let mut last_err = String::new();
    for attempt in 0..max_attempts {
        if attempt > 0 { std::thread::sleep(std::time::Duration::from_millis(1500)); }
        match client.get(&url).timeout(std::time::Duration::from_secs(6)).send() {
            Ok(resp) if resp.status().is_success() => match resp.json::<serde_json::Value>() {
                Ok(json) => return parse_diagnostics_json(&json),
                Err(e) => { last_err = format!("json parse: {e}"); continue; }
            },
            Ok(resp) => { last_err = format!("HTTP {}", resp.status()); continue; }
            Err(e) => {
                let msg = format!("{e}");
                if msg.contains("onnection refused") || msg.contains("No connection") { return None; }
                last_err = msg; continue;
            }
        }
    }
    eprintln!("[diagnose] backend API unreachable after {} attempts (port={}): {}", max_attempts, port, last_err);
    None
}

#[tauri::command]
pub fn diagnose_python_env(venv_dir: String) -> PythonDiagnostic {
    let _ = venv_dir;
    let trace_id = python_diag_trace_id();
    let state_file = state::read_state_file();
    let ws_id = state_file.current_workspace_id.clone();
    let port = ws_id.as_deref().and_then(process::read_workspace_api_port).unwrap_or(18900);

    let heartbeat = ws_id.as_deref().and_then(state::read_heartbeat_file);
    let backend_phase = heartbeat.as_ref().map(|hb| hb.phase.as_str()).unwrap_or("");
    let http_ready = heartbeat.as_ref().map(|hb| hb.http_ready).unwrap_or(false);
    let hb_fresh = heartbeat.as_ref().map(|hb| { let age = crate::util::now_epoch_secs() as f64 - hb.timestamp; age <= 30.0 }).unwrap_or(false);

    if hb_fresh && !http_ready && matches!(backend_phase, "starting" | "initializing") {
        return make_backend_starting_diagnostic(trace_id, port, backend_phase);
    }

    if let Some(diag) = diagnose_via_backend_api(port) {
        return PythonDiagnostic { summary: diag.summary, contracts: diag.contracts, environment: diag.environment, trace_id, generated_at: python_diag_generated_at() };
    }

    if hb_fresh && http_ready {
        return make_backend_api_unreachable_diagnostic(trace_id, port);
    }

    let bundled_dir = python_env::bundled_backend_dir();
    let bundled_exe = if cfg!(windows) { bundled_dir.join("openakita-server.exe") } else { bundled_dir.join("openakita-server") };
    let internal_dir = bundled_dir.join("_internal");
    let mut contracts: Vec<PythonContractResult> = vec![];

    if bundled_exe.exists() && internal_dir.exists() {
        contracts.push(PythonContractResult { id: "C1_BUNDLED_RUNTIME".into(), title: "bundled runtime".into(), status: "pass".into(), code: "RUNTIME_OK".into(), evidence: vec![format!("binary: {}", bundled_exe.display())], auto_fix: false, fix_hint: None });
    } else {
        let mut missing = vec![];
        if !bundled_exe.exists() { missing.push(format!("missing: {}", bundled_exe.display())); }
        if !internal_dir.exists() { missing.push(format!("missing: {}", internal_dir.display())); }
        contracts.push(PythonContractResult { id: "C1_BUNDLED_RUNTIME".into(), title: "bundled runtime".into(), status: "fail".into(), code: "RUNTIME_MISSING".into(), evidence: missing, auto_fix: false, fix_hint: Some("reinstall OpenAkita to restore bundled runtime".into()) });
    }

    contracts.push(PythonContractResult { id: "C0_BACKEND_OFFLINE".into(), title: "backend service".into(), status: "warn".into(), code: "BACKEND_NOT_RUNNING".into(), evidence: vec![format!("port {} unreachable", port)], auto_fix: false, fix_hint: Some("start the backend to get full diagnostics".into()) });

    let failing: Vec<&PythonContractResult> = contracts.iter().filter(|c| c.status == "fail").collect();
    let summary = if failing.is_empty() { "healthy" } else { "broken" }.to_string();

    PythonDiagnostic {
        summary, contracts,
        environment: PythonEnvironmentSnapshot { platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH), bundled_python_path: None, openakita_version: None },
        trace_id, generated_at: python_diag_generated_at(),
    }
}

#[tauri::command]
pub fn export_python_diagnostic_report(venv_dir: String) -> Result<String, String> {
    let diag = diagnose_python_env(venv_dir);
    let report_dir = state::openakita_root_dir().join("runtime").join("reports");
    fs::create_dir_all(&report_dir).map_err(|e| format!("create report dir failed: {e}"))?;
    let report_path = report_dir.join(format!("python-diagnostic-{}.json", diag.trace_id));
    let text = serde_json::to_string_pretty(&diag).map_err(|e| format!("serialize report failed: {e}"))?;
    fs::write(&report_path, text).map_err(|e| format!("write report failed: {e}"))?;
    Ok(report_path.to_string_lossy().to_string())
}

#[tauri::command]
pub fn check_python_for_pip() -> Result<String, String> {
    match python_env::find_pip_python() {
        Some(p) => Ok(format!("Python available: {}", p.display())),
        None => Err("no usable Python interpreter found".into()),
    }
}


// ============================================================
//  Bundled Python install
// ============================================================

fn install_bundled_python_sync(_python_series: Option<String>, _log_path: Option<PathBuf>) -> Result<BundledPythonInstallResult, String> {
    let py = python_env::bundles_internal_python_path().ok_or_else(|| {
        "bundled Python not available; reinstall OpenAkita to restore resources/openakita-server/_internal".to_string()
    })?;
    let bundled_dir = python_env::bundled_backend_dir();
    Ok(BundledPythonInstallResult {
        python_command: vec![py.to_string_lossy().to_string()],
        python_path: py.to_string_lossy().to_string(),
        install_dir: bundled_dir.to_string_lossy().to_string(),
        asset_name: "bundled-internal".to_string(),
        tag: "bundled".to_string(),
    })
}

#[tauri::command]
pub async fn install_bundled_python(python_series: Option<String>, log_path: Option<String>) -> Result<BundledPythonInstallResult, String> {
    let path_buf = log_path.map(PathBuf::from);
    spawn_blocking_result(move || install_bundled_python_sync(python_series, path_buf)).await
}

// ============================================================
//  Create venv
// ============================================================

#[tauri::command]
pub async fn create_venv(python_command: Vec<String>, venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let venv = PathBuf::from(&venv_dir);
        if venv.exists() { return Ok(venv.to_string_lossy().to_string()); }
        let _ = python_command;
        let bundled_py = python_env::bundles_internal_python_path().ok_or_else(|| "bundled Python not available, please reinstall OpenAkita".to_string())?;
        let mut c = Command::new(&bundled_py);
        python_env::apply_no_window(&mut c);
        python_env::apply_bundled_python_env(&mut c, &python_env::bundled_backend_dir().join("_internal"));
        c.args(["-m", "venv"]).arg(&venv).status().map_err(|e| format!("failed to create venv: {e}"))?.success().then_some(()).ok_or_else(|| "venv creation failed".to_string())?;
        Ok(venv.to_string_lossy().to_string())
    }).await
}

// ============================================================
//  pip install
// ============================================================

#[tauri::command]
pub async fn pip_install(app: tauri::AppHandle, venv_dir: String, package_spec: String, index_url: Option<String>) -> Result<String, String> {
    spawn_blocking_result(move || {
        let (py, pythonpath) = python_env::resolve_python(&venv_dir)?;
        let mut log = String::new();

        #[derive(Serialize, Clone)]
        #[serde(rename_all = "camelCase")]
        struct PipInstallEvent { kind: String, stage: Option<String>, percent: Option<u8>, text: Option<String> }

        let emit_stage = |stage: &str, percent: u8| {
            let _ = app.emit("pip_install_event", PipInstallEvent { kind: "stage".into(), stage: Some(stage.into()), percent: Some(percent), text: None });
        };
        let emit_line = |text: &str| {
            let _ = app.emit("pip_install_event", PipInstallEvent { kind: "line".into(), stage: None, percent: None, text: Some(text.into()) });
        };

        fn run_streaming(mut cmd: Command, header: &str, log: &mut String, emit_line: &dyn Fn(&str)) -> Result<std::process::ExitStatus, String> {
            use std::io::Read as _;
            use std::process::Stdio;
            use std::sync::mpsc;
            use std::thread;

            emit_line(&format!("\n=== {} ===\n", header));
            log.push_str(&format!("=== {} ===\n", header));

            cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
            let mut child = cmd.spawn().map_err(|e| format!("{} failed to start: {e}", header))?;
            let mut stdout = child.stdout.take().ok_or_else(|| format!("{} stdout pipe missing", header))?;
            let mut stderr = child.stderr.take().ok_or_else(|| format!("{} stderr pipe missing", header))?;

            let (tx, rx) = mpsc::channel::<(bool, String)>();
            let tx1 = tx.clone();
            let h1 = thread::spawn(move || {
                let mut buf = [0u8; 4096];
                loop {
                    match stdout.read(&mut buf) {
                        Ok(0) => break,
                        Ok(n) => { let s = String::from_utf8_lossy(&buf[..n]).to_string(); let _ = tx1.send((false, s)); }
                        Err(_) => break,
                    }
                }
            });
            let tx2 = tx.clone();
            let h2 = thread::spawn(move || {
                let mut buf = [0u8; 4096];
                loop {
                    match stderr.read(&mut buf) {
                        Ok(0) => break,
                        Ok(n) => { let s = String::from_utf8_lossy(&buf[..n]).to_string(); let _ = tx2.send((true, s)); }
                        Err(_) => break,
                    }
                }
            });
            drop(tx);

            loop {
                match rx.recv_timeout(std::time::Duration::from_millis(120)) {
                    Ok((_is_err, chunk)) => { emit_line(&chunk); log.push_str(&chunk); }
                    Err(mpsc::RecvTimeoutError::Timeout) => { if let Ok(Some(_)) = child.try_wait() { break; } }
                    Err(mpsc::RecvTimeoutError::Disconnected) => break,
                }
            }

            let status = child.wait().map_err(|e| format!("{} wait failed: {e}", header))?;
            let _ = h1.join(); let _ = h2.join();
            while let Ok((_is_err, chunk)) = rx.try_recv() { emit_line(&chunk); log.push_str(&chunk); }
            log.push_str("\n\n");
            Ok(status)
        }

        let effective_index = index_url.as_deref().unwrap_or("https://mirrors.aliyun.com/pypi/simple/");
        let effective_host = effective_index.split("//").nth(1).unwrap_or("").split('/').next().unwrap_or("");

        emit_stage("upgrade pip (best-effort)", 40);
        let mut up = Command::new(&py);
        python_env::apply_no_window(&mut up);
        python_env::strip_harmful_python_env(&mut up);
        up.env("PYTHONUTF8", "1"); up.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath { up.env("PYTHONPATH", pp); }
        up.args(["-m", "pip", "install", "-U", "pip", "setuptools", "wheel"]);
        up.args(["-i", effective_index]);
        if !effective_host.is_empty() { up.args(["--trusted-host", effective_host]); }
        let _ = run_streaming(up, "pip upgrade (best-effort)", &mut log, &emit_line);

        emit_stage("install openakita (pip)", 70);
        let mut c = Command::new(&py);
        python_env::apply_no_window(&mut c);
        python_env::strip_harmful_python_env(&mut c);
        c.env("PYTHONUTF8", "1"); c.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath { c.env("PYTHONPATH", pp); }
        c.args(["-m", "pip", "install", "-U", &package_spec]);
        c.args(["-i", effective_index]);
        if !effective_host.is_empty() { c.args(["--trusted-host", effective_host]); }
        let status = run_streaming(c, "pip install", &mut log, &emit_line)?;
        if !status.success() {
            let tail = if log.len() > 6000 { &log[log.len() - 6000..] } else { &log };
            return Err(format!("pip install failed: {}\n\n--- output tail ---\n{}", status, tail));
        }

        emit_stage("verify install", 95);
        emit_line("\n=== verify ===\n");
        let mut verify = Command::new(&py);
        python_env::apply_no_window(&mut verify);
        python_env::strip_harmful_python_env(&mut verify);
        verify.env("PYTHONUTF8", "1"); verify.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath { verify.env("PYTHONPATH", pp); }
        verify.args(["-c", "import openakita; import openakita.setup_center.bridge; print(getattr(openakita,'__version__',''))"]);
        let v = verify.output().map_err(|e| format!("verify openakita failed: {e}"))?;
        if !v.status.success() {
            let stdout = String::from_utf8_lossy(&v.stdout).to_string();
            let stderr = String::from_utf8_lossy(&v.stderr).to_string();
            return Err(format!("openakita installed but missing Setup Center module (openakita.setup_center.bridge). This usually means the installed openakita version is too old. stdout:\n{}\nstderr:\n{}", stdout, stderr));
        }

        let ver = String::from_utf8_lossy(&v.stdout).trim().to_string();
        log.push_str("=== verify ===\n"); log.push_str("import openakita.setup_center.bridge: OK\n");
        emit_line("import openakita.setup_center.bridge: OK\n");
        if !ver.is_empty() { log.push_str(&format!("openakita version: {}\n", ver)); emit_line(&format!("openakita version: {}\n", ver)); }
        emit_stage("done", 100);
        Ok(log)
    }).await
}

// ============================================================
//  pip uninstall
// ============================================================

#[tauri::command]
pub async fn pip_uninstall(venv_dir: String, package_name: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let (py, pythonpath) = python_env::resolve_python(&venv_dir)?;
        if package_name.trim().is_empty() { return Err("package_name is empty".into()); }
        let mut c = Command::new(&py);
        python_env::apply_no_window(&mut c);
        python_env::strip_harmful_python_env(&mut c);
        if let Some(ref pp) = pythonpath { c.env("PYTHONPATH", pp); }
        c.args(["-m", "pip", "uninstall", "-y", package_name.trim()]);
        let status = c.status().map_err(|e| format!("pip uninstall failed to start: {e}"))?;
        if !status.success() { return Err(format!("pip uninstall failed: {}", status)); }
        Ok("ok".into())
    }).await
}


// ============================================================
//  Autostart
// ============================================================

#[tauri::command]
pub fn autostart_is_enabled(app: tauri::AppHandle) -> Result<bool, String> {
    #[cfg(desktop)]
    { let mgr = app.autolaunch(); return mgr.is_enabled().map_err(|e| format!("autostart is_enabled failed: {e}")); }
    #[cfg(not(desktop))]
    { let _ = app; Ok(false) }
}

#[tauri::command]
pub fn autostart_set_enabled(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    #[cfg(desktop)]
    {
        let mgr = app.autolaunch();
        if enabled { mgr.enable().map_err(|e| format!("autostart enable failed: {e}"))?; }
        else { mgr.disable().map_err(|e| format!("autostart disable failed: {e}"))?; }
        let mut s = state::read_state_file(); s.auto_start_backend = Some(enabled);
        let _ = state::write_state_file(&s);
        return Ok(());
    }
    #[cfg(not(desktop))]
    { let _ = (app, enabled); Ok(()) }
}

// ============================================================
//  Service status
// ============================================================

#[tauri::command]
pub fn openakita_service_status(workspace_id: String) -> Result<ServiceStatus, String> {
    let pid_file = state::service_pid_file(&workspace_id);
    let pf = pid_file.to_string_lossy().to_string();

    {
        let mut guard = state::MANAGED_CHILD.lock().unwrap();
        if let Some(ref mut mp) = *guard {
            if mp.workspace_id == workspace_id {
                match mp.child.try_wait() {
                    Ok(None) => return Ok(build_service_status(&workspace_id, true, Some(mp.pid), pf)),
                    _ => { *guard = None; let _ = fs::remove_file(&pid_file); state::remove_heartbeat_file(&workspace_id); return Ok(build_service_status(&workspace_id, false, None, pf)); }
                }
            }
        }
    }

    if let Some(data) = state::read_pid_file(&workspace_id) {
        if process::is_pid_file_valid(&data) {
            return Ok(build_service_status(&workspace_id, true, Some(data.pid), pf));
        } else {
            let _ = fs::remove_file(&pid_file);
            state::remove_heartbeat_file(&workspace_id);
        }
    }
    Ok(build_service_status(&workspace_id, false, None, pf))
}

// ============================================================
//  Service start (async wrapper)
// ============================================================

#[tauri::command]
pub async fn openakita_service_start(venv_dir: String, workspace_id: String) -> Result<ServiceStatus, String> {
    let task_started = Instant::now();
    let log_workspace_id = workspace_id.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        openakita_service_start_impl(venv_dir, workspace_id)
    }).await.map_err(|e| format!("backend start task failed: {e}"))?;
    state::log_to_file(&format!("[service_start] async command finished: ws={}, elapsed_ms={}, status={}", log_workspace_id, task_started.elapsed().as_millis(), if result.is_ok() { "ok" } else { "error" }));
    result
}

// ============================================================
//  Service start impl (the full implementation)
// ============================================================

#[tauri::command]
pub fn openakita_service_start_impl(venv_dir: String, workspace_id: String) -> Result<ServiceStatus, String> {
    let service_start_started = Instant::now();
    state::log_to_file(&format!("[service_start] called: ws={}, venv={}", workspace_id, venv_dir));

    // Deduplication check
    {
        let mut last_map = state::SERVICE_START_LAST_AT.lock().unwrap();
        let now = crate::util::now_ms();
        if let Some(&last) = last_map.get(&workspace_id) {
            let elapsed = now.saturating_sub(last);
            if elapsed < state::SERVICE_START_DEDUPE_MS {
                state::log_to_file(&format!("[service_start] dedupe-skip ws={} elapsed_ms={}", workspace_id, elapsed));
                let pid_file = state::service_pid_file(&workspace_id);
                let pf = pid_file.to_string_lossy().to_string();
                let pid_opt = state::read_pid_file(&workspace_id).map(|d| d.pid);
                let running = state::read_pid_file(&workspace_id).map(|d| process::is_pid_file_valid(&d)).unwrap_or(false);
                return Ok(build_service_status(&workspace_id, running, pid_opt, pf));
            }
        }
        last_map.insert(workspace_id.clone(), now);
    }

    fs::create_dir_all(state::run_dir()).map_err(|e| { let msg = format!("create run dir failed: {e}"); state::log_to_file(&format!("[service_start] FAIL: {}", msg)); msg })?;
    let pid_file = state::service_pid_file(&workspace_id);
    let pf = pid_file.to_string_lossy().to_string();

    state::remove_heartbeat_file(&workspace_id);

    // Check if already running
    {
        let mut guard = state::MANAGED_CHILD.lock().unwrap();
        if let Some(ref mut mp) = *guard {
            if mp.workspace_id == workspace_id {
                match mp.child.try_wait() {
                    Ok(None) => return Ok(build_service_status(&workspace_id, true, Some(mp.pid), pf)),
                    _ => { *guard = None; }
                }
            }
        }
    }
    if let Some(data) = state::read_pid_file(&workspace_id) {
        if process::is_pid_file_valid(&data) {
            if let Some(true) = state::is_heartbeat_stale(&workspace_id, 60) {
                let port = process::read_workspace_api_port(&workspace_id);
                if state::should_cleanup_stale_heartbeat(Some(true), state::is_backend_http_healthy(port)) {
                    let _ = process::graceful_stop_pid(data.pid, port);
                    let _ = fs::remove_file(&pid_file);
                    state::remove_heartbeat_file(&workspace_id);
                } else {
                    return Ok(build_service_status(&workspace_id, true, Some(data.pid), pf));
                }
            } else {
                return Ok(build_service_status(&workspace_id, true, Some(data.pid), pf));
            }
        } else {
            let _ = fs::remove_file(&pid_file);
            state::remove_heartbeat_file(&workspace_id);
        }
    }

    // Acquire start lock
    if !state::try_acquire_start_lock(&workspace_id) {
        return Err("another start operation is in progress, please wait".to_string());
    }
    struct LockGuard(String);
    impl Drop for LockGuard {
        fn drop(&mut self) { state::release_start_lock(&self.0); }
    }
    let _lock_guard = LockGuard(workspace_id.clone());

    let ws_dir = state::workspace_dir(&workspace_id);
    workspace::ensure_workspace_scaffold(&ws_dir)?;

    // Port check
    let effective_port = process::read_workspace_api_port(&workspace_id).unwrap_or(18900);
    if !process::check_port_available(effective_port) {
        if !process::wait_for_port_free(effective_port, 10_000) {
            return Err(format!("port {} is occupied, cannot start backend. Possible reasons: previous shutdown port not released or another program occupying the port. Please wait and retry, or check if another program is using port {}.", effective_port, effective_port));
        }
    }

    // Get backend executable
    let backend_resolve_started = Instant::now();
    let (backend_exe, backend_args) = runtime::get_backend_executable(&venv_dir);
    state::log_to_file(&format!("[service_start] backend executable resolved in {}ms", backend_resolve_started.elapsed().as_millis()));
    if !backend_exe.exists() {
        let bundled_dir = python_env::bundled_backend_dir();
        let bundled_name = if cfg!(windows) { "openakita-server.exe" } else { "openakita-server" };
        return Err(format!("backend executable not found: {}\nchecked paths:\n  - bundled: {}/{}\n  - venv: {}\ntry: 1) reinstall desktop 2) run quickstart.sh to create venv", backend_exe.to_string_lossy(), bundled_dir.display(), bundled_name, backend_exe.to_string_lossy()));
    }

    let log_dir = ws_dir.join("logs");
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let log_path = log_dir.join("openakita-serve.log");
    let log_file = std::fs::OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| format!("open log failed: {e}"))?;

    let mut cmd = Command::new(&backend_exe);
    cmd.current_dir(&ws_dir);
    cmd.args(&backend_args);

    runtime::apply_dual_runtime_env(&mut cmd);

    cmd.env("PYTHONUTF8", "1"); cmd.env("PYTHONIOENCODING", "utf-8"); cmd.env("PYTHONUNBUFFERED", "1"); cmd.env("NO_COLOR", "1");

    cmd.env("LLM_ENDPOINTS_CONFIG", ws_dir.join("data").join("llm_endpoints.json"));
    cmd.env("OPENAKITA_ROOT", state::openakita_root_dir().to_string_lossy().to_string());

    if let Some(extra_path) = runtime::build_modules_pythonpath() { cmd.env("OPENAKITA_MODULE_PATHS", extra_path); }

    let browsers_dir = runtime::modules_dir().join("browser").join("browsers");
    if browsers_dir.exists() { cmd.env("PLAYWRIGHT_BROWSERS_PATH", &browsers_dir); }

    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(log_file.try_clone().map_err(|e| format!("clone log failed: {e}"))?))
        .stderr(std::process::Stdio::from(log_file));

    #[cfg(windows)]
    { use std::os::windows::process::CommandExt; cmd.creation_flags(0x00000008u32 | 0x00000200u32 | 0x0800_0000u32); }

    let spawn_started = Instant::now();
    let child = cmd.spawn().map_err(|e| { let msg = format!("spawn openakita serve failed: {e}"); state::log_to_file(&format!("[service_start] {}", msg)); msg })?;
    let pid = child.id();
    state::log_to_file(&format!("[service_start] spawned pid={} in {}ms", pid, spawn_started.elapsed().as_millis()));
    let started_at = crate::util::now_epoch_secs();

    state::write_pid_file(&workspace_id, pid, "tauri")?;

    {
        let mut guard = state::MANAGED_CHILD.lock().unwrap();
        *guard = Some(state::ManagedProcess { child, workspace_id: workspace_id.clone(), pid, started_at });
    }

    // Confirm process alive after spawning
    let mut alive = true;
    for _ in 0..6 {
        std::thread::sleep(std::time::Duration::from_millis(500));
        if !process::is_pid_running(pid) { alive = false; break; }
    }
    if !alive {
        {
            let mut guard = state::MANAGED_CHILD.lock().unwrap();
            if let Some(ref mp) = *guard { if mp.pid == pid { *guard = None; } }
        }
        let _ = fs::remove_file(&pid_file);
        let tail = fs::read_to_string(&log_path).ok().and_then(|s| { if s.len() > 6000 { Some(s[s.len() - 6000..].to_string()) } else { Some(s) } }).unwrap_or_default();
        return Err(format!("openakita serve appears to have exited immediately (PID={}).\nplease check service log: {}\n\n--- log tail ---\n{}", pid, log_path.to_string_lossy(), tail));
    }

    state::log_to_file(&format!("[service_start] completed in {}ms", service_start_started.elapsed().as_millis()));
    Ok(build_service_status(&workspace_id, true, Some(pid), pf))
}


// ============================================================
//  Service stop
// ============================================================

#[tauri::command]
pub fn openakita_service_stop(workspace_id: String) -> Result<ServiceStatus, String> {
    let pid_file = state::service_pid_file(&workspace_id);
    let port = process::read_workspace_api_port(&workspace_id);
    let effective_port = port.unwrap_or(18900);

    {
        let mut guard = state::MANAGED_CHILD.lock().unwrap();
        if let Some(mut mp) = guard.take() {
            if mp.workspace_id == workspace_id {
                let _ = process::graceful_stop_pid(mp.pid, port);
                if process::is_pid_running(mp.pid) { let _ = mp.child.kill(); let _ = mp.child.wait(); }
                let _ = fs::remove_file(&pid_file);
                let _ = process::wait_for_port_free(effective_port, 10_000);
                state::remove_heartbeat_file(&workspace_id);
                return Ok(build_service_status(&workspace_id, false, None, pid_file.to_string_lossy().to_string()));
            } else { *guard = Some(mp); }
        }
    }

    let pid = state::read_pid_file(&workspace_id).map(|d| d.pid);
    if let Some(pid) = pid { process::graceful_stop_pid(pid, port).map_err(|e| format!("failed to stop service: {e}"))?; }
    let _ = fs::remove_file(&pid_file);
    state::remove_heartbeat_file(&workspace_id);
    let _ = process::wait_for_port_free(effective_port, 10_000);
    Ok(build_service_status(&workspace_id, false, None, pid_file.to_string_lossy().to_string()))
}

// ============================================================
//  Service log
// ============================================================

#[tauri::command]
pub fn openakita_service_log(workspace_id: String, tail_bytes: Option<u64>) -> Result<ServiceLogChunk, String> {
    let ws_dir = state::workspace_dir(&workspace_id);
    let log_path = ws_dir.join("logs").join("openakita-serve.log");
    let path_str = log_path.to_string_lossy().to_string();
    let tail = tail_bytes.unwrap_or(40_000).min(400_000);

    if !log_path.exists() { return Ok(ServiceLogChunk { path: path_str, content: "".into(), truncated: false }); }

    let mut f = std::fs::File::open(&log_path).map_err(|e| format!("open log failed: {e}"))?;
    let len = f.metadata().map_err(|e| format!("stat log failed: {e}"))?.len();
    let start = len.saturating_sub(tail);
    let truncated = start > 0;
    f.seek(SeekFrom::Start(start)).map_err(|e| format!("seek log failed: {e}"))?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).map_err(|e| format!("read log failed: {e}"))?;
    let content = String::from_utf8_lossy(&buf).to_string();
    Ok(ServiceLogChunk { path: path_str, content, truncated })
}

// ============================================================
//  Check PID alive
// ============================================================

#[tauri::command]
pub fn openakita_check_pid_alive(workspace_id: String) -> Result<bool, String> {
    {
        let mut guard = state::MANAGED_CHILD.lock().unwrap();
        if let Some(ref mut mp) = *guard {
            if mp.workspace_id == workspace_id {
                let alive = mp.child.try_wait().ok().flatten().is_none();
                if !alive { *guard = None; let _ = fs::remove_file(state::service_pid_file(&workspace_id)); state::remove_heartbeat_file(&workspace_id); }
                return Ok(alive);
            }
        }
    }
    if let Some(data) = state::read_pid_file(&workspace_id) {
        if !process::is_pid_running(data.pid) { let _ = fs::remove_file(state::service_pid_file(&workspace_id)); state::remove_heartbeat_file(&workspace_id); return Ok(false); }
        if !process::is_openakita_process(data.pid) { let _ = fs::remove_file(state::service_pid_file(&workspace_id)); state::remove_heartbeat_file(&workspace_id); return Ok(false); }
        if let Some(true) = state::is_heartbeat_stale(&workspace_id, 60) {
            let port = process::read_workspace_api_port(&workspace_id);
            if state::should_cleanup_stale_heartbeat(Some(true), state::is_backend_http_healthy(port)) {
                let _ = process::graceful_stop_pid(data.pid, port);
                let _ = fs::remove_file(state::service_pid_file(&workspace_id));
                state::remove_heartbeat_file(&workspace_id);
                return Ok(false);
            }
        }
        return Ok(true);
    }
    Ok(false)
}

// ============================================================
//  Tray backend status
// ============================================================

#[tauri::command]
pub fn set_tray_backend_status(app: tauri::AppHandle, status: String, im_summary: Option<String>) -> Result<(), String> {
    let base = match status.as_str() {
        "alive" => "OpenAkita - Running", "degraded" => "OpenAkita - Backend Unresponsive",
        "dead" => "OpenAkita - Backend Stopped", _ => "OpenAkita",
    };
    let tooltip = if let Some(ref im) = im_summary { if !im.is_empty() { format!("{}\nIM: {}", base, im) } else { base.to_string() } } else { base.to_string() };
    if let Some(tray) = app.tray_by_id("main_tray") { let _ = tray.set_tooltip(Some(tooltip)); }

    if status == "dead" {
        #[cfg(windows)]
        {
            let mut cmd = Command::new("powershell");
            cmd.args(["-NoProfile", "-NonInteractive", "-Command",
                r#"try { $aumid = 'com.openakita.setupcenter'; $rp = "HKCU:\SOFTWARE\Classes\AppUserModelId\$aumid"; if (!(Test-Path $rp)) { New-Item $rp -Force | Out-Null; Set-ItemProperty $rp -Name DisplayName -Value 'OpenAkita Desktop' }; [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); $t = $xml.GetElementsByTagName('text'); $t[0].AppendChild($xml.CreateTextNode('OpenAkita')) | Out-Null; $t[1].AppendChild($xml.CreateTextNode('Backend service has stopped')) | Out-Null; $n = [Windows.UI.Notifications.ToastNotification]::new($xml); [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($n) } catch {}"#]);
            python_env::apply_no_window(&mut cmd); let _ = cmd.spawn();
        }
        #[cfg(not(windows))]
        { let _ = Command::new("osascript").args(["-e", r#"display notification "Backend service has stopped" with title "OpenAkita""#]).spawn(); }
    }
    Ok(())
}

// ============================================================
//  Auto-start state
// ============================================================

#[tauri::command]
pub fn is_backend_auto_starting() -> bool {
    if state::AUTO_START_IN_PROGRESS.load(Ordering::SeqCst) {
        let started_at = state::AUTO_START_STARTED_AT_MS.load(Ordering::SeqCst);
        if started_at > 0 {
            let elapsed = crate::util::now_ms().saturating_sub(started_at);
            if elapsed >= state::AUTO_START_TIMEOUT_MS {
                state::log_to_file(&format!("[auto-start] is_backend_auto_starting timeout after {}ms, clearing flag", elapsed));
                state::AUTO_START_IN_PROGRESS.store(false, Ordering::SeqCst);
                state::AUTO_START_STARTED_AT_MS.store(0, Ordering::SeqCst);
            } else { return true; }
        } else { return true; }
    }
    let s = state::read_state_file();
    if let Some(ws_id) = s.current_workspace_id {
        if state::backend_in_boot_grace(&ws_id) {
            let port = process::read_workspace_api_port(&ws_id).unwrap_or(18900);
            if !state::is_backend_http_healthy(Some(port)) { return true; }
        }
    }
    false
}

#[tauri::command]
pub fn backend_in_boot_grace_cmd(workspace_id: String) -> bool {
    state::backend_in_boot_grace(&workspace_id)
}


// ============================================================
//  Repair runtime
// ============================================================

#[tauri::command]
pub fn repair_runtime_env() -> Result<String, String> {
    let mut report = String::new();
    for dir in [runtime::app_venv_dir(), runtime::agent_venv_dir()] {
        if dir.exists() {
            match fs::remove_dir_all(&dir) {
                Ok(()) => report.push_str(&format!("removed {}\n", dir.display())),
                Err(e) => report.push_str(&format!("warn: remove {} failed: {}\n", dir.display(), e)),
            }
        }
    }
    let manifest = runtime::runtime_manifest_path();
    if manifest.exists() {
        match fs::remove_file(&manifest) {
            Ok(()) => report.push_str(&format!("removed {}\n", manifest.display())),
            Err(e) => report.push_str(&format!("warn: remove {} failed: {}\n", manifest.display(), e)),
        }
    }
    let app_venv_log = runtime::runtime_logs_dir().join("app-venv.log");
    if app_venv_log.exists() { let _ = fs::remove_file(&app_venv_log); }
    let agent_venv_log = runtime::runtime_logs_dir().join("agent-venv.log");
    if agent_venv_log.exists() { let _ = fs::remove_file(&agent_venv_log); }
    match runtime::ensure_dual_runtime_env() {
        Ok(info) => { report.push_str(&format!("ok: app_python={} agent_python={}\n", info.app_python.display(), info.agent_python.display())); Ok(report) }
        Err(e) => { report.push_str(&format!("ensure_dual_runtime_env failed: {}\n", e)); Err(report) }
    }
}

// ============================================================
//  Settings
// ============================================================

#[tauri::command]
pub fn get_auto_start_backend() -> Result<bool, String> { let s = state::read_state_file(); Ok(s.auto_start_backend.unwrap_or(false)) }

#[tauri::command]
pub fn set_auto_start_backend(enabled: bool) -> Result<(), String> { let mut s = state::read_state_file(); s.auto_start_backend = Some(enabled); state::write_state_file(&s) }

#[tauri::command]
pub fn get_auto_update() -> Result<bool, String> { let s = state::read_state_file(); Ok(s.auto_update.unwrap_or(true)) }

#[tauri::command]
pub fn set_auto_update(enabled: bool) -> Result<(), String> { let mut s = state::read_state_file(); s.auto_update = Some(enabled); state::write_state_file(&s) }

// ============================================================
//  Python bridge helper
// ============================================================

fn make_py_bridge(venv_dir: &str, module: &str, args: &[&str], extra_env: &[(&str, &str)]) -> Result<String, String> {
    python_env::run_python_module_json(venv_dir, module, args, extra_env)
}

// ============================================================
//  openakita_list_skills
// ============================================================

#[tauri::command]
pub async fn openakita_list_skills(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = state::workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["list-skills", "--workspace-dir", &wd_str], &[])
    }).await
}

// ============================================================
//  openakita_list_providers
// ============================================================

#[tauri::command]
pub async fn openakita_list_providers(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || { make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["list-providers"], &[]) }).await
}

// ============================================================
//  openakita_list_models
// ============================================================

#[tauri::command]
pub async fn openakita_list_models(venv_dir: String, api_type: String, base_url: String, provider_slug: Option<String>, api_key: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let mut args = vec!["list-models", "--api-type", api_type.as_str(), "--base-url", base_url.as_str()];
        if let Some(slug) = provider_slug.as_deref() { args.push("--provider-slug"); args.push(slug); }
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &args, &[("SETUPCENTER_API_KEY", api_key.as_str())])
    }).await
}

// ============================================================
//  openakita_version
// ============================================================

#[tauri::command]
pub async fn openakita_version(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let bundled = python_env::bundled_backend_dir();
        let version_file = bundled.join("_internal").join("openakita").join("_bundled_version.txt");
        if version_file.exists() { if let Ok(v) = fs::read_to_string(&version_file) { let v = v.trim().to_string(); if !v.is_empty() { return Ok(v); } } }
        let (py, pythonpath) = python_env::resolve_python(&venv_dir)?;
        let mut c = Command::new(&py);
        python_env::apply_no_window(&mut c); python_env::strip_harmful_python_env(&mut c);
        if let Some(ref pp) = pythonpath { c.env("PYTHONPATH", pp); }
        c.args(["-m", "openakita.setup_center.bridge", "version"]);
        let out = c.output().map_err(|e| format!("failed to run python: {e}"))?;
        if !out.status.success() { return Err(format!("python failed: {}", out.status)); }
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    }).await
}

// ============================================================
//  openakita_health_check_endpoint
// ============================================================

#[tauri::command]
pub async fn openakita_health_check_endpoint(venv_dir: String, api_type: String, base_url: String, api_key: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["health-check-endpoint", "--api-type", api_type.as_str(), "--base-url", base_url.as_str()], &[("SETUPCENTER_API_KEY", api_key.as_str())])
    }).await
}

// ============================================================
//  openakita_health_check_im
// ============================================================

#[tauri::command]
pub async fn openakita_health_check_im(venv_dir: String, workspace_id: String, channel: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = state::workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["health-check-im", "--workspace-dir", &wd_str, "--channel", channel.as_str()], &[])
    }).await
}

// ============================================================
//  openakita_ensure_channel_deps
// ============================================================

#[tauri::command]
pub async fn openakita_ensure_channel_deps(venv_dir: String, channel: String) -> Result<String, String> {
    spawn_blocking_result(move || { make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["ensure-channel-deps", "--channel", channel.as_str()], &[]) }).await
}

// ============================================================
//  openakita_install_skill
// ============================================================

#[tauri::command]
pub async fn openakita_install_skill(venv_dir: String, workspace_id: String, skill_name: String, skill_type: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = state::workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["install-skill", "--workspace-dir", &wd_str, "--skill-name", skill_name.as_str(), "--skill-type", skill_type.as_str()], &[])
    }).await
}

// ============================================================
//  openakita_uninstall_skill
// ============================================================

#[tauri::command]
pub async fn openakita_uninstall_skill(venv_dir: String, workspace_id: String, skill_name: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = state::workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["uninstall-skill", "--workspace-dir", &wd_str, "--skill-name", skill_name.as_str()], &[])
    }).await
}

// ============================================================
//  openakita_list_marketplace
// ============================================================

#[tauri::command]
pub async fn openakita_list_marketplace(venv_dir: String, category: Option<String>) -> Result<String, String> {
    spawn_blocking_result(move || {
        let mut args = vec!["list-marketplace"];
        if let Some(cat) = category.as_deref() { args.push("--category"); args.push(cat); }
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    }).await
}

// ============================================================
//  openakita_get_skill_config
// ============================================================

#[tauri::command]
pub async fn openakita_get_skill_config(venv_dir: String, workspace_id: String, skill_name: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = state::workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["get-skill-config", "--workspace-dir", &wd_str, "--skill-name", skill_name.as_str()], &[])
    }).await
}


// ============================================================
//  Onboarding commands
// ============================================================

#[tauri::command]
pub async fn openakita_wecom_onboard_start(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["wecom-onboard-start", "--workspace-dir", &wd_str], &[]) }).await
}

#[tauri::command]
pub async fn openakita_wecom_onboard_poll(venv_dir: String, workspace_id: String, session_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["wecom-onboard-poll", "--workspace-dir", &wd_str, "--session-id", session_id.as_str()], &[]) }).await
}

#[tauri::command]
pub async fn openakita_feishu_onboard_start(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["feishu-onboard-start", "--workspace-dir", &wd_str], &[]) }).await
}

#[tauri::command]
pub async fn openakita_feishu_onboard_poll(venv_dir: String, workspace_id: String, session_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["feishu-onboard-poll", "--workspace-dir", &wd_str, "--session-id", session_id.as_str()], &[]) }).await
}

#[tauri::command]
pub async fn openakita_feishu_validate(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["feishu-validate", "--workspace-dir", &wd_str], &[]) }).await
}

#[tauri::command]
pub async fn openakita_qqbot_onboard_start(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["qqbot-onboard-start", "--workspace-dir", &wd_str], &[]) }).await
}

#[tauri::command]
pub async fn openakita_qqbot_onboard_poll(venv_dir: String, workspace_id: String, session_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["qqbot-onboard-poll", "--workspace-dir", &wd_str, "--session-id", session_id.as_str()], &[]) }).await
}

#[tauri::command]
pub async fn openakita_qqbot_onboard_create(venv_dir: String, workspace_id: String, session_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["qqbot-onboard-create", "--workspace-dir", &wd_str, "--session-id", session_id.as_str()], &[]) }).await
}

#[tauri::command]
pub async fn openakita_qqbot_onboard_poll_and_create(venv_dir: String, workspace_id: String, session_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["qqbot-onboard-poll-and-create", "--workspace-dir", &wd_str, "--session-id", session_id.as_str()], &[]) }).await
}

#[tauri::command]
pub async fn openakita_qqbot_validate(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["qqbot-validate", "--workspace-dir", &wd_str], &[]) }).await
}

#[tauri::command]
pub async fn openakita_wechat_onboard_start(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["wechat-onboard-start", "--workspace-dir", &wd_str], &[]) }).await
}

#[tauri::command]
pub async fn openakita_wechat_onboard_poll(venv_dir: String, workspace_id: String, session_id: String) -> Result<String, String> {
    spawn_blocking_result(move || { let wd = state::workspace_dir(&workspace_id); let wd_str = wd.to_string_lossy().to_string(); make_py_bridge(&venv_dir, "openakita.setup_center.bridge", &["wechat-onboard-poll", "--workspace-dir", &wd_str, "--session-id", session_id.as_str()], &[]) }).await
}

// ============================================================
//  HTTP utilities
// ============================================================

#[tauri::command]
pub async fn fetch_pypi_versions(package_name: String) -> Result<String, String> {
    let url = format!("https://pypi.org/pypi/{}/json", package_name);
    let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(15)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    let resp = client.get(&url).send().await.map_err(|e| format!("http request failed: {e}"))?;
    resp.text().await.map_err(|e| format!("read response failed: {e}"))
}

#[tauri::command]
pub async fn http_get_json(url: String) -> Result<String, String> {
    let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(15)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    let resp = client.get(&url).send().await.map_err(|e| format!("http request failed: {e}"))?;
    resp.text().await.map_err(|e| format!("read response failed: {e}"))
}

#[tauri::command]
pub async fn http_proxy_request(url: String, method: String, headers: Option<HashMap<String, String>>, body: Option<String>) -> Result<String, String> {
    let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(30)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    let mut req = match method.to_uppercase().as_str() {
        "GET" => client.get(&url), "POST" => client.post(&url), "PUT" => client.put(&url), "DELETE" => client.delete(&url), "PATCH" => client.patch(&url),
        _ => return Err(format!("unsupported http method: {}", method)),
    };
    if let Some(hdrs) = &headers { for (k, v) in hdrs { req = req.header(k.as_str(), v.as_str()); } }
    if let Some(b) = &body { req = req.body(b.clone()); }
    let resp = req.send().await.map_err(|e| format!("http request failed: {e}"))?;
    resp.text().await.map_err(|e| format!("read response failed: {e}"))
}

// ============================================================
//  Backend fetch (streaming) -- with port whitelist
// ============================================================

#[tauri::command]
pub async fn backend_fetch(on_event: tauri::ipc::Channel<BackendFetchEvent>, url: String, method: String, headers: Option<HashMap<String, String>>, body: Option<String>, timeout_secs: Option<u64>) -> Result<serde_json::Value, String> {
    // Port whitelist: only allow ports 18900 and 16185
    let port = url.split("://").nth(1).and_then(|rest| { let hostport = rest.split('/').next()?; if let Some(idx) = hostport.rfind(':') { hostport[idx + 1..].parse::<u16>().ok() } else { None } }).unwrap_or(0);
    if port != 18900 && port != 16185 {
        return Err(format!("port {} is not allowed for backend_fetch; only ports 18900 and 16185 are permitted", port));
    }

    let timeout = std::time::Duration::from_secs(timeout_secs.unwrap_or(120));
    let client = reqwest::Client::builder().timeout(timeout).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    let mut req = match method.to_uppercase().as_str() {
        "GET" => client.get(&url), "POST" => client.post(&url), "PUT" => client.put(&url), "DELETE" => client.delete(&url), "PATCH" => client.patch(&url),
        _ => return Err(format!("unsupported http method: {}", method)),
    };
    if let Some(hdrs) = &headers { for (k, v) in hdrs { req = req.header(k.as_str(), v.as_str()); } }
    if let Some(b) = &body { req = req.body(b.clone()); }

    let mut resp = req.send().await.map_err(|e| { let _ = on_event.send(BackendFetchEvent::Error { message: format!("http request failed: {e}") }); format!("http request failed: {e}") })?;
    let status = resp.status();
    if !status.is_success() { let msg = format!("HTTP {}", status); let _ = on_event.send(BackendFetchEvent::Error { message: msg.clone() }); return Err(msg); }

    let content_type = resp.headers().get("content-type").and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    if content_type.contains("text/event-stream") || content_type.contains("application/x-ndjson") {
        loop {
            match resp.chunk().await {
                Ok(Some(chunk)) => {
                    let text = String::from_utf8_lossy(&chunk).to_string();
                    let _ = on_event.send(BackendFetchEvent::Chunk { text });
                }
                Ok(None) => break,
                Err(e) => {
                    let _ = on_event.send(BackendFetchEvent::Error { message: format!("stream error: {e}") });
                    return Err(format!("stream error: {e}"));
                }
            }
        }
    } else {
        match resp.text().await {
            Ok(text) => { let _ = on_event.send(BackendFetchEvent::Chunk { text }); }
            Err(e) => { let _ = on_event.send(BackendFetchEvent::Error { message: format!("read response failed: {e}") }); return Err(format!("read response failed: {e}")); }
        }
    }
    let _ = on_event.send(BackendFetchEvent::Done);
    Ok(serde_json::json!({"status": "ok"}))
}

// ============================================================
//  File operations
// ============================================================

#[tauri::command]
pub async fn read_file_base64(path: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let bytes = fs::read(&path).map_err(|e| format!("read file failed: {e}"))?;
        use base64::Engine as _;
        Ok(base64::engine::general_purpose::STANDARD.encode(&bytes))
    }).await
}

#[tauri::command]
pub async fn download_file(url: String, filename: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let client = reqwest::blocking::Client::builder().timeout(std::time::Duration::from_secs(300)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
        let resp = client.get(&url).send().map_err(|e| format!("download failed: {e}"))?;
        let bytes = resp.bytes().map_err(|e| format!("read body failed: {e}"))?;
        let dest = crate::util::unique_download_path(&filename)?;
        fs::write(&dest, &bytes).map_err(|e| format!("write file failed: {e}"))?;
        Ok(dest.to_string_lossy().to_string())
    }).await
}

#[tauri::command]
pub fn copy_file_to_downloads(path: String, filename: String) -> Result<String, String> {
    let downloads = dirs_next::download_dir().or_else(|| dirs_next::home_dir().map(|h| h.join("Downloads"))).ok_or_else(|| "cannot determine Downloads directory".to_string())?;
    fs::create_dir_all(&downloads).map_err(|e| format!("cannot create Downloads dir: {e}"))?;
    let safe = crate::util::sanitize_download_filename(&filename);
    let dest = downloads.join(&safe);
    fs::copy(&path, &dest).map_err(|e| format!("copy failed: {e}"))?;
    Ok(dest.to_string_lossy().to_string())
}

#[tauri::command]
pub fn show_item_in_folder(path: String) -> Result<(), String> {
    let p = PathBuf::from(&path);
    #[cfg(target_os = "windows")] { Command::new("explorer").args(["/select,", &p.to_string_lossy()]).spawn().map_err(|e| format!("explorer failed: {e}"))?; }
    #[cfg(target_os = "macos")] { Command::new("open").args(["-R", &p.to_string_lossy()]).spawn().map_err(|e| format!("open failed: {e}"))?; }
    #[cfg(target_os = "linux")] { if let Some(parent) = p.parent() { Command::new("xdg-open").arg(parent).spawn().map_err(|e| format!("xdg-open failed: {e}"))?; } }
    Ok(())
}

#[tauri::command]
pub fn open_file_with_default(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")] { Command::new("cmd").args(["/c", "start", "", &path]).spawn().map_err(|e| format!("start failed: {e}"))?; }
    #[cfg(target_os = "macos")] { Command::new("open").arg(&path).spawn().map_err(|e| format!("open failed: {e}"))?; }
    #[cfg(target_os = "linux")] { Command::new("xdg-open").arg(&path).spawn().map_err(|e| format!("xdg-open failed: {e}"))?; }
    Ok(())
}


// ============================================================
//  Export / diagnostics
// ============================================================

#[tauri::command]
pub fn export_env_backup(workspace_id: String, dest_path: String) -> Result<String, String> {
    let ws_dir = state::workspace_dir(&workspace_id);
    let env_path = ws_dir.join(".env");
    if !env_path.exists() { return Err(".env file not found".into()); }
    fs::copy(&env_path, &dest_path).map_err(|e| format!("copy .env failed: {e}"))?;
    Ok(dest_path)
}

#[tauri::command]
pub fn export_diagnostic_bundle(workspace_id: String, system_info_json: String, dest_path: String) -> Result<String, String> {
    let zip_path = PathBuf::from(&dest_path);
    let file = fs::File::create(&zip_path).map_err(|e| format!("create zip: {e}"))?;
    let mut zw = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);

    let ws_dir = state::workspace_dir(&workspace_id);

    crate::zip_utils::zip_add_dir_capped(&mut zw, &ws_dir.join("identity"), "workspace/identity", options, 2 * 1024 * 1024);
    crate::zip_utils::zip_add_file(&mut zw, &ws_dir.join(".env"), "workspace/.env", options);
    crate::zip_utils::zip_add_file(&mut zw, &state::state_file_path(), "setup/state.json", options);
    crate::zip_utils::zip_add_file(&mut zw, &state::openakita_root_dir().join("root_config.json"), "setup/root_config.json", options);
    crate::zip_utils::zip_add_dir_capped(&mut zw, &state::setup_logs_dir(), "setup/logs", options, 2 * 1024 * 1024);
    crate::zip_utils::zip_add_dir_capped(&mut zw, &ws_dir.join("logs"), "workspace/logs", options, 2 * 1024 * 1024);
    crate::zip_utils::zip_add_file(&mut zw, &runtime::runtime_manifest_path(), "setup/runtime_manifest.json", options);

    if zw.start_file("system_info.json", options).is_ok() { let _ = zw.write_all(system_info_json.as_bytes()); }

    zw.finish().map_err(|e| format!("finalize zip: {e}"))?;
    Ok(dest_path)
}

// ============================================================
//  Feedback
// ============================================================

fn read_feedback_endpoint(workspace_id: &str) -> String {
    let config_path = state::workspace_dir(workspace_id).join("config.yaml");
    let content = match fs::read_to_string(&config_path) { Ok(c) => c, Err(_) => return String::new() };
    for line in content.lines() {
        let trimmed = line.trim();
        if let Some(val) = trimmed.strip_prefix("bug_report_endpoint:") { let v = val.trim().trim_matches('"').trim_matches('\''); if !v.is_empty() { return v.to_string(); } }
    }
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("bug_report_endpoint") { if let Some((_, val)) = trimmed.split_once(':') { let v = val.trim().trim_matches('"').trim_matches('\''); if !v.is_empty() { return v.to_string(); } } }
    }
    String::new()
}

fn pending_feedback_path() -> PathBuf { state::setup_logs_dir().join("pending_feedback.json") }

#[tauri::command]
pub fn build_feedback_zip(workspace_id: String, summary: String, attachments: Vec<String>, dest_path: String) -> Result<String, String> {
    let zip_path = PathBuf::from(&dest_path);
    let file = fs::File::create(&zip_path).map_err(|e| format!("create zip: {e}"))?;
    let mut zw = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);

    let summary_json = serde_json::json!({"workspace_id": workspace_id, "summary": summary, "timestamp": crate::util::now_epoch_secs()});
    if zw.start_file("summary.json", options).is_ok() { let _ = zw.write_all(serde_json::to_string_pretty(&summary_json).unwrap_or_default().as_bytes()); }

    for att in &attachments {
        let att_path = PathBuf::from(att);
        if att_path.exists() && att_path.is_file() {
            if let Some(name) = att_path.file_name().and_then(|n| n.to_str()) {
                if zw.start_file(format!("attachments/{}", name), options).is_ok() { let _ = zw.write_all(&fs::read(&att_path).unwrap_or_default()); }
            }
        }
    }

    let ws_dir = state::workspace_dir(&workspace_id);
    crate::zip_utils::zip_add_file(&mut zw, &ws_dir.join(".env"), "diagnostics/.env", options);
    zw.finish().map_err(|e| format!("finalize zip: {e}"))?;
    Ok(dest_path)
}

#[tauri::command]
pub fn upload_feedback_to_cloud(workspace_id: String, zip_path: String) -> Result<serde_json::Value, String> {
    let endpoint = read_feedback_endpoint(&workspace_id);
    if endpoint.is_empty() { return Ok(serde_json::json!({"status": "skipped", "reason": "no bug_report_endpoint configured"})); }
    let zip_bytes = fs::read(&zip_path).map_err(|e| format!("read zip failed: {e}"))?;
    let client = reqwest::blocking::Client::builder().timeout(std::time::Duration::from_secs(60)).no_proxy().build().map_err(|e| format!("http client error: {e}"))?;
    let resp = client.post(&endpoint)
        .header("Content-Type", "application/zip")
        .body(zip_bytes)
        .send()
        .map_err(|e| format!("upload failed: {e}"))?;
    if resp.status().is_success() { Ok(serde_json::json!({"status": "ok"})) }
    else { let status = resp.status(); let text = resp.text().unwrap_or_default(); Err(format!("upload failed: HTTP {}: {}", status, text)) }
}

#[tauri::command]
pub fn save_pending_feedback(record: serde_json::Value) -> Result<(), String> {
    let path = pending_feedback_path();
    if let Some(parent) = path.parent() { fs::create_dir_all(parent).map_err(|e| format!("create dir failed: {e}"))?; }
    let mut records: Vec<serde_json::Value> = if path.exists() { serde_json::from_str(&fs::read_to_string(&path).unwrap_or_default()).unwrap_or_default() } else { vec![] };
    records.push(record);
    let json = serde_json::to_string_pretty(&records).map_err(|e| format!("serialize failed: {e}"))?;
    fs::write(&path, json).map_err(|e| format!("write failed: {e}"))
}

#[tauri::command]
pub fn get_feedback_config_offline(workspace_id: String) -> serde_json::Value {
    let endpoint = read_feedback_endpoint(&workspace_id);
    let pending = pending_feedback_path();
    let pending_count = if pending.exists() { fs::read_to_string(&pending).ok().and_then(|s| serde_json::from_str::<Vec<serde_json::Value>>(&s).ok()).map(|v: Vec<_>| v.len()).unwrap_or(0) } else { 0 };
    serde_json::json!({"bug_report_endpoint": endpoint, "pending_feedback_count": pending_count})
}

// ============================================================
//  Open URL
// ============================================================

#[tauri::command]
pub fn open_external_url(url: String) -> Result<(), String> {
    #[cfg(target_os = "windows")] { Command::new("cmd").args(["/c", "start", "", &url]).spawn().map_err(|e| format!("start failed: {e}"))?; }
    #[cfg(target_os = "macos")] { Command::new("open").arg(&url).spawn().map_err(|e| format!("open failed: {e}"))?; }
    #[cfg(target_os = "linux")] { Command::new("xdg-open").arg(&url).spawn().map_err(|e| format!("xdg-open failed: {e}"))?; }
    Ok(())
}

// ============================================================
//  Process listing
// ============================================================

#[tauri::command]
pub fn openakita_list_processes() -> Vec<OpenAkitaProcess> {
    let mut out = Vec::new();
    #[cfg(windows)]
    {
        use crate::win_ffi;
        let snap = unsafe { win_ffi::win::CreateToolhelp32Snapshot(win_ffi::win::TH32CS_SNAPPROCESS, 0) };
        if snap == win_ffi::win::INVALID_HANDLE_VALUE || snap.is_null() { return out; }
        let mut pe: win_ffi::win::PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        pe.dw_size = std::mem::size_of::<win_ffi::win::PROCESSENTRY32W>() as u32;
        let mut python_pids: Vec<(u32, u32)> = Vec::new();
        if unsafe { win_ffi::win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                let name = String::from_utf16_lossy(&pe.sz_exe_file[..pe.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(260)]);
                if name.to_ascii_lowercase().contains("python") { python_pids.push((pe.th32_process_id, pe.th32_parent_process_id)); }
                if unsafe { win_ffi::win::Process32NextW(snap, &mut pe) } == 0 { break; }
            }
        }
        unsafe { win_ffi::win::CloseHandle(snap); }
        let mut matched: Vec<(u32, u32, String)> = Vec::new();
        for (ppid, parent_pid) in python_pids {
            let mut c = Command::new("powershell");
            c.args(["-NoProfile", "-NonInteractive", "-Command", &format!("(Get-CimInstance Win32_Process -Filter 'ProcessId={}').CommandLine", ppid)]);
            python_env::apply_no_window(&mut c);
            if let Ok(cmd_out) = c.output() {
                let s = String::from_utf8_lossy(&cmd_out.stdout).to_string();
                let s_lower = s.to_lowercase();
                if s_lower.contains("openakita.main") && (s_lower.contains(" serve") || s_lower.ends_with("serve")) {
                    if process::is_pid_running(ppid) { matched.push((ppid, parent_pid, s.trim().to_string())); }
                }
            }
        }
        for (pid, _parent, cmd) in &matched {
            let has_matched_child = matched.iter().any(|(_, parent, _)| parent == pid);
            if !has_matched_child { out.push(OpenAkitaProcess { pid: *pid, cmd: cmd.clone() }); }
        }
    }
    #[cfg(not(windows))]
    {
        if let Ok(ps_out) = Command::new("sh").args(["-c", r"ps aux | grep '[o]penakita\.main.*serve'"]).output() {
            let stdout = String::from_utf8_lossy(&ps_out.stdout);
            for line in stdout.lines() {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 { if let Ok(pid) = parts[1].parse::<u32>() { if process::is_pid_running(pid) { out.push(OpenAkitaProcess { pid, cmd: parts[10..].join(" ") }); } } }
            }
        }
    }
    out
}

#[tauri::command]
pub fn openakita_stop_all_processes() -> Vec<u32> {
    let mut stopped = Vec::new();
    let entries = process::list_service_pids();
    for ent in &entries { if process::is_pid_running(ent.pid) { let port = process::read_workspace_api_port(&ent.workspace_id); let _ = process::stop_service_pid_entry(ent, port); stopped.push(ent.pid); } }
    let orphans = process::kill_openakita_orphans();
    for pid in orphans { if !stopped.contains(&pid) { stopped.push(pid); } }
    stopped
}

// ============================================================
//  First run
// ============================================================

#[tauri::command]
pub fn is_first_run() -> bool { let s = state::read_state_file(); s.workspaces.is_empty() }

// ============================================================
//  Environment check
// ============================================================

#[tauri::command]
pub fn check_environment() -> EnvironmentCheck {
    let root = state::openakita_root_dir();
    let has_old_venv = root.join("venv").exists() && root.join("venv").read_dir().map(|mut d| d.next().is_some()).unwrap_or(false);
    let has_old_runtime = root.join("runtime").exists() && root.join("runtime").read_dir().map(|mut d| d.next().is_some()).unwrap_or(false);
    let has_old_workspaces = root.join("workspaces").exists() && root.join("workspaces").read_dir().map(|mut d| d.next().is_some()).unwrap_or(false);

    let state_file = state::read_state_file();
    let old_version = state_file.last_installed_version.clone();
    let current_version = env!("CARGO_PKG_VERSION").to_string();

    let mut running = Vec::new();
    if let Ok(entries) = fs::read_dir(state::run_dir()) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("pid") {
                let ws_id = path.file_stem().and_then(|s| s.to_str()).and_then(|s| s.strip_prefix("openakita-")).unwrap_or("unknown");
                if let Ok(content) = fs::read_to_string(&path) {
                    if let Ok(data) = serde_json::from_str::<state::PidFileData>(&content) {
                        if process::is_pid_running(data.pid) { running.push(format!("PID {} (workspace: {})", data.pid, ws_id)); }
                    }
                }
            }
        }
    }

    let disk_usage_mb = util::dir_size_bytes(&root) / (1024 * 1024);
    let mut conflicts = Vec::new();
    if !running.is_empty() { conflicts.push(format!("detected {} running OpenAkita process(es)", running.len())); }

    EnvironmentCheck { openakita_root: root.to_string_lossy().to_string(), has_old_venv, has_old_runtime, has_old_workspaces, old_version, current_version, running_processes: running, disk_usage_mb, conflicts }
}

#[tauri::command]
pub fn check_backend_availability(venv_dir: String) -> BackendAvailability {
    let bundled_dir = python_env::bundled_backend_dir();
    let bundled_exe = if cfg!(windows) { bundled_dir.join("openakita-server.exe") } else { bundled_dir.join("openakita-server") };
    let venv_py = python_env::venv_pythonw_path(&venv_dir);
    let bundled = bundled_exe.exists();
    let venv_ready = venv_py.exists();
    let exe_path = if bundled { bundled_exe.to_string_lossy().to_string() } else if venv_ready { venv_py.to_string_lossy().to_string() } else { String::new() };
    BackendAvailability { bundled, venv_ready, exe_path, bundled_checked: bundled_exe.to_string_lossy().to_string(), venv_checked: venv_py.to_string_lossy().to_string() }
}

// ============================================================
//  Cleanup / factory reset
// ============================================================

#[tauri::command]
pub fn cleanup_old_environment(clean_venv: bool, clean_runtime: bool) -> Result<String, String> {
    let root = state::openakita_root_dir();
    let mut cleaned = Vec::new();
    let mut warnings = Vec::new();

    if clean_venv {
        let venv_path = root.join("venv");
        if venv_path.exists() {
            let modules_base = root.join("modules");
            let has_installed_modules = modules_base.exists() && modules_base.read_dir().map(|mut d| d.any(|e| e.map(|e| e.path().is_dir()).unwrap_or(false))).unwrap_or(false);
            if has_installed_modules { warnings.push("note: after cleaning venv, installed modules (vector-memory etc.) may need reinstallation".to_string()); }
            workspace::force_remove_dir(&venv_path).map_err(|e| format!("clean venv failed: {e}"))?;
            cleaned.push("venv");
        }
    }
    if clean_runtime {
        let runtime_path = root.join("runtime");
        if runtime_path.exists() { workspace::force_remove_dir(&runtime_path).map_err(|e| format!("clean runtime failed: {e}"))?; cleaned.push("runtime"); }
    }

    if cleaned.is_empty() { Ok("nothing to clean".to_string()) }
    else { let mut msg = format!("cleaned: {}", cleaned.join(", ")); if !warnings.is_empty() { msg.push_str(&format!(" ({})", warnings.join("; "))); } Ok(msg) }
}

#[tauri::command]
pub fn factory_reset() -> Result<String, String> {
    let stopped = openakita_stop_all_processes();

    let root = state::openakita_root_dir();
    let dirs_to_remove = ["workspaces", "venv", "runtime", "run", "logs", "modules", "bin", "data"];
    let files_to_remove = ["state.json", "cli.json"];

    let mut removed = Vec::new();
    let mut errors = Vec::new();

    for name in &dirs_to_remove {
        let p = root.join(name);
        if p.exists() { match workspace::force_remove_dir(&p) { Ok(()) => removed.push(name.to_string()), Err(e) => errors.push(format!("{}: {}", name, e)), } }
    }
    for name in &files_to_remove {
        let p = root.join(name);
        if p.exists() { match fs::remove_file(&p) { Ok(()) => removed.push(name.to_string()), Err(e) => errors.push(format!("{}: {}", name, e)), } }
    }

    if !errors.is_empty() { return Err(format!("partial reset failed: {}{}", errors.join("; "), if !removed.is_empty() { format!(" (cleaned: {})", removed.join(", ")) } else { String::new() })); }

    let mut msg = if removed.is_empty() { "nothing to clean (already factory state)".to_string() } else { format!("cleaned: {}", removed.join(", ")) };
    if !stopped.is_empty() { msg.push_str(&format!(" (stopped {} process(es))", stopped.len())); }
    Ok(msg)
}

// ============================================================
//  Logging
// ============================================================

#[tauri::command]
pub fn start_onboarding_log(date_label: String) -> Result<String, String> {
    let log_dir = state::setup_logs_dir();
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let safe_label = date_label.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect::<String>();
    let name = if safe_label.is_empty() { format!("onboarding-{}.log", crate::util::now_epoch_secs()) } else { format!("onboarding-{}.log", safe_label) };
    let path = log_dir.join(&name);
    let mut f = std::fs::OpenOptions::new().create(true).truncate(true).write(true).open(&path).map_err(|e| format!("open onboarding log failed: {e}"))?;
    let header = format!("OpenAkita onboarding log started at {}\n", date_label);
    f.write_all(header.as_bytes()).map_err(|e| format!("write onboarding log header failed: {e}"))?;
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

#[tauri::command]
pub fn append_onboarding_log(log_path: String, line: String) -> Result<(), String> {
    let path = PathBuf::from(&log_path);
    if !path.exists() { return Ok(()); }
    let mut f = std::fs::OpenOptions::new().append(true).open(&path).map_err(|e| format!("append onboarding log failed: {e}"))?;
    writeln!(f, "{}", line).map_err(|e| format!("write line failed: {e}"))?;
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn append_onboarding_log_lines(log_path: String, lines: Vec<String>) -> Result<(), String> {
    let path = PathBuf::from(&log_path);
    if !path.exists() || lines.is_empty() { return Ok(()); }
    let mut f = std::fs::OpenOptions::new().append(true).open(&path).map_err(|e| format!("append onboarding log failed: {e}"))?;
    for line in &lines { writeln!(f, "{}", line).map_err(|e| format!("write line failed: {e}"))?; }
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn append_frontend_log(lines: Vec<String>) -> Result<(), String> {
    if lines.is_empty() { return Ok(()); }
    let log_dir = state::setup_logs_dir();
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let path = frontend_log_path();
    maybe_rotate_frontend_log(&path);
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path).map_err(|e| format!("open frontend log failed: {e}"))?;
    for line in &lines { writeln!(f, "{}", line).map_err(|e| format!("write line failed: {e}"))?; }
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn save_log_export(filename: String, content: String) -> Result<String, String> {
    let downloads = dirs_next::download_dir().or_else(dirs_next::desktop_dir).unwrap_or_else(|| state::openakita_root_dir().join("logs"));
    fs::create_dir_all(&downloads).ok();
    let path = downloads.join(&filename);
    fs::write(&path, content.as_bytes()).map_err(|e| format!("save log export failed: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

// ============================================================
//  CLI registration
// ============================================================

#[tauri::command]
pub fn register_cli(commands: Vec<String>, add_to_path: bool) -> Result<String, String> {
    cli::register_cli_impl(commands, add_to_path)
}

#[tauri::command]
pub fn unregister_cli() -> Result<String, String> {
    cli::unregister_cli_impl()
}

#[tauri::command]
pub fn get_cli_status() -> Result<cli::CliStatus, String> {
    cli::get_cli_status_impl()
}
