//! Cognitrix desktop shell.
//!
//! The window first shows a small bundled start screen (desktop/splash) while
//! the Python engine boots, then navigates to the UI the engine serves on
//! http://127.0.0.1:<port>. The same React app runs on the web; it detects the
//! desktop shell and turns on the title bar, tray and notification features.

mod engine;
mod settings;
mod tray;

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, Emitter, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

const MAIN: &str = "main";
const QUICK: &str = "quick";
const MINIMIZED_FLAG: &str = "--minimized";

fn quick_ask_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::ALT), Code::Space)
}

/// Only the engine's own pages (and the bundled start screen) open inside
/// the app. Anything else goes to the default browser.
fn allow_navigation(app: &AppHandle, url: &Url) -> bool {
    let local_app = matches!(url.scheme(), "tauri" | "asset")
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
        || (url.scheme() == "https" && url.host_str() == Some("tauri.localhost"));
    let engine = url.scheme() == "http" && url.host_str() == Some("127.0.0.1");
    if local_app || engine || url.scheme() == "about" || url.scheme() == "blob" || url.scheme() == "data" {
        return true;
    }
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
    false
}

fn build_main_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let handle = app.clone();
    WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
        .title("Cognitrix")
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 620.0)
        .center()
        .decorations(false)
        .shadow(true)
        .visible(false)
        .background_color(tauri::window::Color(11, 12, 14, 255))
        .on_navigation(move |url| allow_navigation(&handle, url))
        .build()
}

fn build_quick_window(app: &AppHandle, base: &str) -> tauri::Result<WebviewWindow> {
    let url = Url::parse(&format!("{base}/quick")).expect("engine url");
    let handle = app.clone();
    WebviewWindowBuilder::new(app, QUICK, WebviewUrl::External(url))
        .title("Quick ask")
        .inner_size(680.0, 440.0)
        .center()
        .decorations(false)
        .resizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .shadow(true)
        .visible(false)
        .background_color(tauri::window::Color(17, 19, 21, 255))
        .on_navigation(move |url| allow_navigation(&handle, url))
        .build()
}

/// Called by the engine module once the engine answers on its port.
pub(crate) fn on_engine_ready(app: &AppHandle, base: &str) {
    if let Some(main) = app.get_webview_window(MAIN) {
        let on_engine = main
            .url()
            .map(|current| current.as_str().starts_with(base))
            .unwrap_or(false);
        if !on_engine {
            if let Ok(url) = Url::parse(&format!("{base}/home")) {
                let _ = main.navigate(url);
            }
        }
    }
    match app.get_webview_window(QUICK) {
        Some(quick) => {
            let stale = quick
                .url()
                .map(|current| !current.as_str().starts_with(base))
                .unwrap_or(true);
            if stale {
                if let Ok(url) = Url::parse(&format!("{base}/quick")) {
                    let _ = quick.navigate(url);
                }
            }
        }
        None => {
            if let Err(e) = build_quick_window(app, base) {
                log::warn!("could not create the quick ask window: {e}");
            }
        }
    }
}

pub(crate) fn show_main(app: &AppHandle, path: Option<String>) {
    if let Some(main) = app.get_webview_window(MAIN) {
        let _ = main.unminimize();
        let _ = main.show();
        let _ = main.set_focus();
        if let Some(path) = path {
            let _ = main.emit("navigate", path);
        }
    }
}

pub(crate) fn toggle_quick_ask(app: &AppHandle) {
    let Some(quick) = app.get_webview_window(QUICK) else {
        // Engine not ready yet: open the main window, which shows progress.
        show_main(app, None);
        return;
    };
    if quick.is_visible().unwrap_or(false) {
        let _ = quick.hide();
    } else {
        // It may have been sent to the sign-in page (or on to /home after
        // signing in there); always open on quick ask.
        if let Ok(mut url) = quick.url() {
            if url.path() != "/quick" {
                url.set_path("/quick");
                url.set_query(None);
                let _ = quick.navigate(url);
            }
        }
        let _ = quick.center();
        let _ = quick.show();
        let _ = quick.set_focus();
        let _ = quick.emit("quick-ask-open", ());
    }
}

pub(crate) fn notify(app: &AppHandle, title: &str, body: &str) {
    if !settings::load(app).notify_desktop {
        return;
    }
    let _ = app.notification().builder().title(title).body(body).show();
}

pub(crate) fn open_engine_log(app: &AppHandle) {
    let path = engine::log_path(app);
    let _ = app.opener().reveal_item_in_dir(path);
}

// ---------- Commands the UI calls (window.__TAURI_INTERNALS__.invoke) ----------

