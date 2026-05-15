use std::fs;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use tauri::Emitter;

use crate::process;
use crate::state;

/// Shutdown signal for heartbeat thread
pub static HEARTBEAT_SHUTDOWN: AtomicBool = AtomicBool::new(false);

/// Startup version check result
pub enum VersionCheckResult {
    NotRunning,
    RunningOk,
    Upgraded,
}

pub fn stop_backend_for_restart(pid: u32, port: u16) -> VersionCheckResult {
    if let Err(e) = process::graceful_stop_pid(pid, Some(port)) {
        eprintln!("Failed to stop old backend (pid={pid}): {e}. Keeping current backend.");
        return VersionCheckResult::RunningOk;
    }
    for ent in process::list_service_pids() {
        if let Some(data) = state::read_pid_file(&ent.workspace_id) {
            if data.pid == pid || !process::is_pid_running(data.pid) {
                let _ = fs::remove_file(state::service_pid_file(&ent.workspace_id));
                state::remove_heartbeat_file(&ent.workspace_id);
            }
        }
    }
    eprintln!("Old backend (pid={pid}) stopped. New backend will be started automatically.");
    VersionCheckResult::Upgraded
}

pub fn startup_version_check(app_version: &str, port: u16) -> VersionCheckResult {
    let client = &*state::BLOCKING_HTTP_CLIENT;
    let url = format!("http://127.0.0.1:{port}/api/health");

    // Retry up to 5 times with 1.5s backoff for slow cold starts
    // (dual-venv + 122 skills + IM channels can take 20-30s)
    for attempt in 0..5u32 {
        if attempt > 0 {
            std::thread::sleep(std::time::Duration::from_millis(1500 * attempt as u64));
        }
        let resp = match client
            .get(&url)
            .timeout(std::time::Duration::from_secs(4))
            .send()
        {
            Ok(r) if r.status().is_success() => r,
            Ok(r) => {
                state::log_to_file(&format!(
                    "[version_check] health check non-success: {}",
                    r.status()
                ));
                if attempt < 4 { continue; }
                return VersionCheckResult::NotRunning;
            }
            Err(e) => {
                if attempt < 4 { continue; }
                state::log_to_file(&format!("[version_check] health check failed after 3 retries: {e}"));
                return VersionCheckResult::NotRunning;
            }
        };

        let json: serde_json::Value = match resp.json() {
            Ok(v) => v,
            Err(_) => return VersionCheckResult::RunningOk,
        };

        let backend_version = json
            .get("version")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim_start_matches('v');
        let desktop_version = app_version.trim_start_matches('v');

        if backend_version.is_empty() || backend_version == "0.0.0-dev" {
            return VersionCheckResult::RunningOk;
        }

        if backend_version == desktop_version {
            if crate::runtime::runtime_wheel_hash_matches_bootstrap() {
                return VersionCheckResult::RunningOk;
            }
            let pid = match json.get("pid").and_then(|v| v.as_u64()).map(|p| p as u32) {
                Some(p) => p,
                None => {
                    eprintln!("Runtime wheel changed but backend PID is unavailable; keeping current backend.");
                    return VersionCheckResult::RunningOk;
                }
            };
            eprintln!(
                "Runtime wheel changed for version {desktop_version}. Stopping backend to refresh app-venv..."
            );
            return stop_backend_for_restart(pid, port);
        }

        let bundled_v = crate::python_env::bundled_backend_version()
            .unwrap_or_default()
            .trim_start_matches('v')
            .to_string();
        if !bundled_v.is_empty() && bundled_v == backend_version {
            eprintln!(
                "Version mismatch: backend={backend_version} desktop={desktop_version}, but bundled backend is also {bundled_v}. \
                 Restart would not help — keeping current backend.",
            );
            return VersionCheckResult::RunningOk;
        }

        eprintln!(
            "Version mismatch: running={backend_version} bundled={} desktop={desktop_version}. Stopping old backend for upgrade...",
            if bundled_v.is_empty() { "?" } else { &bundled_v },
        );

        let pid = match json.get("pid").and_then(|v| v.as_u64()).map(|p| p as u32) {
            Some(p) => p,
            None => {
                eprintln!("Cannot determine backend PID from health response; keeping current backend.");
                return VersionCheckResult::RunningOk;
            }
        };

        return stop_backend_for_restart(pid, port);
    }

    VersionCheckResult::NotRunning
}

