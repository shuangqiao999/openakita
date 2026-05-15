use std::process::Command;

/// Check if a PID is running
pub fn is_pid_running(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(windows)]
    {
        let handle = unsafe { crate::win_ffi::win::OpenProcess(crate::win_ffi::win::PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if handle.is_null() {
            return false;
        }
        unsafe {
            crate::win_ffi::win::CloseHandle(handle);
        }
        return true;
    }
    #[cfg(not(windows))]
    {
        let status = Command::new("kill").args(["-0", &pid.to_string()]).status();
        status.map(|s| s.success()).unwrap_or(false)
    }
}

/// Kill a PID
pub fn kill_pid(pid: u32) -> Result<(), String> {
    if pid == 0 {
        return Ok(());
    }
    #[cfg(windows)]
    {
        let handle = unsafe { crate::win_ffi::win::OpenProcess(crate::win_ffi::win::PROCESS_TERMINATE, 0, pid) };
        if handle.is_null() {
            if !is_pid_running(pid) {
                return Ok(());
            }
            return Err(format!("无法打开进程（pid={pid}），权限不足或进程不存在"));
        }
        let ok = unsafe { crate::win_ffi::win::TerminateProcess(handle, 1) };
        unsafe {
            crate::win_ffi::win::CloseHandle(handle);
        }
        if ok == 0 {
            if !is_pid_running(pid) {
                return Ok(());
            }
            return Err(format!("TerminateProcess 失败（pid={pid}）"));
        }
        return Ok(());
    }
    #[cfg(not(windows))]
    {
        let pid_str = pid.to_string();
        let _ = Command::new("kill").args(["-TERM", &pid_str]).status();
        for _ in 0..10 {
            if !is_pid_running(pid) {
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
        let status = Command::new("kill")
            .args(["-KILL", &pid_str])
            .status()
            .map_err(|e| format!("kill -KILL failed: {e}"))?;
        if !status.success() && is_pid_running(pid) {
            return Err(format!("kill -KILL failed: {status}"));
        }
        Ok(())
    }
}

/// Check if PID belongs to an OpenAkita process
pub fn is_openakita_process(pid: u32) -> bool {
    if pid == 0 || !is_pid_running(pid) {
        return false;
    }
    #[cfg(windows)]
    {
        let snap = unsafe { crate::win_ffi::win::CreateToolhelp32Snapshot(crate::win_ffi::win::TH32CS_SNAPPROCESS, 0) };
        if snap == crate::win_ffi::win::INVALID_HANDLE_VALUE || snap.is_null() {
            return false;
        }
        let mut pe: crate::win_ffi::win::PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        pe.dw_size = std::mem::size_of::<crate::win_ffi::win::PROCESSENTRY32W>() as u32;

        let mut exe_name = String::new();
        if unsafe { crate::win_ffi::win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                if pe.th32_process_id == pid {
                    exe_name = String::from_utf16_lossy(
                        &pe.sz_exe_file[..pe.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(260)],
                    )
                    .to_ascii_lowercase();
                    break;
                }
                if unsafe { crate::win_ffi::win::Process32NextW(snap, &mut pe) } == 0 {
                    break;
                }
            }
        }
        unsafe {
            crate::win_ffi::win::CloseHandle(snap);
        }

        if exe_name.contains("openakita-server") {
            return true;
        }
        if !exe_name.contains("python") {
            return false;
        }

        let mut c = Command::new("powershell");
        c.args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            &format!("(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"),
        ]);
        crate::python_env::apply_no_window(&mut c);
        if let Ok(out) = c.output() {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            return s.contains("openakita");
        }
        false
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(cmdline) = std::fs::read_to_string(format!("/proc/{pid}/cmdline")) {
            return cmdline.to_lowercase().contains("openakita");
        }
        let output = Command::new("ps")
            .args(["-p", &pid.to_string(), "-o", "args="])
            .output();
        if let Ok(out) = output {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            return s.contains("openakita");
        }
        false
    }
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("ps")
            .args(["-p", &pid.to_string(), "-o", "args="])
            .output();
        if let Ok(out) = output {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            return s.contains("openakita");
        }
        false
    }
}

/// Get process creation time (Unix epoch seconds)
#[cfg(windows)]
pub fn get_process_create_time(pid: u32) -> Option<u64> {
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct FILETIME {
        dw_low_date_time: u32,
        dw_high_date_time: u32,
    }
    extern "system" {
        fn GetProcessTimes(
            hProcess: *mut std::ffi::c_void,
            lpCreationTime: *mut FILETIME,
            lpExitTime: *mut FILETIME,
            lpKernelTime: *mut FILETIME,
            lpUserTime: *mut FILETIME,
        ) -> i32;
    }
    unsafe {
        let handle = crate::win_ffi::win::OpenProcess(crate::win_ffi::win::PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let mut creation: FILETIME = std::mem::zeroed();
        let mut exit: FILETIME = std::mem::zeroed();
        let mut kernel: FILETIME = std::mem::zeroed();
        let mut user: FILETIME = std::mem::zeroed();
        let ok = GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user);
        crate::win_ffi::win::CloseHandle(handle);
        if ok == 0 {
            return None;
        }
        let ft = ((creation.dw_high_date_time as u64) << 32) | (creation.dw_low_date_time as u64);
        let unix_100ns = ft.checked_sub(116444736000000000)?;
        Some(unix_100ns / 10_000_000)
    }
}

