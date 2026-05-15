use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::state::{self, AppStateFile, WorkspaceMeta, WorkspaceSummary, STATE_FILE_LOCK};

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct EnvEntry {
    pub key: String,
    pub value: String,
}

pub fn write_root_marker(root: &Path) -> Result<(), String> {
    fs::create_dir_all(root).map_err(|e| format!("create dir failed: {e}"))?;
    fs::write(
        root.join(state::OPENAKITA_ROOT_MARKER),
        b"OpenAkita data root\nDo not delete this file unless you no longer use this directory for OpenAkita.\n",
    )
    .map_err(|e| format!("write root marker failed: {e}"))
}

pub fn ensure_workspace_scaffold(dir: &Path) -> Result<(), String> {
    fs::create_dir_all(dir.join("data")).map_err(|e| format!("create data dir failed: {e}"))?;
    fs::create_dir_all(dir.join("identity"))
        .map_err(|e| format!("create identity dir failed: {e}"))?;

    let env_path = dir.join(".env");
    if !env_path.exists() {
        let content = [
            "# OpenAkita workspace environment (managed by Setup Center)",
            "#",
            "# - Only keys you explicitly set in Setup Center are written here.",
            "# - Clearing a value removes the key from this file.",
            "# - For the full template, see examples/.env.example",
            "",
        ]
        .join("\n");
        fs::write(&env_path, content).map_err(|e| format!("write .env failed: {e}"))?;
    }

    const DEFAULT_SOUL: &str = include_str!("../../../../identity/SOUL.md.example");
    const DEFAULT_AGENT: &str = include_str!("../../../../identity/AGENT.md.example");
    const DEFAULT_USER: &str = include_str!("../../../../identity/USER.md.example");
    const DEFAULT_MEMORY: &str = include_str!("../../../../identity/MEMORY.md.example");

    let soul = dir.join("identity").join("SOUL.md");
    if !soul.exists() {
        fs::write(&soul, DEFAULT_SOUL)
            .map_err(|e| format!("write identity/SOUL.md failed: {e}"))?;
    }
    let agent_md = dir.join("identity").join("AGENT.md");
    if !agent_md.exists() {
        fs::write(&agent_md, DEFAULT_AGENT)
            .map_err(|e| format!("write identity/AGENT.md failed: {e}"))?;
    }
    let user_md = dir.join("identity").join("USER.md");
    if !user_md.exists() {
        fs::write(&user_md, DEFAULT_USER)
            .map_err(|e| format!("write identity/USER.md failed: {e}"))?;
    }
    let memory_md = dir.join("identity").join("MEMORY.md");
    if !memory_md.exists() {
        fs::write(&memory_md, DEFAULT_MEMORY)
            .map_err(|e| format!("write identity/MEMORY.md failed: {e}"))?;
    }

    {
        const PERSONA_DEFAULT: &str = include_str!("../../../../identity/personas/default.md");
        const PERSONA_BUSINESS: &str = include_str!("../../../../identity/personas/business.md");
        const PERSONA_TECH_EXPERT: &str =
            include_str!("../../../../identity/personas/tech_expert.md");
        const PERSONA_BUTLER: &str = include_str!("../../../../identity/personas/butler.md");
        const PERSONA_GIRLFRIEND: &str =
            include_str!("../../../../identity/personas/girlfriend.md");
        const PERSONA_BOYFRIEND: &str = include_str!("../../../../identity/personas/boyfriend.md");
        const PERSONA_FAMILY: &str = include_str!("../../../../identity/personas/family.md");
        const PERSONA_JARVIS: &str = include_str!("../../../../identity/personas/jarvis.md");
        const PERSONA_USER_CUSTOM: &str =
            include_str!("../../../../identity/personas/user_custom.md.example");

        let personas_dir = dir.join("identity").join("personas");
        fs::create_dir_all(&personas_dir)
            .map_err(|e| format!("create identity/personas dir failed: {e}"))?;

        let presets: &[(&str, &str)] = &[
            ("default.md", PERSONA_DEFAULT),
            ("business.md", PERSONA_BUSINESS),
            ("tech_expert.md", PERSONA_TECH_EXPERT),
            ("butler.md", PERSONA_BUTLER),
            ("girlfriend.md", PERSONA_GIRLFRIEND),
            ("boyfriend.md", PERSONA_BOYFRIEND),
            ("family.md", PERSONA_FAMILY),
            ("jarvis.md", PERSONA_JARVIS),
            ("user_custom.md", PERSONA_USER_CUSTOM),
        ];

        for (filename, content) in presets {
            let path = personas_dir.join(filename);
            if !path.exists() {
                fs::write(&path, content)
                    .map_err(|e| format!("write identity/personas/{filename} failed: {e}"))?;
            }
        }
    }

    {
        let prompts_dir = dir.join("identity").join("prompts");
        fs::create_dir_all(&prompts_dir)
            .map_err(|e| format!("create identity/prompts dir failed: {e}"))?;
        let policies = prompts_dir.join("policies.md");
        if !policies.exists() {
            const DEFAULT_POLICIES: &str = include_str!("../../../../identity/prompts/policies.md");
            fs::write(&policies, DEFAULT_POLICIES)
                .map_err(|e| format!("write identity/prompts/policies.md failed: {e}"))?;
        }
    }

    {
        let runtime_dir = dir.join("identity").join("runtime");
        fs::create_dir_all(&runtime_dir)
            .map_err(|e| format!("create identity/runtime dir failed: {e}"))?;

        const AGENT_CORE: &str = include_str!("../../../../identity/runtime/agent.core.md");
        const AGENT_TOOLING: &str = include_str!("../../../../identity/runtime/agent.tooling.md");

        let golden_files: &[(&str, &str)] = &[
            ("agent.core.md", AGENT_CORE),
            ("agent.tooling.md", AGENT_TOOLING),
        ];
        for (filename, content) in golden_files {
            let path = runtime_dir.join(filename);
            if !path.exists() {
                fs::write(&path, content)
                    .map_err(|e| format!("write identity/runtime/{filename} failed: {e}"))?;
            }
        }
    }

    let llm = dir.join("data").join("llm_endpoints.json");
    if !llm.exists() {
        const DEFAULT_LLM_ENDPOINTS: &str =
            include_str!("../../../../data/llm_endpoints.json.example");
        fs::write(&llm, DEFAULT_LLM_ENDPOINTS)
            .map_err(|e| format!("write data/llm_endpoints.json failed: {e}"))?;
    }

    Ok(())
}

