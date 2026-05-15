#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

mod cli;
mod commands;
mod error;
mod heartbeat;
mod migrations;
mod process;
mod python_env;
mod runtime;
mod state;
mod util;
mod win_ffi;
mod workspace;
mod zip_utils;

use dirs_next::home_dir;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::Ordering;
use tauri::Emitter;
use tauri::Manager;
#[cfg(desktop)]
use tauri_plugin_autostart::MacosLauncher;
#[cfg(desktop)]
use tauri_plugin_autostart::ManagerExt as AutostartManagerExt;

/// 进程级自愈：crash 重启 marker 文件路径
fn restart_marker_path() -> PathBuf {
    let base = home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita");
    let _ = fs::create_dir_all(&base);
    base.join("restart.marker")
}

/// 防止自愈进入无限重启循环
fn try_self_heal_relaunch(panic_msg: &str) {
    use std::time::SystemTime;

    let ts = SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let last_ws = state::read_state_file().current_workspace_id.unwrap_or_default();

    if let Ok(prev) = fs::read_to_string(restart_marker_path()) {
        if let Ok(prev_json) = serde_json::from_str::<serde_json::Value>(&prev) {
            if let Some(prev_ts) = prev_json.get("ts").and_then(|v| v.as_u64()) {
                if ts.saturating_sub(prev_ts) < state::SELF_HEAL_COOLDOWN_MS / 1000 {
                    state::log_to_file(&format!(
                        "[self-heal] skip relaunch: last self-heal {}s ago < cooldown",
                        ts.saturating_sub(prev_ts)
                    ));
                    return;
                }
            }
        }
    }
    let marker = serde_json::json!({
        "ts": ts,
        "panic_brief": panic_msg.chars().take(200).collect::<String>(),
        "last_workspace_id": last_ws,
        "reason": "tao_destroyed_panic",
    });
    let _ = fs::write(
        restart_marker_path(),
        serde_json::to_string_pretty(&marker).unwrap_or_else(|_| "{}".into()),
    );

    if let Ok(exe) = std::env::current_exe() {
        let mut cmd = Command::new(&exe);
        cmd.arg("--auto-restarted");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt as _;
            const DETACHED_PROCESS: u32 = 0x00000008;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
            cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
        }
        match cmd.spawn() {
            Ok(_) => state::log_to_file(&format!(
                "[self-heal] relaunched {} after tao panic",
                exe.display()
            )),
            Err(e) => state::log_to_file(&format!("[self-heal] relaunch FAILED: {e}")),
        }
    }
}

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let open_status = MenuItem::with_id(app, "open_status", "打开状态面板", true, None::<&str>)?;
    let open_web = MenuItem::with_id(app, "open_web", "打开网页版", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "隐藏窗口", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出（Quit）", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&open_status, &open_web, &show, &hide, &quit])?;

    TrayIconBuilder::with_id("main_tray")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("OpenAkita")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app: &tauri::AppHandle, event| match event.id.as_ref() {
            "quit" => {
                {
                    let mut guard = state::MANAGED_CHILD.lock().unwrap();
                    if let Some(mut mp) = guard.take() {
                        let port = process::read_workspace_api_port(&mp.workspace_id);
                        let _ = process::graceful_stop_pid(mp.pid, port);
                        if process::is_pid_running(mp.pid) {
                            let _ = mp.child.kill();
                            let _ = mp.child.wait();
                        }
                        let _ = fs::remove_file(state::service_pid_file(&mp.workspace_id));
                    }
                }
                let entries = process::list_service_pids();
                for ent in &entries {
                    if ent.started_by == "external" {
                        continue;
                    }
                    let port = process::read_workspace_api_port(&ent.workspace_id);
                    let _ = process::stop_service_pid_entry(ent, port);
                }
                process::kill_openakita_orphans();
                std::thread::sleep(std::time::Duration::from_millis(600));

                let still_pid = process::list_service_pids()
                    .into_iter()
                    .filter(|x| x.started_by != "external" && process::is_pid_running(x.pid))
                    .collect::<Vec<_>>();
                let still_orphans = process::kill_openakita_orphans();

                if still_pid.is_empty() && still_orphans.is_empty() {
                    app.exit(0);
                } else {
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.show();
                        let _ = w.unminimize();
                        let _ = w.set_focus();
                    }
                    let mut detail = Vec::new();
                    for x in &still_pid {
                        detail.push(format!("{} (PID={})", x.workspace_id, x.pid));
                    }
                    for p in &still_orphans {
                        detail.push(format!("orphan PID={p}"));
                    }
                    let msg = format!(
                        "退出失败：后台服务仍在运行。\n\n请先在\"状态面板\"点击\"停止服务\"，确认状态变为\"未运行\"后再退出。\n\n仍在运行的进程：{}",
                        detail.join("; ")
                    );
                    let _ = app.emit("open_status", serde_json::json!({}));
                    let _ = app.emit("quit_failed", serde_json::json!({ "message": msg }));
                }
            }
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "hide" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            "open_web" => {
                let state = state::read_state_file();
                let ws_id = state.current_workspace_id.unwrap_or_else(|| "default".into());
                let port = process::read_workspace_api_port(&ws_id).unwrap_or(18900);
                let url = format!("http://127.0.0.1:{port}/web");
                #[cfg(target_os = "windows")]
                { let _ = std::process::Command::new("cmd").args(["/c", "start", &url]).spawn(); }
                #[cfg(target_os = "macos")]
                { let _ = std::process::Command::new("open").arg(&url).spawn(); }
                #[cfg(target_os = "linux")]
                { let _ = std::process::Command::new("xdg-open").arg(&url).spawn(); }
            }
            "open_status" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
                let _ = app.emit("open_status", serde_json::json!({}));
            }
            _ => {}
        })
        .on_tray_icon_event(move |tray: &tauri::tray::TrayIcon, event| match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } => {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                    let _ = w.set_focus();
                }
                let _ = app.emit("open_status", serde_json::json!({}));
            }
            TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } => {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                    let _ = w.set_focus();
                }
                let _ = app.emit("open_status", serde_json::json!({}));
            }
            _ => {}
        })
        .build(app)?;

    Ok(())
}