pub fn startup_reconcile() {
    let dir = state::run_dir();
    if !dir.exists() {
        return;
    }
    // Clean stale lock files
    if let Ok(rd) = fs::read_dir(&dir) {
        for e in rd.flatten() {
            let p = e.path();
            if let Some(ext) = p.extension() {
                if ext == "lock" {
                    let _ = fs::remove_file(&p);
                }
            }
        }
    }
    // Clean stale PID files
    let entries = process::list_service_pids();
    for ent in &entries {
        if let Some(data) = state::read_pid_file(&ent.workspace_id) {
            if !process::is_pid_file_valid(&data) {
                let _ = fs::remove_file(state::service_pid_file(&ent.workspace_id));
                state::remove_heartbeat_file(&ent.workspace_id);
            } else if let Some(true) = state::is_heartbeat_stale(&ent.workspace_id, 60) {
                let port = process::read_workspace_api_port(&ent.workspace_id);
                if state::should_cleanup_stale_heartbeat(
                    Some(true),
                    state::is_backend_http_healthy(port),
                ) {
                    let _ = process::graceful_stop_pid(data.pid, port);
                    let _ = fs::remove_file(state::service_pid_file(&ent.workspace_id));
                    state::remove_heartbeat_file(&ent.workspace_id);
                }
            }
        }
    }
}

/// Start the background heartbeat monitoring thread.
/// Uses `shutdown` signal for clean termination.
pub fn start_heartbeat_loop(app_handle: tauri::AppHandle, app_version: String) {
    let shutdown = Arc::new(AtomicBool::new(false));
    let hb_shutdown = shutdown.clone();

    // Register exit signal writer
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(std::time::Duration::from_millis(500));
            if HEARTBEAT_SHUTDOWN.load(Ordering::Acquire) {
                hb_shutdown.store(true, Ordering::Release);
                break;
            }
        }
    });

    std::thread::spawn(move || {
        let mut consecutive_failures: u32 = 0;
        let mut last_status_was_healthy: Option<bool> = None;
        let mut last_starting_log_at: u64 = 0;
        let mut state_cache: (Option<u64>, Option<state::AppStateFile>) = (None, None);

        loop {
            if shutdown.load(Ordering::Acquire) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_secs(5));
            if shutdown.load(Ordering::Acquire) {
                break;
            }

            let state_snap = state::read_state_file_cached(&mut state_cache);
            let ws_id = match state_snap.current_workspace_id {
                Some(s) => s,
                None => continue,
            };
            let port = process::read_workspace_api_port(&ws_id).unwrap_or(18900);
            let healthy = state::is_backend_http_healthy(Some(port));
            if healthy {
                consecutive_failures = 0;
                if last_status_was_healthy != Some(true) {
                    let _ = app_handle.emit(
                        "backend:status",
                        serde_json::json!({"healthy": true, "port": port}),
                    );
                    if last_status_was_healthy == Some(false) {
                        let _ = app_handle.emit(
                            "backend:back",
                            serde_json::json!({"port": port}),
                        );
                    }
                    last_status_was_healthy = Some(true);
                }
                continue;
            }

            if state::backend_in_boot_grace(&ws_id) {
                let now = crate::util::now_epoch_secs();
                if now.saturating_sub(last_starting_log_at) >= 30 {
                    state::log_to_file(&format!(
                        "[heartbeat] backend in boot-grace (port={port}) — skipping down/spawn",
                    ));
                    let _ = app_handle.emit(
                        "backend:status",
                        serde_json::json!({"healthy": false, "starting": true, "port": port}),
                    );
                    last_starting_log_at = now;
                }
                consecutive_failures = 0;
                continue;
            }

            consecutive_failures = consecutive_failures.saturating_add(1);
            if consecutive_failures < 3 {
                continue;
            }
            if last_status_was_healthy != Some(false) {
                let _ = app_handle.emit(
                    "backend:lost",
                    serde_json::json!({
                        "port": port,
                        "consecutive_failures": consecutive_failures,
                    }),
                );
                state::log_to_file(&format!(
                    "[heartbeat] backend down for {}s, attempting auto spawn (port={port})",
                    consecutive_failures * 5,
                ));
                last_status_was_healthy = Some(false);
            }
            if state::AUTO_START_IN_PROGRESS.load(Ordering::Acquire) {
                continue;
            }
            let check_result = startup_version_check(&app_version, port);
            if matches!(check_result, VersionCheckResult::RunningOk) {
                consecutive_failures = 0;
                continue;
            }
            state::AUTO_START_IN_PROGRESS.store(true, Ordering::Release);
            state::AUTO_START_STARTED_AT_MS.store(crate::util::now_ms(), Ordering::Release);
            let venv_dir = state::openakita_root_dir()
                .join("venv")
                .to_string_lossy()
                .to_string();
            let ws_clone = ws_id.clone();
            match crate::commands::openakita_service_start_impl(venv_dir, ws_clone) {
                Ok(status) => state::log_to_file(&format!(
                    "[heartbeat] auto-spawn returned: running={}, pid={:?}",
                    status.running, status.pid
                )),
                Err(e) => state::log_to_file(&format!("[heartbeat] auto-spawn FAILED: {e}")),
            }
            state::AUTO_START_IN_PROGRESS.store(false, Ordering::Release);
            state::AUTO_START_STARTED_AT_MS.store(0, Ordering::Release);
            consecutive_failures = 0;
        }
    });
}

pub fn signal_heartbeat_shutdown() {
    HEARTBEAT_SHUTDOWN.store(true, Ordering::Release);
}
