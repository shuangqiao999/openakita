use std::fs;
use std::io::Write;
use std::path::Path;

/// Unified recursive file collector
pub fn collect_files(dir: &Path) -> Vec<std::path::PathBuf> {
    let mut result = Vec::new();
    collect_files_recurse(dir, &mut result);
    result
}

fn collect_files_recurse(dir: &Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        out.push(path.clone());
        if path.is_dir() {
            collect_files_recurse(&path, out);
        }
    }
}

/// Add entire directory to zip (uncapped)
pub fn zip_add_dir(
    zw: &mut zip::ZipWriter<fs::File>,
    dir: &Path,
    prefix: &str,
    opts: zip::write::SimpleFileOptions,
) {
    if !dir.exists() {
        return;
    }
    for fp in collect_files(dir) {
        if let Ok(rel) = fp.strip_prefix(dir) {
            let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
            if zw.start_file(&name, opts).is_ok() {
                let _ = zw.write_all(&fs::read(&fp).unwrap_or_default());
            }
        }
    }
}

/// Add directory to zip with size cap (newest files first)
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
    let mut files = collect_files(dir);
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

/// Add single file to zip
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

/// Simple recursive walkdir replacement (for backup export)
pub mod walkdir {
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

pub fn walkdir(dir: &Path) -> Vec<walkdir::Entry> {
    let mut result = Vec::new();
    walkdir_recurse(dir, &mut result);
    result
}

fn walkdir_recurse(dir: &Path, out: &mut Vec<walkdir::Entry>) {
    let Ok(rd) = fs::read_dir(dir) else {
        return;
    };
    for entry in rd.flatten() {
        let path = entry.path();
        out.push(walkdir::Entry { path: path.clone() });
        if path.is_dir() {
            walkdir_recurse(&path, out);
        }
    }
}
