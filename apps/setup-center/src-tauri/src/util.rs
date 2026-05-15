use dirs_next::home_dir;
use once_cell::sync::Lazy;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

static CACHED_HOME_DIR: Lazy<Mutex<Option<PathBuf>>> = Lazy::new(|| Mutex::new(None));
static CACHED_EXE_PATH: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new(String::new()));
static CACHED_CWD: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new(String::new()));

pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

pub fn now_epoch_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn cached_home_dir() -> PathBuf {
    let mut cache = CACHED_HOME_DIR.lock().unwrap();
    if let Some(ref p) = *cache {
        return p.clone();
    }
    let p = home_dir().unwrap_or_else(|| PathBuf::from("."));
    *cache = Some(p.clone());
    p
}

pub fn cached_exe_str() -> String {
    let mut cache = CACHED_EXE_PATH.lock().unwrap();
    if !cache.is_empty() {
        return cache.clone();
    }
    let s = std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "<unknown>".into());
    *cache = s.clone();
    s
}

pub fn cached_cwd_str() -> String {
    let mut cache = CACHED_CWD.lock().unwrap();
    if !cache.is_empty() {
        return cache.clone();
    }
    let s = std::env::current_dir()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "<unknown>".into());
    *cache = s.clone();
    s
}

pub fn comparable_path(path: &Path) -> String {
    let mut text = path.to_string_lossy().replace('/', "\\");
    while text.len() > 1 && text.ends_with('\\') {
        text.pop();
    }
    if cfg!(windows) {
        text.to_ascii_lowercase()
    } else {
        text
    }
}

pub fn is_path_root(path: &Path) -> bool {
    path.parent().is_none() || path.file_name().is_none()
}

pub fn read_text_lossy(path: &Path) -> String {
    match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(_) => fs::read(path)
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
            .unwrap_or_default(),
    }
}

pub fn atomic_write_with_backup(path: &Path, content: &[u8]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create parent dir failed: {e}"))?;
    }
    if path.exists() {
        let bak = path.with_extension("json.bak");
        let _ = fs::copy(path, &bak);
    }
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, content).map_err(|e| format!("write tmp failed: {e}"))?;
    for attempt in 0..3u64 {
        match fs::rename(&tmp, path) {
            Ok(()) => return Ok(()),
            Err(e) => {
                if attempt < 2 {
                    std::thread::sleep(std::time::Duration::from_millis(100 * (attempt + 1)));
                } else {
                    eprintln!(
                        "atomic rename failed after 3 retries ({e}), falling back to direct write"
                    );
                    if let Err(e2) = fs::write(path, content) {
                        let _ = fs::remove_file(&tmp);
                        return Err(format!("write failed: {e2}"));
                    }
                    let _ = fs::remove_file(&tmp);
                    return Ok(());
                }
            }
        }
    }
    Ok(())
}

pub fn sanitize_download_filename(candidate: &str) -> String {
    let leaf = Path::new(candidate)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(candidate);
    let sanitized: String = leaf
        .chars()
        .map(|ch| match ch {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            ch if ch.is_control() => '_',
            ch => ch,
        })
        .collect();
    let trimmed = sanitized.trim_matches(|ch| ch == ' ' || ch == '.');
    let name = if trimmed.is_empty() { "download" } else { trimmed };
    let stem = Path::new(name)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(name);
    let reserved = matches!(
        stem.to_ascii_uppercase().as_str(),
        "CON" | "PRN" | "AUX" | "NUL"
            | "COM1" | "COM2" | "COM3" | "COM4" | "COM5" | "COM6" | "COM7" | "COM8" | "COM9"
            | "LPT1" | "LPT2" | "LPT3" | "LPT4" | "LPT5" | "LPT6" | "LPT7" | "LPT8" | "LPT9"
    );
    if reserved {
        format!("_{name}")
    } else {
        name.to_string()
    }
}

pub fn unique_download_path(filename: &str) -> Result<PathBuf, String> {
    let downloads_dir = dirs_next::download_dir()
        .or_else(|| home_dir().map(|h| h.join("Downloads")))
        .ok_or_else(|| "Cannot determine Downloads directory".to_string())?;
    fs::create_dir_all(&downloads_dir)
        .map_err(|e| format!("Cannot create Downloads dir: {e}"))?;

    let safe_filename = sanitize_download_filename(filename);
    let stem = Path::new(&safe_filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("download")
        .to_string();
    let ext = Path::new(&safe_filename)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| format!(".{s}"))
        .unwrap_or_default();
    let mut dest = downloads_dir.join(&safe_filename);
    let mut counter = 1u32;
    while dest.exists() {
        dest = downloads_dir.join(format!("{stem} ({counter}){ext}"));
        counter += 1;
    }
    Ok(dest)
}

