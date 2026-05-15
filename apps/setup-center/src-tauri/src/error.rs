use std::fmt;

#[derive(Debug)]
pub enum AppError {
    Io(std::io::Error),
    Json(serde_json::Error),
    Http(reqwest::Error),
    Lock(String),
    Command(String),
    Config(String),
    Workspace(String),
    Runtime(String),
    Process(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::Io(e) => write!(f, "IO error: {e}"),
            AppError::Json(e) => write!(f, "JSON error: {e}"),
            AppError::Http(e) => write!(f, "HTTP error: {e}"),
            AppError::Lock(s) => write!(f, "Lock error: {s}"),
            AppError::Command(s) => write!(f, "Command error: {s}"),
            AppError::Config(s) => write!(f, "Config error: {s}"),
            AppError::Workspace(s) => write!(f, "Workspace error: {s}"),
            AppError::Runtime(s) => write!(f, "Runtime error: {s}"),
            AppError::Process(s) => write!(f, "Process error: {s}"),
        }
    }
}

impl std::error::Error for AppError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            AppError::Io(e) => Some(e),
            AppError::Json(e) => Some(e),
            AppError::Http(e) => Some(e),
            _ => None,
        }
    }
}

impl From<std::io::Error> for AppError {
    fn from(e: std::io::Error) -> Self {
        AppError::Io(e)
    }
}

impl From<serde_json::Error> for AppError {
    fn from(e: serde_json::Error) -> Self {
        AppError::Json(e)
    }
}

impl From<reqwest::Error> for AppError {
    fn from(e: reqwest::Error) -> Self {
        AppError::Http(e)
    }
}

impl From<String> for AppError {
    fn from(s: String) -> Self {
        AppError::Command(s)
    }
}

pub type AppResult<T> = Result<T, AppError>;
