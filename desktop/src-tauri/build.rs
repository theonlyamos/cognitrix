// Declaring the app's own commands turns them into permissions
// (allow-<command>), which capabilities/ grants to the app's windows.
// Without this, Tauri refuses every one of them when called from the
// engine's pages on http://127.0.0.1, a remote origin to Tauri: the
// settings page, notifications, Restart engine and the rest failed silently.
const COMMANDS: &[&str] = &[
    "engine_status",
    "restart_engine",
    "open_folder",
    "open_external",
    "get_desktop_settings",
    "set_desktop_settings",
    "pick_working_folder",
    "pick_folder",
    "desktop_notify",
    "show_main_window",
    "hide_quick_ask",
    "overlay_stop",
    "get_saved_session",
    "save_session",
    "clear_session",
    "test_notification",
    "check_for_update",
    "install_update",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