pub fn clean_env_value(raw: &str) -> String {
    let v = raw.trim();
    if v.len() >= 2 {
        let bytes = v.as_bytes();
        if (bytes[0] == b'"' && bytes[v.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[v.len() - 1] == b'\'')
        {
            return v[1..v.len() - 1].to_string();
        }
    }
    for pat in [" #", "\t#"] {
        if let Some(pos) = v.find(pat) {
            return v[..pos].trim_end().to_string();
        }
    }
    v.to_string()
}

pub fn read_env_kv(path: &Path) -> Vec<(String, String)> {
    let Ok(content) = fs::read_to_string(path) else {
        return vec![];
    };
    let mut out = vec![];
    for line in content.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') || !t.contains('=') {
            continue;
        }
        let (k, v) = t.split_once('=').unwrap_or((t, ""));
        let key = k.trim();
        if key.is_empty() {
            continue;
        }
        out.push((key.to_string(), clean_env_value(v)));
    }
    out
}

pub fn run_capture(cmd: &[String]) -> Result<String, String> {
    if cmd.is_empty() {
        return Err("empty command".into());
    }
    let mut c = std::process::Command::new(&cmd[0]);
    if cmd.len() > 1 {
        c.args(&cmd[1..]);
    }
    crate::python_env::apply_no_window(&mut c);
    let out = c.output().map_err(|e| format!("failed to run {:?}: {e}", cmd))?;
    let mut s = String::new();
    if !out.stdout.is_empty() {
        s.push_str(&String::from_utf8_lossy(&out.stdout));
    }
    if !out.stderr.is_empty() {
        s.push_str(&String::from_utf8_lossy(&out.stderr));
    }
    Ok(s.trim().to_string())
}

pub fn python_version_ok(version_text: &str) -> bool {
    let lower = version_text.to_lowercase();
    let Some(idx) = lower.find("python") else {
        return false;
    };
    let ver = version_text[idx..].split_whitespace().nth(1).unwrap_or("");
    let parts: Vec<_> = ver.split('.').collect();
    if parts.len() < 2 {
        return false;
    }
    let major: i32 = parts[0].parse().unwrap_or(0);
    let minor: i32 = parts[1].parse().unwrap_or(0);
    major == 3 && minor >= 11
}

pub fn dir_size_bytes(path: &Path) -> u64 {
    if !path.exists() {
        return 0;
    }
    let mut total: u64 = 0;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                total += p.metadata().map(|m| m.len()).unwrap_or(0);
            } else if p.is_dir() {
                total += dir_size_bytes(&p);
            }
        }
    }
    total
}

pub fn chrono_like_timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let dt = time_from_epoch(secs);
    format!(
        "{:04}{:02}{:02}_{:02}{:02}{:02}",
        dt.0, dt.1, dt.2, dt.3, dt.4, dt.5
    )
}

fn time_from_epoch(epoch_secs: u64) -> (u32, u32, u32, u32, u32, u32) {
    const SECS_PER_DAY: u64 = 86400;
    let total_days = epoch_secs / SECS_PER_DAY;
    let time_of_day = epoch_secs % SECS_PER_DAY;
    let hour = (time_of_day / 3600) as u32;
    let minute = ((time_of_day % 3600) / 60) as u32;
    let second = (time_of_day % 60) as u32;
    let mut year = 1970u32;
    let mut remaining = total_days;
    loop {
        let days_in_year = if is_leap(year) { 366 } else { 365 };
        if remaining < days_in_year {
            break;
        }
        remaining -= days_in_year;
        year += 1;
    }
    let days_in_months: [u64; 12] = if is_leap(year) {
        [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    } else {
        [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    };
    let mut month = 1u32;
    for &dm in &days_in_months {
        if remaining < dm {
            break;
        }
        remaining -= dm;
        month += 1;
    }
    let day = remaining as u32 + 1;
    (year, month, day, hour, minute, second)
}

fn is_leap(y: u32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

pub fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}
