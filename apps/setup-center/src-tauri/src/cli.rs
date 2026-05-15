use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::python_env;
use crate::state;
use crate::util;

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct CliConfig {
    pub commands: Vec<String>,
    pub add_to_path: bool,
    pub bin_dir: String,
    pub installed_at: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct CliStatus {
    pub registered_commands: Vec<String>,
    pub in_path: bool,
    pub bin_dir: String,
}

pub fn cli_bin_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        exe_dir.join("bin")
    }
    #[cfg(not(target_os = "windows"))]
    {
        state::openakita_root_dir().join("bin")
    }
}

pub fn cli_backend_exe_path() -> Result<PathBuf, String> {
    let bundled_dir = python_env::bundled_backend_dir();
    let exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    if exe.exists() {
        return Ok(exe);
    }
    let venv_base = state::openakita_root_dir().join("venv");
    let venv_py = if cfg!(windows) {
        venv_base.join("Scripts").join("python.exe")
    } else {
        let py3 = venv_base.join("bin").join("python3");
        if py3.exists() {
            py3
        } else {
            venv_base.join("bin").join("python")
        }
    };
    if venv_py.exists() {
        return Ok(venv_py);
    }
    eprintln!(
        "[cli_backend_exe_path] not found. checked:\n  bundled: {}\n  venv: {}",
        exe.display(),
        venv_py.display(),
    );
    Err(format!(
        "back end executable not found (openakita-server or venv python)\n\
         checked: {} | {}",
        exe.display(),
        venv_py.display(),
    ))
}

pub fn read_cli_config() -> Option<CliConfig> {
    let path = state::openakita_root_dir().join("cli.json");
    if !path.exists() {
        return None;
    }
    let content = fs::read_to_string(&path).ok()?;
    serde_json::from_str(&content).ok()
}

pub fn write_cli_config(config: &CliConfig) -> Result<(), String> {
    let path = state::openakita_root_dir().join("cli.json");
    let content =
        serde_json::to_string_pretty(config).map_err(|e| format!("serialize CLI config failed: {e}"))?;
    util::atomic_write_with_backup(&path, content.as_bytes())
}

pub fn generate_wrapper_content(backend_exe: &Path) -> String {
    #[cfg(target_os = "windows")]
    {
        let _ = backend_exe;
        format!(
            "@echo off\r\n\"%~dp0..\\resources\\openakita-server\\openakita-server.exe\" %*\r\n"
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        let exe_path = backend_exe.to_string_lossy();
        format!(
            "#!/bin/sh\n# OpenAkita CLI wrapper - managed by OpenAkita Desktop\nexec \"{}\" \"$@\"\n",
            exe_path
        )
    }
}

pub fn create_wrapper_script(
    bin_dir: &Path,
    cmd_name: &str,
    backend_exe: &Path,
) -> Result<(), String> {
    let content = generate_wrapper_content(backend_exe);

    #[cfg(target_os = "windows")]
    let file_path = bin_dir.join(format!("{}.cmd", cmd_name));
    #[cfg(not(target_os = "windows"))]
    let file_path = bin_dir.join(cmd_name);

    fs::write(&file_path, &content)
        .map_err(|e| format!("write {} failed: {e}", file_path.display()))?;

    #[cfg(not(target_os = "windows"))]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o755);
        fs::set_permissions(&file_path, perms)
            .map_err(|e| format!("chmod {} failed: {e}", file_path.display()))?;
    }

    Ok(())
}

pub fn remove_wrapper_script(bin_dir: &Path, cmd_name: &str) {
    #[cfg(target_os = "windows")]
    let file_path = bin_dir.join(format!("{}.cmd", cmd_name));
    #[cfg(not(target_os = "windows"))]
    let file_path = bin_dir.join(cmd_name);

    let _ = fs::remove_file(&file_path);
}

// ── Windows PATH operations ──