#[cfg(target_os = "linux")]
pub fn get_process_create_time(pid: u32) -> Option<u64> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let after_comm = stat.rfind(')')? + 2;
    if after_comm >= stat.len() {
        return None;
    }
    let fields: Vec<&str> = stat[after_comm..].split_whitespace().collect();
    let starttime = fields.get(19)?.parse::<u64>().ok()?;
    let clk_tck: u64 = 100;
    let uptime_str = std::fs::read_to_string("/proc/uptime").ok()?;
    let uptime_secs: f64 = uptime_str.split_whitespace().next()?.parse().ok()?;
    let now = crate::util::now_epoch_secs();
    let boot_time = now.saturating_sub(uptime_secs as u64);
    Some(boot_time + starttime / clk_tck)
}

#[cfg(target_os = "macos")]
pub fn get_process_create_time(pid: u32) -> Option<u64> {
    let output = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "lstart="])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let lstart = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if lstart.is_empty() {
        return None;
    }
    let date_out = Command::new("date")
        .args(["-jf", "%a %b %d %T %Y", &lstart, "+%s"])
        .output()
        .ok()?;
    let epoch_str = String::from_utf8_lossy(&date_out.stdout).trim().to_string();
    epoch_str.parse::<u64>().ok()
}

/// Validate PID file data against actual process
pub fn is_pid_file_valid(data: &crate::state::PidFileData) -> bool {
    if !is_pid_running(data.pid) {
        return false;
    }
    if data.started_at == 0 {
        return is_openakita_process(data.pid);
    }
    if let Some(actual_create) = get_process_create_time(data.pid) {
        let diff = if data.started_at > actual_create {
            data.started_at - actual_create
        } else {
            actual_create - data.started_at
        };
        if diff > 5 {
            return is_openakita_process(data.pid);
        }
        true
    } else {
        is_openakita_process(data.pid)
    }
}

/// List all service PIDs from run directory
#[derive(Debug, serde::Serialize, serde::Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct ServicePidEntry {
    pub workspace_id: String,
    pub pid: u32,
    pub pid_file: String,
    #[serde(default)]
    pub started_by: String,
}

pub fn list_service_pids() -> Vec<ServicePidEntry> {
    let mut out = Vec::new();
    let dir = crate::state::run_dir();
    let Ok(rd) = std::fs::read_dir(&dir) else {
        return out;
    };
    for e in rd.flatten() {
        let p = e.path();
        let Some(name) = p.file_name().and_then(|s| s.to_str()) else {
            continue;
        };
        if !name.starts_with("openakita-") || !name.ends_with(".pid") {
            continue;
        }
        let ws = name
            .trim_start_matches("openakita-")
            .trim_end_matches(".pid")
            .to_string();
        if let Some(data) = crate::state::read_pid_file(&ws) {
            out.push(ServicePidEntry {
                workspace_id: ws,
                pid: data.pid,
                pid_file: p.to_string_lossy().to_string(),
                started_by: data.started_by,
            });
        }
    }
    out
}

