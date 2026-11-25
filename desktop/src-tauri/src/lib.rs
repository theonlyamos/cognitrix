//! Cognitrix desktop shell.
//!
//! The window first shows a small bundled start screen (desktop/splash) while
//! the Python engine boots, then navigates to the UI the engine serves on
//! http://127.0.0.1:<port>. The same React app runs on the web; it detects the
//! desktop shell and turns on the title bar, tray and notification features.

mod engine;
mod installed;
mod notify;
mod overlay;
mod session;
mod settings;
mod tray;
mod update;

use serde::{Deserialize, Serialize};
use tauri::{
    webview::{NewWindowResponse, PermissionKind, PermissionResponse},
    AppHandle, Emitter, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_opener::OpenerExt;

const MAIN: &str = "main";
const QUICK: &str = "quick";
const MINIMIZED_FLAG: &str = "--minimized";

/// WebView2 arguments for every window (they must match, or the second
/// window fails to open). The first part is wry's default. The rest stops
/// Chromium from slowing timers in a hidden window: the app usually sits in
/// the tray, and the UI's polling is what notices approvals, questions and
/// finished tasks and raises their notifications.
pub(crate) const BROWSER_ARGS: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection \
    --disable-background-timer-throttling --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows";

fn quick_ask_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::ALT), Code::Space)
}

/// Only the engine's own pages (and the bundled start screen) open inside
/// the app. Anything else goes to the default browser.
pub(crate) fn allow_navigation(app: &AppHandle, url: &Url) -> bool {
    let local_app = matches!(url.scheme(), "tauri" | "asset")
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
        || (url.scheme() == "https" && url.host_str() == Some("tauri.localhost"));
    let engine = url.scheme() == "http" && url.host_str() == Some("127.0.0.1");
    if local_app || engine || url.scheme() == "about" || url.scheme() == "blob" || url.scheme() == "data" {
        return true;
    }
    open_in_browser(app, url);
    false
}

/// Web and mail links leave the app for the default browser or mail app.
/// Every other scheme (file:, javascript:, custom protocols) is dropped.
fn is_browser_link(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https" | "mailto")
}

