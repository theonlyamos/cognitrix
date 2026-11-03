//! Keeps the sign-in token outside the webview, so the app stays signed in
//! across restarts even when the engine comes up on a different port (a new
//! origin starts with empty localStorage) or the webview's storage is cleared.
//!
//! Windows keeps it in Credential Manager and macOS in the Keychain. Linux
//! has no keyring every desktop is sure to run, so it goes in a file only
//! your user can read, in the app's data folder. If the keyring refuses, the
//! file is used there too, so sign-in still sticks.

use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

#[cfg(any(windows, target_os = "macos"))]
const ACCOUNT: &str = "session";

fn file_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_local_data_dir().ok().map(|dir| dir.join("session"))
}

fn read_file(app: &AppHandle) -> Option<String> {
    let text = fs::read_to_string(file_path(app)?).ok()?;
    let token = text.trim().to_string();
    (!token.is_empty()).then_some(token)
}

fn write_file(app: &AppHandle, token: &str) -> Result<(), String> {
    let path = file_path(app).ok_or("No data folder for this app")?;
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    #[cfg(unix)]
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&path)
            .map_err(|e| e.to_string())?;
        file.write_all(token.as_bytes()).map_err(|e| e.to_string())?;
        // An older file may have been created with wider permissions.
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
        Ok(())
    }
    #[cfg(not(unix))]
    {
        fs::write(&path, token).map_err(|e| e.to_string())
    }
}

fn remove_file(app: &AppHandle) {
    if let Some(path) = file_path(app) {
        let _ = fs::remove_file(path);
    }
}

#[cfg(any(windows, target_os = "macos"))]
fn entry(app: &AppHandle) -> Option<keyring::Entry> {
    keyring::Entry::new(&app.config().identifier, ACCOUNT)
        .map_err(|e| log::warn!("keyring unavailable: {e}"))
        .ok()
}

/// The saved token, if any.
pub fn load(app: &AppHandle) -> Option<String> {
    #[cfg(any(windows, target_os = "macos"))]
    if let Some(entry) = entry(app) {
        match entry.get_password() {
            Ok(token) if !token.trim().is_empty() => return Some(token),
            Ok(_) | Err(keyring::Error::NoEntry) => {}
            Err(e) => log::warn!("could not read the saved sign-in: {e}"),
        }
    }
    read_file(app)
}

pub fn save(app: &AppHandle, token: &str) -> Result<(), String> {
    let token = token.trim();
    if token.is_empty() {
        clear(app);
        return Ok(());
    }
    #[cfg(any(windows, target_os = "macos"))]
    if let Some(entry) = entry(app) {
        match entry.set_password(token) {
            Ok(()) => {
                // Don't leave an older copy behind in the fallback file.
                remove_file(app);
                return Ok(());
            }
            Err(e) => {
                log::warn!("could not save the sign-in to the keyring, using a file: {e}");
                // load() reads the keyring first: drop the older entry there
                // so the file's newer token is the one that's used.
                let _ = entry.delete_credential();
            }
        }
    }
    write_file(app, token)
}

pub fn clear(app: &AppHandle) {
    #[cfg(any(windows, target_os = "macos"))]
    if let Some(entry) = entry(app) {
        let _ = entry.delete_credential();
    }
    remove_file(app);
}