pub fn validate_workspace_id(id: &str) -> Result<(), String> {
    let id = id.trim();
    if id.is_empty() {
        return Err("workspace id is empty".into());
    }
    if id.len() > 64 {
        return Err("workspace id too long (max 64 chars)".into());
    }
    if !id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err("workspace id can only contain a-z, A-Z, 0-9, _ and -".into());
    }
    if !id.chars().any(|c| c.is_ascii_alphanumeric()) {
        return Err("workspace id must contain at least one letter or digit".into());
    }
    const RESERVED: &[&str] = &[
        "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
        "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    ];
    if RESERVED.contains(&id.to_ascii_lowercase().as_str()) {
        return Err("workspace id conflicts with a reserved system name".into());
    }
    Ok(())
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

pub fn force_remove_dir(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    if fs::remove_dir_all(path).is_ok() {
        return Ok(());
    }
    #[cfg(target_os = "windows")]
    {
        let mut attrib = std::process::Command::new("cmd");
        attrib.args(["/c", "attrib", "-R", "/S", "/D"]).arg(path);
        crate::python_env::apply_no_window(&mut attrib);
        let _ = attrib.status();
        let mut rd_cmd = std::process::Command::new("cmd");
        rd_cmd.args(["/c", "rd", "/s", "/q"]).arg(path);
        crate::python_env::apply_no_window(&mut rd_cmd);
        let status = rd_cmd
            .status()
            .map_err(|e| format!("rd command failed: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    #[cfg(not(windows))]
    {
        let _ = std::process::Command::new("chmod")
            .args(["-R", "u+w"])
            .arg(path)
            .status();
        let status = std::process::Command::new("rm")
            .args(["-rf"])
            .arg(path)
            .status()
            .map_err(|e| format!("rm -rf failed: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    if path.exists() {
        Err(format!("unable to remove dir: {}", path.display()))
    } else {
        Ok(())
    }
}

pub fn update_env_content(existing: &str, entries: &[EnvEntry]) -> String {
    let mut updates = std::collections::BTreeMap::new();
    let mut deletes = std::collections::BTreeSet::new();
    for e in entries {
        if e.key.trim().is_empty() {
            continue;
        }
        let k = e.key.trim().to_string();
        if e.value.trim().is_empty() {
            deletes.insert(k);
        } else {
            updates.insert(k, e.value.clone());
        }
    }
    if updates.is_empty() && deletes.is_empty() {
        return existing.to_string();
    }

    let mut out = Vec::new();
    let mut seen = std::collections::BTreeSet::new();

    for line in existing.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') || !trimmed.contains('=') {
            out.push(line.to_string());
            continue;
        }
        let (k, _v) = trimmed.split_once('=').unwrap_or((trimmed, ""));
        let key = k.trim();
        if deletes.contains(key) {
            seen.insert(key.to_string());
            continue;
        }
        if let Some(new_val) = updates.get(key) {
            out.push(format!("{key}={new_val}"));
            seen.insert(key.to_string());
        } else {
            out.push(line.to_string());
        }
    }

    for (k, v) in updates {
        if !seen.contains(&k) {
            out.push(format!("{k}={v}"));
        }
    }

    let mut s = out.join("\n");
    if !s.ends_with('\n') {
        s.push('\n');
    }
    s
}

pub fn create_workspace_impl(
    id: String,
    name: String,
    set_current: bool,
) -> Result<(WorkspaceSummary, AppStateFile), String> {
    validate_workspace_id(&id)?;
    if name.trim().is_empty() {
        return Err("workspace name is empty".into());
    }

    fs::create_dir_all(state::workspaces_dir())
        .map_err(|e| format!("create workspaces dir failed: {e}"))?;

    let _lock = STATE_FILE_LOCK
        .lock()
        .map_err(|e| format!("state lock failed: {e}"))?;
    let mut state = state::read_state_file();
    if state.workspaces.iter().any(|w| w.id == id) {
        return Err("workspace id already exists".into());
    }
    state.workspaces.push(WorkspaceMeta {
        id: id.clone(),
        name: name.clone(),
    });
    if set_current {
        state.current_workspace_id = Some(id.clone());
    } else if state.current_workspace_id.is_none() {
        state.current_workspace_id = Some(id.clone());
    }
    state::write_state_file(&state)?;

    let dir = state::workspace_dir(&id);
    ensure_workspace_scaffold(&dir)?;

    let summary = WorkspaceSummary {
        id: id.clone(),
        name,
        path: dir.to_string_lossy().to_string(),
        is_current: state.current_workspace_id.as_deref() == Some(&id),
    };
    Ok((summary, state))
}

pub fn set_current_workspace_impl(id: String) -> Result<AppStateFile, String> {
    let _lock = STATE_FILE_LOCK
        .lock()
        .map_err(|e| format!("state lock failed: {e}"))?;
    let mut state = state::read_state_file();
    if !state.workspaces.iter().any(|w| w.id == id) {
        return Err("workspace id not found".into());
    }
    let dir = state::workspace_dir(&id);
    if !dir.exists() {
        eprintln!(
            "workspace dir missing, recreating scaffold: {}",
            dir.display()
        );
        ensure_workspace_scaffold(&dir)?;
    }
    state.current_workspace_id = Some(id);
    state::write_state_file(&state)?;
    Ok(state)
}