fn open_in_browser(app: &AppHandle, url: &Url) {
    if is_browser_link(url) {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
}

/// A page asked for a new window: a `target="_blank"` link in a chat reply,
/// an artifact or an app, or `window.open`. The webview would otherwise drop
/// it silently, so the link opens in the default browser instead and no bare
/// webview window appears.
pub(crate) fn open_new_window(app: &AppHandle, url: &Url) -> NewWindowResponse<tauri::Wry> {
    open_in_browser(app, url);
    NewWindowResponse::Deny
}

/// Voice input in the chat box records the microphone. These windows only
/// ever show the bundled start screen or the engine's pages (see
/// `allow_navigation`), so the microphone is allowed without a prompt;
/// everything else keeps the webview's default behaviour.
fn permission_for(kind: PermissionKind) -> PermissionResponse {
    match kind {
        PermissionKind::Microphone => PermissionResponse::Allow,
        _ => PermissionResponse::Default,
    }
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
        .additional_browser_args(BROWSER_ARGS)
        .on_navigation({
            let handle = handle.clone();
            move |url| allow_navigation(&handle, url)
        })
        .on_new_window(move |url, _| open_new_window(&handle, &url))
        .on_permission_request(|_, kind| permission_for(kind))
        .build()
}

fn build_quick_window(app: &AppHandle, base: &str) -> tauri::Result<WebviewWindow> {
    let url = Url::parse(&format!("{base}/quick")).expect("engine url");
    let handle = app.clone();
    // See-through so the voice pill and its answer cards float on their own
    // (macOS stays opaque, see overlay::see_through). The page draws its own
    // shadows, so the window has none.
    let builder = WebviewWindowBuilder::new(app, QUICK, WebviewUrl::External(url))
        .title("Quick ask")
        .inner_size(QUICK_WIDTH, QUICK_MIN_HEIGHT)
        .decorations(false)
        .resizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .shadow(cfg!(target_os = "macos"))
        .visible(false);
    #[cfg(target_os = "macos")]
    let builder = builder.background_color(tauri::window::Color(17, 19, 21, 255));
    #[cfg(not(target_os = "macos"))]
    let builder = builder.background_color(tauri::window::Color(0, 0, 0, 0));
    overlay::see_through(builder)
        .additional_browser_args(BROWSER_ARGS)
        .on_navigation({
            let handle = handle.clone();
            move |url| allow_navigation(&handle, url)
        })
        .on_new_window(move |url, _| open_new_window(&handle, &url))
        .on_permission_request(|_, kind| permission_for(kind))
        .build()
}

const QUICK_WIDTH: f64 = 720.0;
const QUICK_MIN_HEIGHT: f64 = 88.0;
/// Gap between the pill and the taskbar (or the bottom of the screen).
const QUICK_BOTTOM_GAP: f64 = 12.0;

/// The monitor the mouse is on, so quick ask opens on the screen in use.
fn quick_monitor(window: &WebviewWindow) -> Option<tauri::Monitor> {
    window
        .cursor_position()
        .ok()
        .and_then(|p| window.monitor_from_point(p.x, p.y).ok().flatten())
        .or_else(|| window.current_monitor().ok().flatten())
        .or_else(|| window.primary_monitor().ok().flatten())
}

/// Size the quick ask window to `height` (CSS pixels) and dock it at the
/// bottom centre of the work area, just above the taskbar, so it grows
/// upward as answers come in.
fn dock_quick(window: &WebviewWindow, height: Option<f64>, monitor: Option<tauri::Monitor>) {
    let Some(monitor) = monitor.or_else(|| quick_monitor(window)) else {
        let _ = window.center();
        return;
    };
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let max_height = (area.size.height as f64 / scale) - QUICK_BOTTOM_GAP * 2.0;
    let css_height = match height {
        Some(h) => h,
        None => window
            .inner_size()
            .map(|s| s.height as f64 / window.scale_factor().unwrap_or(scale))
            .unwrap_or(QUICK_MIN_HEIGHT),
    }
    .clamp(QUICK_MIN_HEIGHT, max_height.max(QUICK_MIN_HEIGHT));
    let width = (QUICK_WIDTH * scale).round() as i32;
    let height = (css_height * scale).round() as i32;
    let x = area.position.x + (area.size.width as i32 - width) / 2;
    let y = area.position.y + area.size.height as i32 - height - (QUICK_BOTTOM_GAP * scale).round() as i32;
    let _ = window.set_size(tauri::PhysicalSize::new(width as u32, height as u32));
    let _ = window.set_position(tauri::PhysicalPosition::new(x, y));
}

/// The page reports how tall its pill and cards are; the window follows,
/// keeping its bottom edge where it is.
#[tauri::command]
fn fit_quick_ask(app: AppHandle, height: f64) {
    if let Some(quick) = app.get_webview_window(QUICK) {
        let monitor = quick.current_monitor().ok().flatten();
        dock_quick(&quick, Some(height), monitor);
    }
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
    installed::on_engine_ready(app);
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

/// The in-app route for a `cognitrix://` link, or None for links we don't know.
/// `cognitrix://pair?server=…&code=…` opens Settings › Computers with the
/// web account's address and pairing code filled in (the person still checks
/// the account and chooses folders before anything is allowed).
fn link_route(url: &str) -> Option<String> {
    let rest = url.strip_prefix("cognitrix://")?;
    let (target, query) = rest.split_once('?').unwrap_or((rest, ""));
    match target.trim_end_matches('/') {
        "pair" => {
            let keep: Vec<&str> = query
                .split('&')
                .filter(|kv| kv.starts_with("server=") || kv.starts_with("code="))
                .filter(|kv| kv.len() < 400 && kv.chars().all(|c| c.is_ascii_graphic()))
                .collect();
            Some(format!("/settings/computers?{}", keep.join("&")))
        }
        _ => None,
    }
}

// A link that arrived before the engine's page was up; the page asks for it.
static PENDING_LINK: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

fn open_link(app: &AppHandle, url: &str) {
    // cognitrix://app/<slug>: an installed app's launcher (macOS).
    if let Some(slug) = installed::slug_from_link(url) {
        installed::open_app(app, slug, None, None);
        return;
    }
    if let Some(route) = link_route(url) {
        if let Ok(mut pending) = PENDING_LINK.lock() {
            *pending = Some(route.clone());
        }
        show_main(app, Some(route));
    }
}

fn watch_links(app: &AppHandle) {
    use tauri_plugin_deep_link::DeepLinkExt;
    #[cfg(any(windows, target_os = "linux"))]
    if let Err(e) = app.deep_link().register_all() {
        log::warn!("could not register cognitrix:// links: {e}");
    }
    if let Ok(Some(urls)) = app.deep_link().get_current() {
        for url in urls {
            open_link(app, url.as_str());
        }
    }
    let handle = app.clone();
    app.deep_link().on_open_url(move |event| {
        for url in event.urls() {
            open_link(&handle, url.as_str());
        }
    });
}

/// The page asks once it's up: a link that opened the app before it was ready.
#[tauri::command]
fn take_pending_link() -> Option<String> {
    PENDING_LINK.lock().ok().and_then(|mut pending| pending.take())
}

pub(crate) fn toggle_quick_ask(app: &AppHandle) {
    let Some(quick) = app.get_webview_window(QUICK) else {
        // Engine not ready yet: open the main window, which shows progress.
        show_main(app, None);
        return;
    };
    if quick.is_visible().unwrap_or(false) {
        let _ = quick.hide();
        let _ = quick.emit("quick-ask-hide", ());
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
        dock_quick(&quick, None, None);
        let _ = quick.show();
        let _ = quick.set_focus();
        let _ = quick.emit("quick-ask-open", ());
    }
}

/// Show a system notification; clicking it opens `path` in the main window.
pub(crate) fn notify(app: &AppHandle, title: &str, body: &str, path: Option<&str>) -> bool {
    notify::show(
        app,
        notify::Notice { title, body, path: notify::clean_path(path.map(str::to_owned)), urgent: false, approval: None, app: None },
    )
}

pub(crate) fn autostart_enabled(app: &AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

/// Add the app to (or remove it from) the apps the OS opens when you sign
/// in: the Run key on Windows, a LaunchAgent on macOS, an autostart entry on
/// Linux. It opens minimized to the tray, and the engine starts with it.
pub(crate) fn set_autostart(app: &AppHandle, enable: bool) -> Result<(), String> {
    let launcher = app.autolaunch();
    let result = if enable { launcher.enable() } else { launcher.disable() };
    result.map_err(|e| e.to_string())?;
    tray::set_autostart_tick(app, autostart_enabled(app));
    Ok(())
}

/// Turn "Launch at startup" on the first time the app runs, and re-register
/// it on later runs so the entry follows the app after an update or a move.
/// Development builds are left alone.
fn init_autostart(app: &AppHandle) {
    if cfg!(debug_assertions) {
        return;
    }
    let mut prefs = settings::load(app);
    let enable = if prefs.autostart_initialized { autostart_enabled(app) } else { true };
    if enable {
        if let Err(e) = set_autostart(app, true) {
            log::warn!("could not turn on launch at startup: {e}");
            return;
        }
    }
    if !prefs.autostart_initialized {
        prefs.autostart_initialized = true;
        let _ = settings::save(app, &prefs);
    }
}

pub(crate) fn open_engine_log(app: &AppHandle) {
    let path = engine::log_path(app);
    let _ = app.opener().reveal_item_in_dir(path);
}

// ---------- Commands the UI calls (window.__TAURI_INTERNALS__.invoke) ----------

/// Open a link in the default browser (or mail app): Google/GitHub sign-in,
/// and links clicked in chat replies. Only http, https and mailto links.
#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    let parsed = Url::parse(&url).map_err(|e| e.to_string())?;
    if !is_browser_link(&parsed) {
        return Err("Only web and mail links can be opened".into());
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
    agent_cursor: bool,
}

fn settings_view(app: &AppHandle) -> DesktopSettingsView {
    let prefs = settings::load(app);
    DesktopSettingsView {
        close_to_tray: prefs.close_to_tray,
        notify_desktop: prefs.notify_desktop,
        start_with_windows: autostart_enabled(app),
        working_folder: settings::working_folder(app, &prefs).display().to_string(),
        data_folder: settings::data_folder(app).display().to_string(),
        quick_ask_shortcut: "Alt+Space".into(),
        version: app.package_info().version.to_string(),
        agent_cursor: prefs.agent_cursor,
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
    agent_cursor: Option<bool>,
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
    if let Some(value) = patch.agent_cursor {
        prefs.agent_cursor = value;
    }
    settings::save(&app, &prefs)?;
    if let Some(enable) = patch.start_with_windows {
        set_autostart(&app, enable)?;
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

/// Native folder picker for adding a working folder to a chat or an agent.
/// Only returns the path; the engine checks it before anything uses it.
#[tauri::command]
async fn pick_folder(app: AppHandle, title: Option<String>) -> Result<Option<String>, String> {
    let title = title.unwrap_or_else(|| "Add a folder".to_string());
    let Some(picked) = app.dialog().file().set_title(&title).blocking_pick_folder() else {
        return Ok(None);
    };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    Ok(Some(path.display().to_string()))
}

/// Returns whether a notification was shown, so the UI can fall back to its
/// own toast when they're off or Windows refused it. Async so the toast call
/// never blocks the window's thread.
#[tauri::command]
async fn desktop_notify(
    app: AppHandle,
    title: String,
    body: String,
    path: Option<String>,
    urgent: Option<bool>,
    approval: Option<String>,
) -> bool {
    notify::show(
        &app,
        notify::Notice {
            title: &title,
            body: &body,
            path: notify::clean_path(path),
            urgent: urgent.unwrap_or(false),
            approval: notify::clean_approval(approval),
            app: None,
        },
    )
}

/// The sign-in the app saved last time (see session.rs).
#[tauri::command]
fn get_saved_session(app: AppHandle) -> Option<String> {
    session::load(&app)
}

#[tauri::command]
fn save_session(app: AppHandle, token: String) -> Result<(), String> {
    session::save(&app, &token)
}

#[tauri::command]
fn clear_session(app: AppHandle) {
    session::clear(&app);
}

#[tauri::command]
async fn test_notification(app: AppHandle) -> Result<(), String> {
    notify::test(&app)
}

/// An app agents built (Apps page), in a window of its own: the engine's
/// /apps/open/<slug> page, which shows the app under a bar with Close.
/// Opening the same app again brings its window to the front. Async: on
/// Windows, creating a WebView2 window inside a synchronous command deadlocks.
#[tauri::command]
async fn open_app_window(app: AppHandle, slug: String, title: Option<String>) -> Result<(), String> {
    if !installed::valid_slug(&slug) {
        return Err("Not an app name".into());
    }
    if app.state::<engine::Engine>().status().url.is_none() {
        return Err("The engine isn't running yet".into());
    }
    installed::open_app_now(&app, slug, title, None)
}

#[tauri::command]
fn show_main_window(app: AppHandle, path: Option<String>) {
    show_main(&app, path);
}

/// The Stop button on the agent cursor's pill (stop.html).
#[tauri::command]
async fn overlay_stop(app: AppHandle, agent_id: Option<String>) -> Result<(), String> {
    overlay::stop(&app, agent_id)
}

#[tauri::command]
fn hide_quick_ask(app: AppHandle) {
    if let Some(quick) = app.get_webview_window(QUICK) {
        let _ = quick.hide();
        let _ = quick.emit("quick-ask-hide", ());
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        // Must be first: a second launch focuses the running app instead.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // An installed app's shortcut opens that app, not the main window.
            match installed::slug_from_args(&argv) {
                Some(slug) => installed::open_app(app, slug, None, None),
                None => show_main(app, None),
            }
        }))
        // After single-instance: a cognitrix:// link opened while the app
        // runs reaches this instance through it.
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        // The plugin's own link script swallows target=_blank clicks and calls
        // a command no capability grants (and that artifact frames can't
        // reach), so links did nothing. Links go through `on_new_window` and
        // `open_external` instead.
        .plugin(
            tauri_plugin_opener::Builder::new()
                .open_js_links_on_click(false)
                .build(),
        )
        .plugin(tauri_plugin_updater::Builder::new().build())
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
            // Started by an installed app's shortcut: only that app's window
            // opens (once the engine is up); Cognitrix stays in the tray.
            let launched_app = installed::slug_from_args(std::env::args());
            let app_link = {
                use tauri_plugin_deep_link::DeepLinkExt;
                handle.deep_link().get_current().ok().flatten().is_some_and(|urls| {
                    urls.iter().any(|u| installed::slug_from_link(u.as_str()).is_some())
                })
            };
            if !std::env::args().any(|arg| arg == MINIMIZED_FLAG) && launched_app.is_none() && !app_link {
                main.show()?;
            }
            tray::create(&handle)?;
            installed::init(&handle);
            if let Some(slug) = launched_app {
                installed::open_app(&handle, slug, None, None);
            }
            // (A cognitrix://app/ link opens its app in watch_links below.)
            init_autostart(&handle);
            notify::init(&handle);
            if let Err(e) = handle.global_shortcut().register(quick_ask_shortcut()) {
                log::warn!("Alt+Space is taken by another app, quick ask has no hotkey: {e}");
            }
            watch_links(&handle);
            engine::start(&handle);
            overlay::watch(&handle);
            update::watch(&handle);
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
                // The page closes the microphone on this.
                let _ = window.emit("quick-ask-hide", ());
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            take_pending_link,
            engine_status,
            restart_engine,
            open_folder,
            open_external,
            get_desktop_settings,
            set_desktop_settings,
            pick_working_folder,
            pick_folder,
            desktop_notify,
            show_main_window,
            open_app_window,
            installed::install_app,
            installed::uninstall_app,
            installed::list_installed_apps,
            installed::set_app_tray_allowed,
            installed::set_app_tray,
            installed::app_notify,
            hide_quick_ask,
            fit_quick_ask,
            overlay_stop,
            get_saved_session,
            save_session,
            clear_session,
            test_notification,
            update::check_for_update,
            update::install_update,
        ])
        .build(tauri::generate_context!())
        .expect("error while building the Cognitrix desktop app");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            engine::stop(app);
        }
    });
}