/// Open a page in the default browser: Google/GitHub sign-in can't run inside
/// the app window. Only https pages and the local engine are allowed.
#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    let parsed = Url::parse(&url).map_err(|e| e.to_string())?;
    let engine = parsed.scheme() == "http" && parsed.host_str() == Some("127.0.0.1");
    if parsed.scheme() != "https" && !engine {
        return Err("Only https pages can be opened".into());
    }
    app.opener()
        .open_url(parsed.as_str(), None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn engine_status(app: AppHandle) -> engine::EngineStatus {
    app.state::<engine::Engine>().status()
}

#[tauri::command]
fn restart_engine(app: AppHandle) {
    engine::restart(&app);
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum FolderKind {
    Logs,
    Data,
    Working,
}

#[tauri::command]
fn open_folder(app: AppHandle, kind: FolderKind) -> Result<(), String> {
    let path = match kind {
        FolderKind::Logs => return Ok(open_engine_log(&app)),
        FolderKind::Data => settings::data_folder(&app),
        FolderKind::Working => settings::working_folder(&app, &settings::load(&app)),
    };
    app.opener()
        .open_path(path.display().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}

#[derive(Serialize)]
struct DesktopSettingsView {
    close_to_tray: bool,
    notify_desktop: bool,
    start_with_windows: bool,
    working_folder: String,
    data_folder: String,
    quick_ask_shortcut: String,
    version: String,
}

fn settings_view(app: &AppHandle) -> DesktopSettingsView {
    let prefs = settings::load(app);
    DesktopSettingsView {
        close_to_tray: prefs.close_to_tray,
        notify_desktop: prefs.notify_desktop,
        start_with_windows: app.autolaunch().is_enabled().unwrap_or(false),
        working_folder: settings::working_folder(app, &prefs).display().to_string(),
        data_folder: settings::data_folder(app).display().to_string(),
        quick_ask_shortcut: "Alt+Space".into(),
        version: app.package_info().version.to_string(),
    }
}

#[tauri::command]
fn get_desktop_settings(app: AppHandle) -> DesktopSettingsView {
    settings_view(&app)
}

#[derive(Deserialize)]
struct DesktopSettingsPatch {
    close_to_tray: Option<bool>,
    notify_desktop: Option<bool>,
    start_with_windows: Option<bool>,
}

#[tauri::command]
fn set_desktop_settings(app: AppHandle, patch: DesktopSettingsPatch) -> Result<DesktopSettingsView, String> {
    let mut prefs = settings::load(&app);
    if let Some(value) = patch.close_to_tray {
        prefs.close_to_tray = value;
    }
    if let Some(value) = patch.notify_desktop {
        prefs.notify_desktop = value;
    }
    settings::save(&app, &prefs)?;
    if let Some(enable) = patch.start_with_windows {
        let launcher = app.autolaunch();
        let result = if enable { launcher.enable() } else { launcher.disable() };
        result.map_err(|e| e.to_string())?;
    }
    Ok(settings_view(&app))
}

/// Let the person pick the folder the agent's shell and file tools are
/// limited to, then restart the engine so it takes effect.
#[tauri::command]
async fn pick_working_folder(app: AppHandle) -> Result<Option<String>, String> {
    let Some(picked) = app.dialog().file().set_title("Choose the agents' working folder").blocking_pick_folder() else {
        return Ok(None);
    };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    let mut prefs = settings::load(&app);
    prefs.working_folder = Some(path.display().to_string());
    settings::save(&app, &prefs)?;
    engine::restart(&app);
    Ok(Some(path.display().to_string()))
}

#[tauri::command]
fn desktop_notify(app: AppHandle, title: String, body: String) {
    notify(&app, &title, &body);
}

#[tauri::command]
fn show_main_window(app: AppHandle, path: Option<String>) {
    show_main(&app, path);
}

#[tauri::command]
fn hide_quick_ask(app: AppHandle) {
    if let Some(quick) = app.get_webview_window(QUICK) {
        let _ = quick.hide();
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        // Must be first: a second launch focuses the running app instead.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main(app, None);
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![MINIMIZED_FLAG]),
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() == ShortcutState::Pressed && *shortcut == quick_ask_shortcut() {
                        toggle_quick_ask(app);
                    }
                })
                .build(),
        )
        .manage(engine::Engine::new())
        .setup(|app| {
            let handle = app.handle().clone();
            let main = build_main_window(&handle)?;
            if !std::env::args().any(|arg| arg == MINIMIZED_FLAG) {
                main.show()?;
            }
            tray::create(&handle)?;
            if let Err(e) = handle.global_shortcut().register(quick_ask_shortcut()) {
                log::warn!("Alt+Space is taken by another app, quick ask has no hotkey: {e}");
            }
            engine::start(&handle);
            Ok(())
        })
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } if window.label() == MAIN => {
                if settings::load(window.app_handle()).close_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    window.app_handle().exit(0);
                }
            }
            WindowEvent::Focused(false) if window.label() == QUICK => {
                let _ = window.hide();
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            engine_status,
            restart_engine,
            open_folder,
            open_external,
            get_desktop_settings,
            set_desktop_settings,
            pick_working_folder,
            desktop_notify,
            show_main_window,
            hide_quick_ask,
        ])
        .build(tauri::generate_context!())
        .expect("error while building the Cognitrix desktop app");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            engine::stop(app);
        }
    });
}