/// Kill orphaned OpenAkita processes (not tracked by PID files)
pub fn kill_openakita_orphans() -> Vec<u32> {
    let mut killed = Vec::new();
    #[cfg(windows)]
    {
        let snap = unsafe { crate::win_ffi::win::CreateToolhelp32Snapshot(crate::win_ffi::win::TH32CS_SNAPPROCESS, 0) };
        if snap == crate::win_ffi::win::INVALID_HANDLE_VALUE || snap.is_null() {
            return killed;
        }
        let mut pe: crate::win_ffi::win::PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        pe.dw_size = std::mem::size_of::<crate::win_ffi::win::PROCESSENTRY32W>() as u32;

        let mut python_pids: Vec<u32> = Vec::new();
        let mut bundled_pids: Vec<u32> = Vec::new();

        if unsafe { crate::win_ffi::win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                let name = String::from_utf16_lossy(
                    &pe.sz_exe_file[..pe.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(260)],
                );
                let name_lower = name.to_ascii_lowercase();
                if name_lower.contains("python") {
                    python_pids.push(pe.th32_process_id);
                }
                if name_lower.contains("openakita-server") {
                    bundled_pids.push(pe.th32_process_id);
                }
                if unsafe { crate::win_ffi::win::Process32NextW(snap, &mut pe) } == 0 {
                    break;
                }
            }
        }
        unsafe {
            crate::win_ffi::win::CloseHandle(snap);
        }

        for ppid in bundled_pids {
            if is_pid_running(ppid) {
                let _ = kill_pid(ppid);
                killed.push(ppid);
            }
        }

        for ppid in python_pids {
            let mut c = Command::new("powershell");
            c.args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                &format!("(Get-CimInstance Win32_Process -Filter 'ProcessId={ppid}').CommandLine"),
            ]);
            crate::python_env::apply_no_window(&mut c);
            if let Ok(out) = c.output() {
                let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
                if s.contains("openakita.main") && (s.contains(" serve") || s.ends_with("serve")) {
                    if is_pid_running(ppid) {
                        let _ = kill_pid(ppid);
                        killed.push(ppid);
                    }
                }
            }
        }
    }
    #[cfg(not(windows))]
    {
        // Use /proc scanning instead of relying on sh -c (more portable)
        let mut pids_to_kill: Vec<u32> = Vec::new();
        if let Ok(proc_dir) = std::fs::read_dir("/proc") {
            for entry in proc_dir.flatten() {
                let name = entry.file_name();
                let name_str = name.to_string_lossy();
                if let Ok(pid) = name_str.parse::<u32>() {
                    if let Ok(cmdline) = std::fs::read_to_string(format!("/proc/{pid}/cmdline")) {
                        let lower = cmdline.to_lowercase();
                        if (lower.contains("openakita.main") && lower.contains("serve"))
                            || lower.contains("openakita-server")
                        {
                            if is_pid_running(pid) && !killed.contains(&pid) {
                                pids_to_kill.push(pid);
                            }
                        }
                    }
                }
            }
        }

        // SIGTERM
        for &pid in &pids_to_kill {
            let _ = Command::new("kill").args(["-TERM", &pid.to_string()]).status();
        }
        if !pids_to_kill.is_empty() {
            std::thread::sleep(std::time::Duration::from_millis(1500));
        }
        // SIGKILL
        for pid in pids_to_kill {
            if is_pid_running(pid) {
                let _ = Command::new("kill").args(["-KILL", &pid.to_string()]).status();
            }
            killed.push(pid);
        }
    }
    killed
}

/// Check port availability
pub fn check_port_available(port: u16) -> bool {
    std::net::TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// Wait for port to become free
pub fn wait_for_port_free(port: u16, timeout_ms: u64) -> bool {
    let start = std::time::Instant::now();
    let timeout = std::time::Duration::from_millis(timeout_ms);
    while start.elapsed() < timeout {
        if check_port_available(port) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    false
}

/// Gracefully stop a backend PID via HTTP API, then force kill
pub fn graceful_stop_pid(pid: u32, port: Option<u16>) -> Result<(), String> {
    if !is_pid_running(pid) {
        return Ok(());
    }
    let effective_port = port.unwrap_or(18900);
    let api_ok = crate::state::BLOCKING_HTTP_CLIENT
        .post(format!("http://127.0.0.1:{effective_port}/api/shutdown"))
        .timeout(std::time::Duration::from_secs(3))
        .send()
        .ok()
        .map(|r| r.status().is_success())
        .unwrap_or(false);

    if api_ok {
        for _ in 0..25 {
            if !is_pid_running(pid) {
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    }
    if is_pid_running(pid) {
        kill_pid(pid)?;
        for _ in 0..10 {
            if !is_pid_running(pid) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    }
    if is_pid_running(pid) {
        Err(format!("pid {pid} still running after graceful + forced stop"))
    } else {
        Ok(())
    }
}

pub fn stop_service_pid_entry(ent: &ServicePidEntry, port: Option<u16>) -> Result<(), String> {
    if is_pid_running(ent.pid) {
        graceful_stop_pid(ent.pid, port)?;
    }
    let _ = std::fs::remove_file(std::path::PathBuf::from(&ent.pid_file));
    crate::state::remove_heartbeat_file(&ent.workspace_id);
    Ok(())
}

/// Read workspace API port from .env
pub fn read_workspace_api_port(workspace_id: &str) -> Option<u16> {
    let env_path = crate::state::workspace_dir(workspace_id).join(".env");
    let content = crate::util::read_text_lossy(&env_path);
    for line in content.lines() {
        let t = line.trim();
        if let Some(val) = t.strip_prefix("API_PORT=") {
            return val.trim().parse::<u16>().ok();
        }
    }
    None
}

pub fn healthy_backend_pid(port: u16) -> Option<u32> {
    let resp = crate::state::BLOCKING_HTTP_CLIENT
        .get(format!("http://127.0.0.1:{port}/api/health"))
        .timeout(std::time::Duration::from_secs(3))
        .send()
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let json: serde_json::Value = resp.json().ok()?;
    if json.get("service").and_then(|v| v.as_str()) != Some("openakita") {
        return None;
    }
    json.get("pid")
        .and_then(|v| v.as_u64())
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| is_pid_running(*pid))
}
