//! System notifications.
//!
//! On Windows these are native toasts sent straight through the WinRT toast
//! API, so clicking one (or its button) brings the app forward on the page
//! the notification is about. The notification plugin can't report clicks on
//! desktop, which is why Windows doesn't go through it. Other platforms still
//! use the plugin and only show the message.
//!
//! Windows only shows toasts for an app it knows. The installer's Start menu
//! shortcut carries the app id (the bundle identifier); on top of that the
//! app registers the id under HKCU at startup, which gives the toast the
//! Cognitrix name and icon even when the shortcut is missing.

use tauri::AppHandle;

use crate::settings;

/// What a notification says and where clicking it goes.
pub struct Notice<'a> {
    pub title: &'a str,
    pub body: &'a str,
    /// An in-app route such as `/inbox` or `/tasks/abc`.
    pub path: Option<String>,
    /// Approvals and questions stay on screen until the person acts.
    pub urgent: bool,
}

/// Only in-app routes are accepted: a leading slash, no scheme or host.
pub fn clean_path(path: Option<String>) -> Option<String> {
    path.filter(|p| p.starts_with('/') && !p.starts_with("//") && !p.contains('\\') && p.len() <= 512)
}

/// Show a notification if the person hasn't turned them off in Settings.
/// Returns true when one was shown.
pub fn show(app: &AppHandle, notice: Notice) -> bool {
    if !settings::load(app).notify_desktop {
        return false;
    }
    match platform::show(app, notice) {
        Ok(()) => true,
        Err(e) => {
            log::warn!("could not show a notification: {e}");
            false
        }
    }
}

/// Called once at startup.
pub fn init(app: &AppHandle) {
    platform::init(app);
}

#[cfg(windows)]
mod platform {
    use super::Notice;
    use std::path::{PathBuf, MAIN_SEPARATOR_STR as SEP};
    use tauri::{AppHandle, Manager};
    use tauri_winrt_notification::{Duration, Scenario, Toast};

    const ICON: &[u8] = include_bytes!("../icons/128x128.png");

    /// A build run from `cargo`/`tauri dev` has no Start menu entry, and
    /// Windows drops toasts from unknown app ids, so those borrow
    /// PowerShell's id (the same trick the notification plugin uses).
    fn dev_build() -> bool {
        let Ok(exe) = tauri::utils::platform::current_exe() else {
            return false;
        };
        let dir = exe.parent().map(|d| d.display().to_string()).unwrap_or_default();
        dir.ends_with(&format!("{SEP}target{SEP}debug")) || dir.ends_with(&format!("{SEP}target{SEP}release"))
    }

    fn app_id(app: &AppHandle) -> String {
        if dev_build() {
            Toast::POWERSHELL_APP_ID.to_string()
        } else {
            app.config().identifier.clone()
        }
    }

    fn icon_path(app: &AppHandle) -> Option<PathBuf> {
        let dir = app.path().app_local_data_dir().ok()?;
        std::fs::create_dir_all(&dir).ok()?;
        let path = dir.join("notification-icon.png");
        let current = std::fs::read(&path).map(|bytes| bytes == ICON).unwrap_or(false);
        if !current {
            std::fs::write(&path, ICON).ok()?;
        }
        Some(path)
    }

    /// Register the app id with a display name and icon, so toasts show
    /// "Cognitrix" and its icon. Harmless when the installer's shortcut
    /// already identifies the app.
    pub fn init(app: &AppHandle) {
        if dev_build() {
            return;
        }
        let id = app.config().identifier.clone();
        let name = app.package_info().name.clone();
        let icon = icon_path(app);
        let result = (|| -> std::io::Result<()> {
            let hkcu = winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER);
            let (key, _) = hkcu.create_subkey(format!(r"Software\Classes\AppUserModelId\{id}"))?;
            key.set_value("DisplayName", &name)?;
            if let Some(icon) = icon {
                key.set_value("IconUri", &icon.display().to_string())?;
            }
            Ok(())
        })();
        if let Err(e) = result {
            log::warn!("could not register the app for notifications: {e}");
        }
    }

    pub fn show(app: &AppHandle, notice: Notice) -> Result<(), String> {
        let mut toast = Toast::new(&app_id(app)).title(notice.title).text1(notice.body);
        if notice.urgent {
            // Reminders stay on screen until clicked or dismissed. Windows
            // only honours that when the toast has a button.
            toast = toast.scenario(Scenario::Reminder).duration(Duration::Long);
        }
        if notice.path.is_some() {
            toast = toast.add_button(if notice.urgent { "Review" } else { "Open" }, "open");
        }
        let handle = app.clone();
        let path = notice.path;
        toast
            .on_activated(move |_action| {
                crate::show_main(&handle, path.clone());
                Ok(())
            })
            .show()
            .map_err(|e| e.to_string())
    }
}

#[cfg(not(windows))]
mod platform {
    use super::Notice;
    use tauri::AppHandle;
    use tauri_plugin_notification::NotificationExt;

    pub fn init(_app: &AppHandle) {}

    pub fn show(app: &AppHandle, notice: Notice) -> Result<(), String> {
        app.notification()
            .builder()
            .title(notice.title)
            .body(notice.body)
            .show()
            .map_err(|e| e.to_string())
    }
}
