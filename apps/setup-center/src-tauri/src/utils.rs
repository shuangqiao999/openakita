// utils.rs — 独立工具函数（从 main.rs 提取）
//
// 分组:
//   G1: 日期时间
//   G2: 文件系统
//   G3: Zip / walkdir
//   G4: Python 命令构建器

use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

// ═══════════════════════════════════════════════════════════════
// G4: Python 命令构建器
// ═══════════════════════════════════════════════════════════════

#[cfg(target_os = "windows")]
pub fn apply_no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(target_os = "windows"))]
pub fn apply_no_window(_cmd: &mut Command) {}

/// 清除可能干扰 Python 运行环境的外部环境变量。
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

/// 为 Python 进程准备 Command：隐藏控制台窗口 + 清除有害环境变量 + UTF-8 编码
pub fn prepare_python_command(cmd: &mut Command) {
    apply_no_window(cmd);
    strip_harmful_python_env(cmd);
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
}

// ═══════════════════════════════════════════════════════════════
// G1: 日期时间工具
// ═══════════════════════════════════════════════════════════════

pub fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

pub fn now_epoch_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn chrono_like_timestamp() -> String {
    let dt = time_from_epoch(now_epoch_secs());
    format!(
        "{:04}{:02}{:02}_{:02}{:02}{:02}",
        dt.0, dt.1, dt.2, dt.3, dt.4, dt.5
    )
}

pub fn time_from_epoch(epoch_secs: u64) -> (u32, u32, u32, u32, u32, u32) {
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

// ═══════════════════════════════════════════════════════════════
// G2: 文件系统工具
// ═══════════════════════════════════════════════════════════════

pub const FRONTEND_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;
pub const FRONTEND_LOG_TRUNCATE_TO: u64 = 2 * 1024 * 1024;

pub fn maybe_rotate_log_file(path: &Path, max_bytes: u64, keep_bytes: u64) {
    let meta = match fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return,
    };
    if meta.len() <= max_bytes {
        return;
    }
    let mut f = match fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return,
    };
    let start = meta.len().saturating_sub(keep_bytes);
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

pub fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<(), String> {
    fs::create_dir_all(dst).map_err(|e| format!("create dir {}: {e}", dst.display()))?;
    let entries = fs::read_dir(src).map_err(|e| format!("read dir {}: {e}", src.display()))?;
    for entry in entries.flatten() {
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        let ft = match entry.file_type() {
            Ok(ft) => ft,
            Err(_) => continue,
        };
        if ft.is_symlink() {
            continue;
        }
        if ft.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else if ft.is_file() {
            if let Err(e) = fs::copy(&src_path, &dst_path) {
                eprintln!(
                    "copy file {} -> {}: {e}",
                    src_path.display(),
                    dst_path.display()
                );
            }
        }
    }
    Ok(())
}