fn main() {
    if std::env::args().any(|a| a == "--auto-restarted") {
        std::thread::sleep(std::time::Duration::from_millis(1500));
    }

    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let msg = format!("PANIC: {info}");
        eprintln!("{msg}");
        state::write_crash_log(&msg, true);
        let panic_str = info.to_string();
        if panic_str.contains("cannot move state from Destroyed")
            || panic_str.contains("tao") && panic_str.contains("Destroyed")
        {
            try_self_heal_relaunch(&panic_str);
        }
        default_hook(info);
    }));

    // Ensure localhost is excluded from proxy
    {
        const LOCALS: &str = "localhost,127.0.0.1";
        for key in ["NO_PROXY", "no_proxy"] {
            let cur = std::env::var(key).unwrap_or_default();
            if !cur.contains("127.0.0.1") {
                let val = if cur.is_empty() {
                    LOCALS.to_string()
                } else {
                    format!("{cur},{LOCALS}")
                };
                std::env::set_var(key, &val);
            }
        }
    }

    // Workaround: NVIDIA drivers blank WebKitGTK window on Linux
    #[cfg(target_os = "linux")]
    {
        if std::env::var("WEBKIT_DISABLE_DMABUF_RENDERER").is_err() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
    }

    let app = match tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--background"]),
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let result: Result<(), Box<dyn std::error::Error>> = (|| {
            let args: Vec<String> = std::env::args().collect();
            if let Some(pos) = args.iter().position(|a| a == "--clean-env") {
                let mut clean_venv = false;
                let mut clean_runtime = false;
                for a in args.iter().skip(pos + 1) {
                    if a == "venv" { clean_venv = true; }
                    if a == "runtime" { clean_runtime = true; }
                    if a.starts_with("--") { break; }
                }
                if clean_venv || clean_runtime {
                    match commands::cleanup_old_environment(clean_venv, clean_runtime) {
                        Ok(msg) => eprintln!("Clean env: {}", msg),
                        Err(e) => eprintln!("Clean env failed: {}", e),
                    }
                    std::process::exit(0);
                }
            }

            heartbeat::startup_reconcile();

            let root = state::openakita_root_dir();
            let state_path = state::state_file_path();
            if let Err(e) = migrations::run_migrations(&state_path, &root) {
                eprintln!("Config migration error: {e}");
            }

            setup_tray(app)?;

            // Auto-start self-repair
            #[cfg(desktop)]
            {
                let repair_state = state::read_state_file();
                if repair_state.auto_start_backend.unwrap_or(false) {
                    let mgr = app.autolaunch();
                    match mgr.is_enabled() {
                        Ok(false) => {
                            eprintln!("Auto-start self-repair: registry entry missing, re-enabling...");
                            if let Err(e) = mgr.enable() {
                                eprintln!("Auto-start self-repair failed: {e}");
                            }
                        }
                        Err(e) => eprintln!("Auto-start check failed: {e}"),
                        _ => {}
                    }
                }
            }

            let is_first_run_arg = std::env::args().any(|a| a == "--first-run");
            let launch_mode = if is_first_run_arg { "first-run" } else { "normal" };
            app.emit("app-launch-mode", launch_mode).ok();

            // Self-heal recovery
            let marker_path = restart_marker_path();
            if marker_path.exists() {
                if let Ok(content) = fs::read_to_string(&marker_path) {
                    state::log_to_file(&format!(
                        "[self-heal] restart.marker recovered: {}",
                        content.lines().next().unwrap_or("")
                    ));
                    let payload: serde_json::Value =
                        serde_json::from_str(&content).unwrap_or(serde_json::json!({}));
                    app.emit("app-restarted-from-crash", payload).ok();
                }
                let _ = fs::remove_file(&marker_path);
            }

            let is_background = std::env::args().any(|a| a == "--background");
            if is_background {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }

            // Auto-start backend
            let app_version = app.package_info().version.to_string();
            let is_auto_restarted = std::env::args().any(|a| a == "--auto-restarted");
            let state = state::read_state_file();
            if let Some(ref ws_id) = state.current_workspace_id {
                let port = process::read_workspace_api_port(ws_id).unwrap_or(18900);
                // If this is a self-heal restart, do NOT run startup_version_check.
                // The old backend may still be initializing and the check would
                // spuriously fail, causing a new spawn that kills the old backend.
                // Instead, just adopt the existing PID or let the heartbeat resume.
                let need_start = if is_auto_restarted {
                    let existing_pid = state::read_pid_file(ws_id).and_then(|d| {
                        if process::is_pid_running(d.pid) { Some(d.pid) } else { None }
                    });
                    if let Some(pid) = existing_pid {
                        state::log_to_file(&format!(
                            "[auto-start] self-heal restart: adopting existing pid={}",
                            pid
                        ));
                        false
                    } else if let Some(pid) = process::healthy_backend_pid(port) {
                        state::log_to_file(&format!(
                            "[auto-start] self-heal restart: found healthy backend pid={}",
                            pid
                        ));
                        let _ = state::write_pid_file(ws_id, pid, "external");
                        false
                    } else {
                        state::log_to_file(
                            "[auto-start] self-heal restart: no backend found, starting new one",
                        );
                        true
                    }
                } else {
                    let check_result = heartbeat::startup_version_check(&app_version, port);
                    !matches!(check_result, heartbeat::VersionCheckResult::RunningOk)
                };
                state::log_to_file(&format!(
                    "[auto-start] app_version={}, ws_id={}, port={}, need_start={}",
                    app_version, ws_id, port, need_start
                ));
                if need_start {
                    state::AUTO_START_IN_PROGRESS.store(true, Ordering::Release);
                    state::AUTO_START_STARTED_AT_MS.store(util::now_ms(), Ordering::Release);
                    let venv_dir = state::openakita_root_dir().join("venv").to_string_lossy().to_string();
                    let ws_clone = ws_id.clone();
                    std::thread::spawn(move || {
                        match commands::openakita_service_start_impl(venv_dir.clone(), ws_clone.clone()) {
                            Ok(status) => {
                                state::log_to_file(&format!(
                                    "[auto-start] success: running={}, pid={:?}",
                                    status.running, status.pid
                                ));
                            }
                            Err(e) => {
                                state::log_to_file(&format!("[auto-start] FAILED: {}", e));
                            }
                        }
                        state::AUTO_START_IN_PROGRESS.store(false, Ordering::Release);
                        state::AUTO_START_STARTED_AT_MS.store(0, Ordering::Release);
                    });
                } else if let Some(pid) = process::healthy_backend_pid(port) {
                    let should_adopt = state::read_pid_file(ws_id)
                        .map(|data| !process::is_pid_file_valid(&data) || data.pid != pid)
                        .unwrap_or(true);
                    if should_adopt {
                        match state::write_pid_file(ws_id, pid, "external") {
                            Ok(()) => state::log_to_file(&format!(
                                "[auto-start] adopted healthy backend pid={pid} for ws={ws_id}"
                            )),
                            Err(e) => state::log_to_file(&format!(
                                "[auto-start] failed to adopt healthy backend pid={pid}: {e}"
                            )),
                        }
                    }
                }
            } else {
                state::log_to_file("[auto-start] skipped: no current_workspace_id in state");
            }

            // Start heartbeat monitoring thread
            {
                let app_handle = app.handle().clone();
                let app_version_for_hb = app_version.clone();
                heartbeat::start_heartbeat_loop(app_handle, app_version_for_hb);
            }

            Ok(())
            })();

            if let Err(ref e) = result {
                state::write_crash_log(&format!("Setup failed: {e}"), false);
            }
            result
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.hide();
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_platform_info,
            commands::toggle_pet_window,
            commands::get_root_dir_info,
            commands::set_custom_root_dir,
            commands::preflight_migrate_root,
            commands::list_workspaces,
            commands::create_workspace,
            commands::set_current_workspace,
            commands::get_current_workspace_id,
            commands::workspace_read_file,
            commands::workspace_write_file,
            commands::workspace_update_env,
            commands::export_workspace_backup,
            commands::import_workspace_backup,
            commands::detect_python,
            commands::diagnose_python_env,
            commands::export_python_diagnostic_report,
            commands::check_python_for_pip,
            commands::install_bundled_python,
            commands::create_venv,
            commands::pip_install,
            commands::pip_uninstall,
            commands::autostart_is_enabled,
            commands::autostart_set_enabled,
            commands::openakita_service_status,
            commands::openakita_service_start,
            commands::openakita_service_stop,
            commands::openakita_service_log,
            commands::openakita_check_pid_alive,
            commands::set_tray_backend_status,
            commands::is_backend_auto_starting,
            commands::backend_in_boot_grace_cmd,
            commands::repair_runtime_env,
            commands::get_auto_start_backend,
            commands::set_auto_start_backend,
            commands::get_auto_update,
            commands::set_auto_update,
            commands::openakita_list_skills,
            commands::openakita_list_providers,
            commands::openakita_list_models,
            commands::openakita_version,
            commands::openakita_health_check_endpoint,
            commands::openakita_health_check_im,
            commands::openakita_ensure_channel_deps,
            commands::openakita_install_skill,
            commands::openakita_uninstall_skill,
            commands::openakita_list_marketplace,
            commands::openakita_get_skill_config,
            commands::openakita_wecom_onboard_start,
            commands::openakita_wecom_onboard_poll,
            commands::openakita_feishu_onboard_start,
            commands::openakita_feishu_onboard_poll,
            commands::openakita_feishu_validate,
            commands::openakita_qqbot_onboard_start,
            commands::openakita_qqbot_onboard_poll,
            commands::openakita_qqbot_onboard_create,
            commands::openakita_qqbot_onboard_poll_and_create,
            commands::openakita_qqbot_validate,
            commands::openakita_wechat_onboard_start,
            commands::openakita_wechat_onboard_poll,
            commands::fetch_pypi_versions,
            commands::http_get_json,
            commands::http_proxy_request,
            commands::backend_fetch,
            commands::read_file_base64,
            commands::download_file,
            commands::copy_file_to_downloads,
            commands::show_item_in_folder,
            commands::open_file_with_default,
            commands::export_env_backup,
            commands::export_diagnostic_bundle,
            commands::build_feedback_zip,
            commands::upload_feedback_to_cloud,
            commands::save_pending_feedback,
            commands::get_feedback_config_offline,
            commands::open_external_url,
            commands::openakita_list_processes,
            commands::openakita_stop_all_processes,
            commands::is_first_run,
            commands::check_environment,
            commands::check_backend_availability,
            commands::cleanup_old_environment,
            commands::factory_reset,
            commands::start_onboarding_log,
            commands::append_onboarding_log,
            commands::append_onboarding_log_lines,
            commands::append_frontend_log,
            commands::save_log_export,
            commands::register_cli,
            commands::unregister_cli,
            commands::get_cli_status,
            commands::start_dragging
        ])
        .build(tauri::generate_context!())
    {
        Ok(a) => a,
        Err(e) => {
            let msg = format!("Tauri build failed: {e}");
            eprintln!("{msg}");
            state::write_crash_log(&msg, true);
            std::process::exit(1);
        }
    };

    app.run(|_app_handle, event| {
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Reopen { has_visible_windows, .. } = &event {
            if !has_visible_windows {
                if let Some(win) = _app_handle.get_webview_window("main") {
                    let _ = win.show();
                    let _ = win.set_focus();
                }
            }
        }
        if let tauri::RunEvent::Exit = event {
            heartbeat::signal_heartbeat_shutdown();
            let entries = process::list_service_pids();
            for ent in &entries {
                if ent.started_by == "external" {
                    continue;
                }
                if process::is_pid_running(ent.pid) {
                    let _ = process::kill_pid(ent.pid);
                }
                let _ = fs::remove_file(std::path::PathBuf::from(&ent.pid_file));
                state::remove_heartbeat_file(&ent.workspace_id);
            }
            process::kill_openakita_orphans();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bundled_backend_dir_returns_non_empty_path() {
        let dir = python_env::bundled_backend_dir();
        assert!(!dir.to_string_lossy().is_empty());
        assert!(
            dir.to_string_lossy().contains("openakita-server"),
            "bundled_backend_dir should contain 'openakita-server': {:?}",
            dir
        );
    }

    #[test]
    fn test_get_backend_executable_falls_back_to_venv() {
        let fake_venv = if cfg!(windows) {
            r"C:\nonexistent-test-venv-12345"
        } else {
            "/tmp/nonexistent-test-venv-12345"
        };
        let (exe, args) = runtime::get_backend_executable(fake_venv);
        let exe_str = exe.to_string_lossy();
        assert!(
            exe_str.contains("python"),
            "fallback exe should contain 'python': {}",
            exe_str
        );
        assert!(args.contains(&"-m".to_string()));
        assert!(args.contains(&"openakita.main".to_string()));
        assert!(args.contains(&"serve".to_string()));
    }

    #[test]
    fn test_venv_python_path_platform_layout() {
        let dir = if cfg!(windows) {
            r"C:\Users\test\.openakita\venv"
        } else {
            "/home/test/.openakita/venv"
        };
        let py = python_env::venv_python_path(dir);
        if cfg!(windows) {
            assert!(py.to_string_lossy().contains("Scripts"));
            assert!(py.to_string_lossy().ends_with("python.exe"));
        } else {
            assert!(py.to_string_lossy().contains("bin"));
            assert!(py.to_string_lossy().ends_with("python"));
        }
    }

    #[test]
    fn test_venv_pythonw_path_consistent_with_python_path() {
        let dir = if cfg!(windows) {
            r"C:\Users\test\.openakita\venv"
        } else {
            "/home/test/.openakita/venv"
        };
        let py = python_env::venv_python_path(dir);
        let pyw = python_env::venv_pythonw_path(dir);
        if cfg!(not(windows)) {
            assert_eq!(py, pyw);
        }
        if cfg!(windows) {
            assert!(pyw.to_string_lossy().contains("python"));
        }
    }

    #[test]
    fn test_check_backend_availability_with_nonexistent_venv() {
        let fake = if cfg!(windows) {
            r"C:\nonexistent-venv-test-99999"
        } else {
            "/tmp/nonexistent-venv-test-99999"
        };
        let result = commands::check_backend_availability(fake.to_string());
        assert!(!result.venv_ready);
        assert!(!result.venv_checked.is_empty());
        assert!(!result.bundled_checked.is_empty());
    }

    #[test]
    fn test_stale_heartbeat_cleanup_requires_http_failure() {
        assert!(!state::should_cleanup_stale_heartbeat(Some(true), true));
        assert!(state::should_cleanup_stale_heartbeat(Some(true), false));
        assert!(!state::should_cleanup_stale_heartbeat(Some(false), false));
        assert!(!state::should_cleanup_stale_heartbeat(None, false));
    }

    #[test]
    fn test_cli_backend_exe_path_does_not_panic() {
        let result = cli::cli_backend_exe_path();
        assert!(result.is_ok() || result.is_err());
    }

    #[test]
    fn test_openakita_root_dir_is_valid() {
        let root = state::openakita_root_dir();
        assert!(!root.to_string_lossy().is_empty());
        let root_str = root.to_string_lossy();
        assert!(
            root_str.contains(".openakita") || std::env::var("OPENAKITA_ROOT").is_ok(),
            "root dir should contain '.openakita' or OPENAKITA_ROOT should be set: {}",
            root_str
        );
    }

    #[test]
    fn test_data_root_rejects_drive_or_filesystem_root() {
        let root = if cfg!(windows) {
            PathBuf::from(r"D:\")
        } else {
            PathBuf::from("/")
        };
        assert!(!state::is_safe_openakita_data_root(&root));
        assert!(state::ensure_safe_openakita_data_root(&root).is_err());
    }

    #[test]
    fn test_data_root_rejects_home_directory() {
        if let Some(home) = home_dir() {
            assert!(!state::is_safe_openakita_data_root(&home));
            assert!(state::ensure_safe_openakita_data_root(&home).is_err());
        }
    }

    #[test]
    fn test_data_root_allows_dedicated_directory() {
        let dedicated = if cfg!(windows) {
            PathBuf::from(r"D:\OpenAkitaData\.openakita")
        } else {
            PathBuf::from("/tmp/openakita-data/.openakita")
        };
        assert!(state::is_safe_openakita_data_root(&dedicated));
        assert!(state::ensure_safe_openakita_data_root(&dedicated).is_ok());
    }

    #[test]
    fn test_cli_bin_dir_is_valid() {
        let dir = cli::cli_bin_dir();
        assert!(!dir.to_string_lossy().is_empty());
        if cfg!(windows) {
            assert!(dir.to_string_lossy().contains("bin"));
        } else {
            assert!(dir.to_string_lossy().contains("bin"));
        }
    }
}
