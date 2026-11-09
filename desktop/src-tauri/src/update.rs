//! Self-update.
//!
//! The app asks GitHub for the newest release, downloads it, checks its
//! signature against the public key the build was given, and installs it.
//! A build made without that key (a local build, or a release made before
//! signing was set up) can't verify anything, so it never updates itself.
//! On Linux only the AppImage can replace itself; .deb and .rpm installs are
//! updated by downloading the new package.

use std::{thread, time::Duration};

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

use crate::engine;

const FIRST_CHECK_AFTER: Duration = Duration::from_secs(90);
const CHECK_EVERY: Duration = Duration::from_secs(12 * 60 * 60);

#[derive(Serialize)]
pub struct UpdateInfo {
    /// This build can update itself.
    enabled: bool,
    current: String,
    /// The newer version, when there is one.
    available: Option<String>,
    notes: Option<String>,
}

fn enabled(app: &AppHandle) -> bool {
    let has_key = app
        .config()
        .plugins
        .0
        .get("updater")
        .and_then(|updater| updater.get("pubkey"))
        .and_then(|key| key.as_str())
        .is_some_and(|key| !key.trim().is_empty());
    #[cfg(target_os = "linux")]
    {
        has_key && app.env().appimage.is_some()
    }
    #[cfg(not(target_os = "linux"))]
    {
        has_key
    }
}

async fn newer(app: &AppHandle) -> Result<Option<tauri_plugin_updater::Update>, String> {
    let updater = app.updater().map_err(|e| format!("The updater couldn't start: {e}"))?;
    updater.check().await.map_err(|e| format!("Couldn't check for updates: {e}"))
}

#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();
    if !enabled(&app) {
        return Ok(UpdateInfo { enabled: false, current, available: None, notes: None });
    }
    let update = newer(&app).await?;
    Ok(UpdateInfo {
        enabled: true,
        current,
        available: update.as_ref().map(|u| u.version.clone()),
        notes: update.and_then(|u| u.body),
    })
}

/// Download the newest version, stop the engine, install and restart.
#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    if !enabled(&app) {
        return Err("This build can't update itself. Download the new version instead.".into());
    }
    let Some(update) = newer(&app).await? else {
        return Err("Cognitrix is already up to date.".into());
    };
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|e| format!("The download failed: {e}"))?;
    // The installer replaces the engine's files, so nothing may be using them.
    engine::stop(&app);
    if let Err(e) = update.install(bytes) {
        engine::restart(&app);
        return Err(format!("The update couldn't be installed: {e}"));
    }
    // On Windows the installer has already taken over by now.
    app.restart();
}

/// Look for a new version a little after startup and twice a day after that,
/// and say so once per version.
pub fn watch(app: &AppHandle) {
    if !enabled(app) {
        return;
    }
    let app = app.clone();
    thread::spawn(move || {
        let mut told: Option<String> = None;
        thread::sleep(FIRST_CHECK_AFTER);
        loop {
            match tauri::async_runtime::block_on(newer(&app)) {
                Ok(Some(update)) if told.as_deref() != Some(update.version.as_str()) => {
                    crate::notify(
                        &app,
                        "A Cognitrix update is ready",
                        &format!("Version {} can be installed from Settings, Desktop app.", update.version),
                        Some("/settings/desktop"),
                    );
                    told = Some(update.version);
                }
                Ok(_) => {}
                Err(e) => log::warn!("{e}"),
            }
            thread::sleep(CHECK_EVERY);
        }
    });
}