#[cfg(target_os = "windows")]
pub fn windows_add_to_path(bin_dir: &Path) -> Result<(), String> {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let bin_norm = bin_str.trim_end_matches('\\');

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let key = hkcu
        .open_subkey_with_flags("Environment", KEY_READ | KEY_WRITE)
        .map_err(|e| format!("unable to open user env registry: {e}"))?;

    let current_path = read_path_value(&key)?;

    if current_path
        .split(';')
        .any(|p| p.trim_end_matches('\\').eq_ignore_ascii_case(bin_norm))
    {
        return Ok(());
    }

    let new_path = if current_path.is_empty() {
        bin_str
    } else {
        format!("{};{}", current_path, bin_str)
    };
    if new_path.len() > 2047 {
        return Err("PATH env var approaching length limit (2048), unable to append".into());
    }

    write_path_value(&key, &new_path)?;
    windows_broadcast_env_change();

    Ok(())
}

#[cfg(target_os = "windows")]
pub fn windows_remove_from_path(bin_dir: &Path) -> Result<(), String> {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let bin_norm = bin_str.trim_end_matches('\\');
    let mut modified = false;

    for (hive_predef, subkey_path) in [
        (
            HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (HKEY_CURRENT_USER, "Environment"),
    ] {
        let hive = RegKey::predef(hive_predef);
        if let Ok(key) = hive.open_subkey_with_flags(subkey_path, KEY_READ | KEY_WRITE) {
            let current_path = read_path_value(&key).unwrap_or_default();
            if current_path.is_empty() {
                continue;
            }
            let new_paths: Vec<&str> = current_path
                .split(';')
                .filter(|p| !p.trim_end_matches('\\').eq_ignore_ascii_case(bin_norm))
                .collect();
            let new_path = new_paths.join(";");
            if new_path != current_path {
                let _ = write_path_value(&key, &new_path);
                modified = true;
            }
        }
    }

    if modified {
        windows_broadcast_env_change();
    }
    Ok(())
}

#[cfg(target_os = "windows")]
pub fn windows_is_in_path(bin_dir: &Path) -> bool {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let bin_norm = bin_str.trim_end_matches('\\');

    for (hive_predef, subkey_path) in [
        (
            HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (HKEY_CURRENT_USER, "Environment"),
    ] {
        let hive = RegKey::predef(hive_predef);
        if let Ok(key) = hive.open_subkey_with_flags(subkey_path, KEY_READ) {
            if let Ok(current_path) = read_path_value(&key) {
                if current_path
                    .split(';')
                    .any(|p| p.trim_end_matches('\\').eq_ignore_ascii_case(bin_norm))
                {
                    return true;
                }
            }
        }
    }
    false
}

#[cfg(target_os = "windows")]
pub fn windows_broadcast_env_change() {
    use std::ffi::CString;

    #[link(name = "user32")]
    extern "system" {
        fn SendMessageTimeoutA(
            hwnd: isize,
            msg: u32,
            w_param: usize,
            l_param: *const u8,
            fu_flags: u32,
            u_timeout: u32,
            lpdw_result: *mut usize,
        ) -> isize;
    }
    let env_str = CString::new("Environment").unwrap();
    unsafe {
        let mut result: usize = 0;
        SendMessageTimeoutA(
            0xFFFF_isize,
            0x001A,
            0,
            env_str.as_ptr() as *const u8,
            0x0002,
            5000,
            &mut result,
        );
    }
}

#[cfg(target_os = "windows")]
pub fn read_path_value(key: &winreg::RegKey) -> Result<String, String> {
    use winreg::enums::RegType;
    match key.get_raw_value("Path") {
        Ok(raw) => {
            if raw.vtype != RegType::REG_SZ && raw.vtype != RegType::REG_EXPAND_SZ {
                return Err(format!("PATH registry value type unexpected: {:?}", raw.vtype));
            }
            let wide: Vec<u16> = raw
                .bytes
                .chunks_exact(2)
                .map(|c| u16::from_le_bytes([c[0], c[1]]))
                .collect();
            Ok(String::from_utf16_lossy(&wide)
                .trim_end_matches('\0')
                .to_string())
        }
        Err(_) => Ok(String::new()),
    }
}

#[cfg(target_os = "windows")]
pub fn write_path_value(key: &winreg::RegKey, value: &str) -> Result<(), String> {
    use winreg::enums::RegType;
    use winreg::RegValue;
    let wide: Vec<u16> = value.encode_utf16().chain(std::iter::once(0)).collect();
    let bytes: Vec<u8> = wide.iter().flat_map(|&w| w.to_le_bytes()).collect();
    key.set_raw_value(
        "Path",
        &RegValue {
            bytes,
            vtype: RegType::REG_EXPAND_SZ,
        },
    )
    .map_err(|e| format!("write PATH registry failed: {e}"))
}

// ── Unix PATH operations ──

#[cfg(not(target_os = "windows"))]
pub fn unix_add_to_path(bin_dir: &Path) -> Result<(), String> {
    let bin_str = bin_dir.to_string_lossy().to_string();
    let marker_start = "# >>> openakita cli >>>";
    let marker_end = "# <<< openakita cli <<<";
    let block = format!(
        "{}\nexport PATH=\"{}:$PATH\"\n{}\n",
        marker_start, bin_str, marker_end
    );

    let home = dirs_next::home_dir().ok_or("unable to get HOME dir")?;
    let profiles = get_shell_profiles(&home);

    for profile in &profiles {
        let existing = fs::read_to_string(profile).unwrap_or_default();
        if existing.contains(marker_start) {
            let lines: Vec<&str> = existing.lines().collect();
            let mut new_lines: Vec<&str> = Vec::new();
            let mut in_block = false;
            for line in &lines {
                if line.contains(marker_start) {
                    in_block = true;
                    continue;
                }
                if line.contains(marker_end) {
                    in_block = false;
                    continue;
                }
                if !in_block {
                    new_lines.push(line);
                }
            }
            let mut content = new_lines.join("\n");
            if !content.ends_with('\n') {
                content.push('\n');
            }
            content.push_str(&block);
            fs::write(profile, content)
                .map_err(|e| format!("write {} failed: {e}", profile.display()))?;
        } else {
            let mut content = existing;
            if !content.is_empty() && !content.ends_with('\n') {
                content.push('\n');
            }
            content.push_str(&block);
            fs::write(profile, content)
                .map_err(|e| format!("write {} failed: {e}", profile.display()))?;
        }
    }

    #[cfg(target_os = "linux")]
    {
        let local_bin = home.join(".local").join("bin");
        if local_bin.exists() || fs::create_dir_all(&local_bin).is_ok() {
            if let Some(config) = read_cli_config() {
                for cmd in &config.commands {
                    let src = bin_dir.join(cmd);
                    let dst = local_bin.join(cmd);
                    let _ = fs::remove_file(&dst);
                    let _ = std::os::unix::fs::symlink(&src, &dst);
                }
            }
        }
    }

    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn unix_remove_from_path(_bin_dir: &Path) -> Result<(), String> {
    let marker_start = "# >>> openakita cli >>>";
    let marker_end = "# <<< openakita cli <<<";

    let home = dirs_next::home_dir().ok_or("unable to get HOME dir")?;
    let profiles = get_shell_profiles(&home);

    for profile in &profiles {
        if !profile.exists() {
            continue;
        }
        let existing = fs::read_to_string(profile).unwrap_or_default();
        if !existing.contains(marker_start) {
            continue;
        }
        let lines: Vec<&str> = existing.lines().collect();
        let mut new_lines: Vec<&str> = Vec::new();
        let mut in_block = false;
        for line in &lines {
            if line.contains(marker_start) {
                in_block = true;
                continue;
            }
            if line.contains(marker_end) {
                in_block = false;
                continue;
            }
            if !in_block {
                new_lines.push(line);
            }
        }
        let content = new_lines.join("\n");
        let _ = fs::write(profile, content);
    }

    #[cfg(target_os = "linux")]
    {
        let local_bin = home.join(".local").join("bin");
        if let Some(config) = read_cli_config() {
            for cmd in &config.commands {
                let dst = local_bin.join(cmd);
                let _ = fs::remove_file(&dst);
            }
        }
    }

    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn unix_is_in_path(bin_dir: &Path) -> bool {
    let marker_start = "# >>> openakita cli >>>";
    let home = match dirs_next::home_dir() {
        Some(h) => h,
        None => return false,
    };
    let profiles = get_shell_profiles(&home);
    for profile in &profiles {
        if let Ok(content) = fs::read_to_string(profile) {
            if content.contains(marker_start) {
                return true;
            }
        }
    }
    if let Ok(path) = std::env::var("PATH") {
        let bin_str = bin_dir.to_string_lossy();
        if path.split(':').any(|p| p == bin_str.as_ref()) {
            return true;
        }
    }
    false
}

#[cfg(not(target_os = "windows"))]
pub fn get_shell_profiles(home: &Path) -> Vec<PathBuf> {
    let mut profiles = Vec::new();
    let zshrc = home.join(".zshrc");
    profiles.push(zshrc);
    #[cfg(target_os = "macos")]
    {
        profiles.push(home.join(".bash_profile"));
    }
    #[cfg(target_os = "linux")]
    {
        profiles.push(home.join(".bashrc"));
    }
    profiles
}

// ── CLI registration / unregistration ──

pub fn register_cli_impl(commands: Vec<String>, add_to_path: bool) -> Result<String, String> {
    if commands.is_empty() {
        return Err("at least one command name required".into());
    }

    for cmd in &commands {
        if !cmd
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Err(format!("command name '{}' contains illegal characters", cmd));
        }
    }

    let bin_dir = cli_bin_dir();
    fs::create_dir_all(&bin_dir).map_err(|e| format!("create bin dir failed: {e}"))?;

    let backend_exe = cli_backend_exe_path()?;

    for cmd_name in &commands {
        create_wrapper_script(&bin_dir, cmd_name, &backend_exe)?;
    }

    if add_to_path {
        #[cfg(target_os = "windows")]
        windows_add_to_path(&bin_dir)?;

        #[cfg(not(target_os = "windows"))]
        unix_add_to_path(&bin_dir)?;
    }

    let config = CliConfig {
        commands: commands.clone(),
        add_to_path,
        bin_dir: bin_dir.to_string_lossy().to_string(),
        installed_at: {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            format!("{}", now)
        },
    };
    write_cli_config(&config)?;

    Ok(format!(
        "CLI commands registered: {}{}",
        commands.join(", "),
        if add_to_path {
            " (added to PATH)"
        } else {
            ""
        }
    ))
}

pub fn unregister_cli_impl() -> Result<String, String> {
    let config = read_cli_config().ok_or("CLI config not found")?;
    let bin_dir = PathBuf::from(&config.bin_dir);

    for cmd_name in &config.commands {
        remove_wrapper_script(&bin_dir, cmd_name);
    }

    if config.add_to_path {
        #[cfg(target_os = "windows")]
        windows_remove_from_path(&bin_dir)?;

        #[cfg(not(target_os = "windows"))]
        unix_remove_from_path(&bin_dir)?;
    }

    let _ = fs::remove_dir(&bin_dir);

    let config_path = state::openakita_root_dir().join("cli.json");
    let _ = fs::remove_file(&config_path);

    Ok("CLI commands unregistered".into())
}

pub fn get_cli_status_impl() -> Result<CliStatus, String> {
    let bin_dir = cli_bin_dir();

    if let Some(config) = read_cli_config() {
        let existing_commands: Vec<String> = config
            .commands
            .iter()
            .filter(|cmd| {
                #[cfg(target_os = "windows")]
                let path = PathBuf::from(&config.bin_dir).join(format!("{}.cmd", cmd));
                #[cfg(not(target_os = "windows"))]
                let path = PathBuf::from(&config.bin_dir).join(cmd.as_str());
                path.exists()
            })
            .cloned()
            .collect();

        let in_path = {
            #[cfg(target_os = "windows")]
            {
                windows_is_in_path(&PathBuf::from(&config.bin_dir))
            }
            #[cfg(not(target_os = "windows"))]
            {
                unix_is_in_path(&PathBuf::from(&config.bin_dir))
            }
        };

        Ok(CliStatus {
            registered_commands: existing_commands,
            in_path,
            bin_dir: config.bin_dir,
        })
    } else {
        Ok(CliStatus {
            registered_commands: vec![],
            in_path: false,
            bin_dir: bin_dir.to_string_lossy().to_string(),
        })
    }
}