pub fn available_space_mb(path: &Path) -> f64 {
    #[cfg(target_os = "windows")]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        let fallback = path
            .ancestors()
            .last()
            .map(|r| r.to_string_lossy().to_string())
            .unwrap_or_else(|| "C:\\".to_string());
        let wide: Vec<u16> = OsStr::new(path.to_str().unwrap_or(&fallback))
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let mut free_bytes: u64 = 0;
        unsafe {
            #[link(name = "kernel32")]
            extern "system" {
                fn GetDiskFreeSpaceExW(
                    lpDirectoryName: *const u16,
                    lpFreeBytesAvailableToCaller: *mut u64,
                    lpTotalNumberOfBytes: *mut u64,
                    lpTotalNumberOfFreeBytes: *mut u64,
                ) -> i32;
            }
            GetDiskFreeSpaceExW(
                wide.as_ptr(),
                &mut free_bytes,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            );
        }
        free_bytes as f64 / 1024.0 / 1024.0
    }
    #[cfg(not(target_os = "windows"))]
    {
        use std::mem::MaybeUninit;
        let c_path =
            std::ffi::CString::new(path.to_str().unwrap_or("/")).unwrap_or_default();
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

pub fn force_remove_dir(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    if fs::remove_dir_all(path).is_ok() {
        return Ok(());
    }
    #[cfg(target_os = "windows")]
    {
        let mut attrib = Command::new("cmd");
        apply_no_window(&mut attrib);
        attrib.args(["/c", "attrib", "-R", "/S", "/D"]).arg(path);
        let _ = attrib.status();
        let mut rd_cmd = Command::new("cmd");
        apply_no_window(&mut rd_cmd);
        rd_cmd.args(["/c", "rd", "/s", "/q"]).arg(path);
        let status = rd_cmd
            .status()
            .map_err(|e| format!("执行 rd 命令失败: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = Command::new("chmod")
            .args(["-R", "u+w"])
            .arg(path)
            .status();
        let status = Command::new("rm")
            .args(["-rf"])
            .arg(path)
            .status()
            .map_err(|e| format!("rm -rf failed: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    if path.exists() {
        Err(format!("无法删除目录: {}", path.display()))
    } else {
        Ok(())
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

pub fn read_text_lossy(path: &Path) -> String {
    match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(_) => fs::read(path)
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
            .unwrap_or_default(),
    }
}

// ═══════════════════════════════════════════════════════════════
// G3: Zip / walkdir 工具
// ═══════════════════════════════════════════════════════════════

pub mod walkdir_entry {
    use std::path::{Path, PathBuf};
    pub struct Entry {
        pub path: PathBuf,
    }
    impl Entry {
        pub fn path(&self) -> &Path {
            &self.path
        }
    }
}

pub fn walkdir(dir: &Path) -> Vec<walkdir_entry::Entry> {
    let mut result = Vec::new();
    walkdir_recurse(dir, &mut result);
    result
}

fn walkdir_recurse(dir: &Path, out: &mut Vec<walkdir_entry::Entry>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    for entry in rd.flatten() {
        let path = entry.path();
        out.push(walkdir_entry::Entry { path: path.clone() });
        if path.is_dir() {
            walkdir_recurse(&path, out);
        }
    }
}

pub fn zip_collect_files(dir: &Path) -> Vec<PathBuf> {
    let mut result = Vec::new();
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                result.extend(zip_collect_files(&path));
            } else {
                result.push(path);
            }
        }
    }
    result
}

pub fn zip_add_dir(
    zw: &mut zip::ZipWriter<fs::File>,
    dir: &Path,
    prefix: &str,
    opts: zip::write::SimpleFileOptions,
) {
    if !dir.exists() {
        return;
    }
    for fp in zip_collect_files(dir) {
        if let Ok(rel) = fp.strip_prefix(dir) {
            let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
            if zw.start_file(&name, opts).is_ok() {
                let _ = zw.write_all(&fs::read(&fp).unwrap_or_default());
            }
        }
    }
}

pub fn zip_add_dir_capped(
    zw: &mut zip::ZipWriter<fs::File>,
    dir: &Path,
    prefix: &str,
    opts: zip::write::SimpleFileOptions,
    max_bytes: u64,
) {
    if !dir.exists() {
        return;
    }
    let mut files = zip_collect_files(dir);
    files.sort_by(|a, b| {
        let ma = fs::metadata(a).and_then(|m| m.modified()).ok();
        let mb = fs::metadata(b).and_then(|m| m.modified()).ok();
        mb.cmp(&ma)
    });
    let mut total: u64 = 0;
    for fp in files {
        let sz = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
        if total + sz > max_bytes {
            continue;
        }
        if let Ok(rel) = fp.strip_prefix(dir) {
            let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
            if zw.start_file(&name, opts).is_ok() {
                let _ = zw.write_all(&fs::read(&fp).unwrap_or_default());
                total += sz;
            }
        }
    }
}

pub fn zip_add_file(
    zw: &mut zip::ZipWriter<fs::File>,
    path: &Path,
    zip_name: &str,
    opts: zip::write::SimpleFileOptions,
) {
    if !path.exists() || !path.is_file() {
        return;
    }
    if zw.start_file(zip_name, opts).is_ok() {
        let _ = zw.write_all(&fs::read(path).unwrap_or_default());
    }
}
