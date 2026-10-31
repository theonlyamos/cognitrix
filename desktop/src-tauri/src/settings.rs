//! Desktop-only preferences, kept in the app's config folder as desktop.json.
//! Everything about agents, tasks and accounts lives in the engine instead.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct DesktopSettings {
    /// Closing the window hides it to the tray instead of quitting.
    pub close_to_tray: bool,
    /// Show a Windows notification when a task finishes or needs you.
    pub notify_desktop: bool,
    /// Folder the agent's shell and file tools are limited to. None means
    /// the default, ~/Cognitrix.
    pub working_folder: Option<String>,
    /// Port the engine tries first. A fixed port keeps the UI's origin, and
    /// with it your sign-in, the same across launches.
    pub preferred_port: u16,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            close_to_tray: true,
            notify_desktop: true,
            working_folder: None,
            preferred_port: 48210,
        }
    }
}

fn settings_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|dir| dir.join("desktop.json"))
}

pub fn load(app: &AppHandle) -> DesktopSettings {
    settings_path(app)
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

pub fn save(app: &AppHandle, settings: &DesktopSettings) -> Result<(), String> {
    let path = settings_path(app).ok_or("No config folder for this app")?;
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    fs::write(path, text).map_err(|e| e.to_string())
}

pub fn home_dir(app: &AppHandle) -> PathBuf {
    app.path().home_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// The working folder the engine actually uses.
pub fn working_folder(app: &AppHandle, settings: &DesktopSettings) -> PathBuf {
    match &settings.working_folder {
        Some(folder) if !folder.trim().is_empty() => PathBuf::from(folder),
        _ => home_dir(app).join("Cognitrix"),
    }
}

/// Where the engine keeps its database, keys and task queue (shared with the
/// `cognitrix` command line tool).
pub fn data_folder(app: &AppHandle) -> PathBuf {
    home_dir(app).join(".cognitrix")
}
