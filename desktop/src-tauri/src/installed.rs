//! Apps agents built, installed on this computer like ordinary apps.
//!
//! "Install as app" on an app's page makes an OS shortcut that opens the app
//! in a window of its own: a Start menu entry (and, if asked, a desktop
//! shortcut) on Windows, a small launcher in ~/Applications/Cognitrix Apps on
//! macOS, a .desktop entry on Linux. The shortcut starts Cognitrix with
//! `--open-app=<slug>` (macOS opens `cognitrix://app/<slug>`), which reaches
//! the running app through the single-instance plugin or the deep link.
//! When Cognitrix wasn't running, the main window stays in the tray and the
//! app's window opens once the engine is up.
//!
//! An installed app may also get its own tray (menu bar) icon, if the person
//! allows it. Whether it may is kept here, per computer; what the app may do
//! with the person's account (notifications, connected apps) is kept by the
//! engine, per person. Everything an app asks for goes through the
//! Cognitrix page around it (/apps/open/<slug>), never the app's own code:
//! the app runs in a sandboxed frame that can't call the shell.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, Url, WebviewUrl, WebviewWindowBuilder};

pub const OPEN_APP_FLAG: &str = "--open-app=";
const MAX_TRAY_ITEMS: usize = 12;
const MAX_ICON_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TrayEntry {
    pub id: String,
    pub label: String,
}

/// What the app put in its tray menu (`cognitrix.tray.set` in the app).
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TrayConfig {
    pub tooltip: String,
    /// Text next to the icon in the macOS menu bar (other systems ignore it).
    pub title: String,
    pub items: Vec<TrayEntry>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct InstalledApp {
    pub slug: String,
    pub title: String,
    /// The files and folders the install made, removed again on uninstall.
    pub shortcuts: Vec<String>,
    pub desktop_shortcut: bool,
    /// The person let this app put an icon in the tray (menu bar).
    pub tray_allowed: bool,
    /// The person was asked about the tray (and said no, if not allowed).
    pub tray_asked: bool,
    pub tray: Option<TrayConfig>,
    pub installed_at: u64,
}

// ---------------------------------------------------------------- storage

static LOCK: Mutex<()> = Mutex::new(());

fn list_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|dir| dir.join("installed-apps.json"))
}

fn icons_dir(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_local_data_dir().ok().map(|dir| dir.join("app-icons"))
}

fn icon_png_path(app: &AppHandle, slug: &str) -> Option<PathBuf> {
    icons_dir(app).map(|dir| dir.join(format!("{slug}.png")))
}

pub fn load(app: &AppHandle) -> Vec<InstalledApp> {
    list_path(app)
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<Vec<InstalledApp>>(&text).ok())
        .unwrap_or_default()
        .into_iter()
        .filter(|a| valid_slug(&a.slug))
        .collect()
}

fn save(app: &AppHandle, apps: &[InstalledApp]) -> Result<(), String> {
    let path = list_path(app).ok_or("No config folder for this app")?;
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(apps).map_err(|e| e.to_string())?;
    fs::write(path, text).map_err(|e| e.to_string())
}

fn find(app: &AppHandle, slug: &str) -> Option<InstalledApp> {
    load(app).into_iter().find(|a| a.slug == slug)
}

/// Change one installed app's record and save the list.
fn update<F: FnOnce(&mut InstalledApp)>(app: &AppHandle, slug: &str, change: F) -> Result<InstalledApp, String> {
    let _guard = LOCK.lock().map_err(|_| "busy")?;
    let mut apps = load(app);
    let row = apps.iter_mut().find(|a| a.slug == slug).ok_or("This app isn't installed on this computer")?;
    change(row);
    let out = row.clone();
    save(app, &apps)?;
    Ok(out)
}

pub fn valid_slug(slug: &str) -> bool {
    !slug.is_empty()
        && slug.len() <= 48
        && slug.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !slug.starts_with('-')
}

fn clean_title(title: &str, slug: &str) -> String {
    let title: String = title.chars().filter(|c| !c.is_control()).take(80).collect();
    let title = title.trim().to_string();
    if title.is_empty() { slug.to_string() } else { title }
}

/// A title as a file name: no path or reserved characters, no trailing dots.
pub fn file_name(title: &str, slug: &str) -> String {
    let cleaned: String = title
        .chars()
        .map(|c| if c.is_control() || "<>:\"/\\|?*".contains(c) { ' ' } else { c })
        .collect();
    let cleaned = cleaned.split_whitespace().collect::<Vec<_>>().join(" ");
    let cleaned: String = cleaned.chars().take(64).collect();
    let cleaned = cleaned.trim_matches(|c: char| c == '.' || c == ' ').to_string();
    let reserved = ["con", "prn", "aux", "nul", "com1", "lpt1"].contains(&cleaned.to_lowercase().as_str());
    if cleaned.is_empty() || reserved { slug.to_string() } else { cleaned }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ---------------------------------------------------------------- icons

pub fn is_png(bytes: &[u8]) -> bool {
    bytes.len() > 8 && bytes.len() <= MAX_ICON_BYTES && bytes.starts_with(&[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a])
}

/// A Windows .ico holding one PNG (Windows Vista and later read these).
#[cfg_attr(not(windows), allow(dead_code))]
pub fn ico_from_png(png: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(png.len() + 22);
    out.extend_from_slice(&[0, 0, 1, 0, 1, 0]); // reserved, type 1 (icon), one image
    out.extend_from_slice(&[0, 0, 0, 0]); // 256 x 256, no palette, reserved
    out.extend_from_slice(&1u16.to_le_bytes()); // planes
    out.extend_from_slice(&32u16.to_le_bytes()); // bits per pixel
    out.extend_from_slice(&(png.len() as u32).to_le_bytes());
    out.extend_from_slice(&22u32.to_le_bytes()); // where the image starts
    out.extend_from_slice(png);
    out
}

/// A macOS .icns holding one 256 x 256 PNG ("ic08").
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn icns_from_png(png: &[u8]) -> Vec<u8> {
    let entry = (png.len() + 8) as u32;
    let mut out = Vec::with_capacity(png.len() + 16);
    out.extend_from_slice(b"icns");
    out.extend_from_slice(&(entry + 8).to_be_bytes());
    out.extend_from_slice(b"ic08");
    out.extend_from_slice(&entry.to_be_bytes());
    out.extend_from_slice(png);
    out
}

fn app_icon(app: &AppHandle, slug: &str) -> Option<tauri::image::Image<'static>> {
    let bytes = fs::read(icon_png_path(app, slug)?).ok()?;
    tauri::image::Image::from_bytes(&bytes).ok().map(|i| i.to_owned())
}

// ---------------------------------------------------------------- shortcuts

/// Only ever remove what an install made, inside the folders installs use.
fn removable(app: &AppHandle, path: &Path) -> bool {
    let roots = platform::roots(app);
    path.components().all(|c| !matches!(c, std::path::Component::ParentDir))
        && roots.iter().any(|root| path.starts_with(root) && path != root.as_path())
}

fn remove_shortcuts(app: &AppHandle, record: &InstalledApp) {
    for item in &record.shortcuts {
        let path = PathBuf::from(item);
        if !removable(app, &path) {
            log::warn!("not removing {item}: outside the shortcut folders");
            continue;
        }
        let result = if path.is_dir() { fs::remove_dir_all(&path) } else { fs::remove_file(&path) };
        if let Err(e) = result {
            if e.kind() != std::io::ErrorKind::NotFound {
                log::warn!("could not remove {item}: {e}");
            }
        }
    }
    platform::after_change(app);
}

/// The file name for this app's shortcut, kept apart from other installed
/// apps that have the same title.
fn shortcut_name(others: &[InstalledApp], slug: &str, title: &str) -> String {
    let name = file_name(title, slug);
    let taken = others.iter().any(|a| a.slug != slug && file_name(&a.title, &a.slug).eq_ignore_ascii_case(&name));
    if taken { format!("{name} ({slug})") } else { name }
}

#[derive(Serialize)]
pub struct InstalledView {
    slug: String,
    title: String,
    desktop_shortcut: bool,
    tray_allowed: bool,
    tray_asked: bool,
    installed_at: u64,
    /// Where to find it, for the person: "Start menu › Cognitrix Apps".
    place: String,
}

fn view(record: &InstalledApp) -> InstalledView {
    InstalledView {
        slug: record.slug.clone(),
        title: record.title.clone(),
        desktop_shortcut: record.desktop_shortcut,
        tray_allowed: record.tray_allowed,
        tray_asked: record.tray_asked,
        installed_at: record.installed_at,
        place: platform::PLACE.to_string(),
    }
}

// ---------------------------------------------------------------- commands

/// Install (or reinstall, after a rename) an app as a shortcut. `icon` is a
/// 256 x 256 PNG the page drew. Async: PowerShell makes the Windows shortcut.
#[tauri::command]
pub async fn install_app(
    app: AppHandle,
    slug: String,
    title: String,
    icon: Vec<u8>,
    desktop: Option<bool>,
) -> Result<InstalledView, String> {
    if !valid_slug(&slug) {
        return Err("Not an app name".into());
    }
    if !is_png(&icon) {
        return Err("The app's icon isn't a PNG image".into());
    }
    let title = clean_title(&title, &slug);
    let desktop = desktop.unwrap_or(false) && platform::HAS_DESKTOP_SHORTCUT;
    let existing = load(&app);
    let previous = existing.iter().find(|a| a.slug == slug).cloned();
    if let Some(previous) = &previous {
        remove_shortcuts(&app, previous);
    }
    let png_path = icon_png_path(&app, &slug).ok_or("No data folder for this app")?;
    if let Some(dir) = png_path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    fs::write(&png_path, &icon).map_err(|e| e.to_string())?;
    let name = shortcut_name(&existing, &slug, &title);
    let made = platform::write_shortcuts(&app, &slug, &title, &name, &icon, &png_path, desktop)?;
    let record = {
        let _guard = LOCK.lock().map_err(|_| "busy")?;
        let mut apps = load(&app);
        apps.retain(|a| a.slug != slug);
        let mut record = previous.unwrap_or_default();
        record.slug = slug.clone();
        record.title = title;
        record.shortcuts = made.iter().map(|p| p.display().to_string()).collect();
        record.desktop_shortcut = desktop;
        if record.installed_at == 0 {
            record.installed_at = now_secs();
        }
        apps.push(record.clone());
        save(&app, &apps)?;
        record
    };
    refresh_tray(&app, &record);
    if let Some(window) = app.get_webview_window(&window_label(&slug)) {
        if let Some(icon) = app_icon(&app, &slug) {
            let _ = window.set_icon(icon);
        }
    }
    Ok(view(&record))
}

#[tauri::command]
pub async fn uninstall_app(app: AppHandle, slug: String) -> Result<(), String> {
    if !valid_slug(&slug) {
        return Err("Not an app name".into());
    }
    let record = {
        let _guard = LOCK.lock().map_err(|_| "busy")?;
        let mut apps = load(&app);
        let record = apps.iter().find(|a| a.slug == slug).cloned();
        apps.retain(|a| a.slug != slug);
        save(&app, &apps)?;
        record
    };
    let _ = app.remove_tray_by_id(&tray_id(&slug));
    if let Some(record) = record {
        remove_shortcuts(&app, &record);
    }
    if let Some(dir) = icons_dir(&app) {
        for ext in ["png", "ico", "icns"] {
            let _ = fs::remove_file(dir.join(format!("{slug}.{ext}")));
        }
    }
    Ok(())
}

#[tauri::command]
pub fn list_installed_apps(app: AppHandle) -> Vec<InstalledView> {
    load(&app).iter().map(view).collect()
}

/// The person's answer about this app's tray icon.
#[tauri::command]
pub fn set_app_tray_allowed(app: AppHandle, slug: String, allowed: bool) -> Result<InstalledView, String> {
    let record = update(&app, &slug, |r| {
        r.tray_allowed = allowed;
        r.tray_asked = true;
    })?;
    refresh_tray(&app, &record);
    Ok(view(&record))
}

/// What the app wants in its tray menu; None puts it back to just Open.
/// Kept even while the tray is off, so allowing it later shows the app's menu.
#[tauri::command]
pub fn set_app_tray(app: AppHandle, slug: String, tray: Option<TrayConfig>) -> Result<InstalledView, String> {
    let tray = tray.map(clean_tray);
    let record = update(&app, &slug, |r| r.tray = tray)?;
    refresh_tray(&app, &record);
    Ok(view(&record))
}

fn clean_text(text: &str, max: usize) -> String {
    text.chars().filter(|c| !c.is_control()).take(max).collect::<String>().trim().to_string()
}

pub fn clean_tray(tray: TrayConfig) -> TrayConfig {
    let mut items = Vec::new();
    for item in tray.items {
        let id = clean_text(&item.id, 64);
        let label = clean_text(&item.label, 60);
        let id_ok = !id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c));
        if id_ok && !label.is_empty() && !items.iter().any(|i: &TrayEntry| i.id == id) {
            items.push(TrayEntry { id, label });
        }
        if items.len() == MAX_TRAY_ITEMS {
            break;
        }
    }
    TrayConfig { tooltip: clean_text(&tray.tooltip, 120), title: clean_text(&tray.title, 24), items }
}

/// A notification from an app (with the person's permission, which the page
/// around the app checked). Clicking it opens the app's window.
#[tauri::command]
pub async fn app_notify(app: AppHandle, slug: String, app_title: String, title: String, body: String) -> bool {
    if !valid_slug(&slug) {
        return false;
    }
    let app_title = clean_text(&app_title, 80);
    let title = clean_text(&title, 120);
    let heading = match (app_title.is_empty(), title.is_empty()) {
        (false, false) if app_title != title => format!("{app_title} · {title}"),
        (false, true) => app_title,
        _ => title,
    };
    let body: String = body.chars().take(400).collect();
    crate::notify::show(
        &app,
        crate::notify::Notice { title: &heading, body: &body, path: None, urgent: false, approval: None, app: Some(slug) },
    )
}

// ---------------------------------------------------------------- tray

fn tray_id(slug: &str) -> String {
    format!("app-{slug}")
}

fn menu_id(slug: &str, what: &str) -> String {
    format!("app:{slug}:{what}")
}

fn tray_menu(app: &AppHandle, record: &InstalledApp) -> tauri::Result<Menu<tauri::Wry>> {
    let slug = &record.slug;
    let menu = Menu::new(app)?;
    menu.append(&MenuItem::with_id(app, menu_id(slug, "open"), format!("Open {}", record.title), true, None::<&str>)?)?;
    let items = record.tray.as_ref().map(|t| t.items.clone()).unwrap_or_default();
    if !items.is_empty() {
        menu.append(&PredefinedMenuItem::separator(app)?)?;
        for item in items {
            let label = if cfg!(windows) { item.label.replace('&', "&&") } else { item.label };
            menu.append(&MenuItem::with_id(app, menu_id(slug, &format!("item:{}", item.id)), label, true, None::<&str>)?)?;
        }
    }
    menu.append(&PredefinedMenuItem::separator(app)?)?;
    menu.append(&MenuItem::with_id(app, menu_id(slug, "hide"), "Remove from tray", true, None::<&str>)?)?;
    Ok(menu)
}

/// Show, update or remove an app's tray icon to match its record.
fn refresh_tray(app: &AppHandle, record: &InstalledApp) {
    let id = tray_id(&record.slug);
    if !record.tray_allowed {
        let _ = app.remove_tray_by_id(&id);
        return;
    }
    let menu = match tray_menu(app, record) {
        Ok(menu) => menu,
        Err(e) => {
            log::warn!("could not make the tray menu for {}: {e}", record.slug);
            return;
        }
    };
    let tooltip = record
        .tray
        .as_ref()
        .map(|t| t.tooltip.clone())
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| record.title.clone());
    let title = record.tray.as_ref().map(|t| t.title.clone()).filter(|t| !t.is_empty());
    if let Some(tray) = app.tray_by_id(&id) {
        let _ = tray.set_menu(Some(menu));
        let _ = tray.set_tooltip(Some(tooltip));
        #[cfg(target_os = "macos")]
        let _ = tray.set_title(title);
        #[cfg(not(target_os = "macos"))]
        let _ = title;
        return;
    }
    let slug = record.slug.clone();
    let mut builder = TrayIconBuilder::with_id(&id)
        .tooltip(tooltip)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(move |tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                open_app(tray.app_handle(), slug.clone(), None, None);
            }
        });
    #[cfg(target_os = "macos")]
    if let Some(title) = title {
        builder = builder.title(title);
    }
    #[cfg(not(target_os = "macos"))]
    let _ = title;
    match app_icon(app, &record.slug).or_else(|| app.default_window_icon().cloned()) {
        Some(icon) => builder = builder.icon(icon),
        None => {}
    }
    if let Err(e) = builder.build(app) {
        log::warn!("could not add {} to the tray: {e}", record.slug);
    }
}

/// Menu clicks from every app's tray icon (menu events are app-wide, so
/// one handler serves them all).
fn on_menu(app: &AppHandle, event: MenuEvent) {
    let Some(rest) = event.id().as_ref().strip_prefix("app:") else {
        return;
    };
    let Some((slug, what)) = rest.split_once(':') else {
        return;
    };
    if !valid_slug(slug) {
        return;
    }
    let slug = slug.to_string();
    match what {
        "open" => open_app(app, slug, None, None),
        "hide" => {
            if let Ok(record) = update(app, &slug, |r| {
                r.tray_allowed = false;
                r.tray_asked = true;
            }) {
                refresh_tray(app, &record);
            }
        }
        other => {
            if let Some(item) = other.strip_prefix("item:") {
                tray_click(app, slug, item.to_string());
            }
        }
    }
}

#[derive(Clone, Serialize)]
struct TrayClick {
    slug: String,
    id: String,
}

/// A click on one of the app's own menu items: the app hears about it in its
/// window, which opens first if it was closed.
fn tray_click(app: &AppHandle, slug: String, id: String) {
    let label = window_label(&slug);
    if let Some(window) = app.get_webview_window(&label) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        let _ = app.emit_to(label.as_str(), "app-tray-click", TrayClick { slug, id });
    } else {
        open_app(app, slug, None, Some(id));
    }
}

// ---------------------------------------------------------------- windows

pub fn window_label(slug: &str) -> String {
    format!("app-{slug}")
}

struct Pending {
    slug: String,
    title: Option<String>,
    tray_item: Option<String>,
}

// Apps asked for before the engine was up (a shortcut that started Cognitrix).
static PENDING: Mutex<Vec<Pending>> = Mutex::new(Vec::new());

fn engine_base(app: &AppHandle) -> Option<Url> {
    let status = app.state::<crate::engine::Engine>().status();
    if !matches!(status.status, crate::engine::Status::Running) {
        return None;
    }
    status.url.and_then(|u| Url::parse(&u).ok())
}

/// Open an app in its own window, or bring its window forward. Safe to call
/// from any thread or event handler: the window is made on the async runtime
/// (making a WebView2 window inside a synchronous handler deadlocks on Windows).
pub fn open_app(app: &AppHandle, slug: String, title: Option<String>, tray_item: Option<String>) {
    if !valid_slug(&slug) {
        return;
    }
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = open_app_now(&handle, slug.clone(), title, tray_item) {
            log::warn!("could not open the app {slug}: {e}");
        }
    });
}

pub fn open_app_now(app: &AppHandle, slug: String, title: Option<String>, tray_item: Option<String>) -> Result<(), String> {
    if !valid_slug(&slug) {
        return Err("Not an app name".into());
    }
    let label = window_label(&slug);
    if let Some(existing) = app.get_webview_window(&label) {
        let _ = existing.unminimize();
        let _ = existing.show();
        let _ = existing.set_focus();
        if let Some(id) = tray_item {
            let _ = app.emit_to(label.as_str(), "app-tray-click", TrayClick { slug, id });
        }
        return Ok(());
    }
    let Some(mut url) = engine_base(app) else {
        if let Ok(mut pending) = PENDING.lock() {
            pending.retain(|p| p.slug != slug);
            pending.push(Pending { slug, title, tray_item });
        }
        return Ok(());
    };
    url.set_path(&format!("/apps/open/{slug}"));
    let mut query = String::from("window=1");
    if let Some(id) = &tray_item {
        let safe: String = id.chars().filter(|c| c.is_ascii_alphanumeric() || "-_.".contains(*c)).take(64).collect();
        query.push_str(&format!("&tray={safe}"));
    }
    url.set_query(Some(&query));
    url.set_fragment(None);
    let record = find(app, &slug);
    let name = title
        .map(|t| clean_title(&t, &slug))
        .or_else(|| record.as_ref().map(|r| r.title.clone()))
        .unwrap_or_else(|| slug.clone());
    let handle = app.clone();
    let mut builder = WebviewWindowBuilder::new(app, label, WebviewUrl::External(url))
        .title(name.chars().take(120).collect::<String>())
        .inner_size(1280.0, 820.0)
        .min_inner_size(480.0, 360.0)
        .center()
        .background_color(tauri::window::Color(11, 12, 14, 255))
        .additional_browser_args(crate::BROWSER_ARGS)
        .on_navigation({
            let handle = handle.clone();
            move |url| crate::allow_navigation(&handle, url)
        })
        .on_new_window(move |url, _| crate::open_new_window(&handle, &url));
    if record.is_some() {
        if let Some(icon) = app_icon(app, &slug) {
            builder = builder.icon(icon).map_err(|e| e.to_string())?;
        }
    }
    builder.build().map(|_| ()).map_err(|e| e.to_string())
}

/// The engine answers: open the apps that were waiting for it.
pub fn on_engine_ready(app: &AppHandle) {
    let waiting: Vec<Pending> = PENDING.lock().map(|mut p| p.drain(..).collect()).unwrap_or_default();
    for p in waiting {
        open_app(app, p.slug, p.title, p.tray_item);
    }
}

/// `--open-app=<slug>` among a launch's arguments.
pub fn slug_from_args<I: IntoIterator<Item = S>, S: AsRef<str>>(args: I) -> Option<String> {
    args.into_iter()
        .find_map(|a| a.as_ref().strip_prefix(OPEN_APP_FLAG).map(str::to_owned))
        .filter(|s| valid_slug(s))
}

/// `cognitrix://app/<slug>`.
pub fn slug_from_link(url: &str) -> Option<String> {
    let rest = url.strip_prefix("cognitrix://app/")?;
    let slug = rest.split(['?', '#', '/']).next().unwrap_or("");
    Some(slug.to_string()).filter(|s| valid_slug(s))
}

/// Called once at startup: tray icons for the installed apps that may have one.
pub fn init(app: &AppHandle) {
    app.on_menu_event(on_menu);
    for record in load(app) {
        if record.tray_allowed {
            refresh_tray(app, &record);
        }
    }
}

// ---------------------------------------------------------------- per OS

#[cfg(windows)]
mod platform {
    use super::*;
    use std::os::windows::process::CommandExt;
    use std::process::Command;

    pub const PLACE: &str = "Start menu › Cognitrix Apps";
    pub const HAS_DESKTOP_SHORTCUT: bool = true;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    fn start_menu(app: &AppHandle) -> Option<PathBuf> {
        app.path().data_dir().ok().map(|d| d.join(r"Microsoft\Windows\Start Menu\Programs\Cognitrix Apps"))
    }

    pub fn roots(app: &AppHandle) -> Vec<PathBuf> {
        [start_menu(app), app.path().desktop_dir().ok()].into_iter().flatten().collect()
    }

    /// WScript.Shell writes the .lnk. Paths and names go in through
    /// environment variables, never into the script text.
    fn make_link(link: &Path, target: &Path, args: &str, icon: &Path, description: &str) -> Result<(), String> {
        let script = "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CX_LINK); \
            $s.TargetPath = $env:CX_TARGET; $s.Arguments = $env:CX_ARGS; \
            $s.IconLocation = $env:CX_ICON + ',0'; $s.Description = $env:CX_DESC; \
            $s.WorkingDirectory = $env:CX_DIR; $s.Save()";
        let dir = target.parent().map(|d| d.display().to_string()).unwrap_or_default();
        let out = Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
            .env("CX_LINK", link)
            .env("CX_TARGET", target)
            .env("CX_ARGS", args)
            .env("CX_ICON", icon)
            .env("CX_DESC", description)
            .env("CX_DIR", dir)
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| format!("Could not run PowerShell to make the shortcut: {e}"))?;
        if out.status.success() && link.exists() {
            Ok(())
        } else {
            let err = String::from_utf8_lossy(&out.stderr);
            Err(format!("Windows didn't make the shortcut. {}", err.lines().next().unwrap_or("").trim()))
        }
    }

    pub fn write_shortcuts(
        app: &AppHandle,
        slug: &str,
        title: &str,
        name: &str,
        icon: &[u8],
        png_path: &Path,
        desktop: bool,
    ) -> Result<Vec<PathBuf>, String> {
        let exe = tauri::utils::platform::current_exe().map_err(|e| e.to_string())?;
        let ico = png_path.with_extension("ico");
        fs::write(&ico, ico_from_png(icon)).map_err(|e| e.to_string())?;
        let folder = start_menu(app).ok_or("No Start menu folder")?;
        fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
        let args = format!("{OPEN_APP_FLAG}{slug}");
        let description = format!("{title}, an app in Cognitrix");
        let mut made = Vec::new();
        let link = folder.join(format!("{name}.lnk"));
        make_link(&link, &exe, &args, &ico, &description)?;
        made.push(link);
        if desktop {
            if let Ok(dir) = app.path().desktop_dir() {
                let link = dir.join(format!("{name}.lnk"));
                match make_link(&link, &exe, &args, &ico, &description) {
                    Ok(()) => made.push(link),
                    Err(e) => log::warn!("no desktop shortcut for {slug}: {e}"),
                }
            }
        }
        Ok(made)
    }

    pub fn after_change(app: &AppHandle) {
        // Drop the Cognitrix Apps folder from the Start menu once it's empty.
        if let Some(folder) = start_menu(app) {
            let _ = fs::remove_dir(folder);
        }
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use super::*;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    pub const PLACE: &str = "Applications › Cognitrix Apps";
    pub const HAS_DESKTOP_SHORTCUT: bool = false;

    fn folder(app: &AppHandle) -> Option<PathBuf> {
        app.path().home_dir().ok().map(|h| h.join("Applications").join("Cognitrix Apps"))
    }

    pub fn roots(app: &AppHandle) -> Vec<PathBuf> {
        folder(app).into_iter().collect()
    }

    fn xml(text: &str) -> String {
        text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
    }

    /// A tiny .app whose only job is to open cognitrix://app/<slug>: the
    /// running Cognitrix (or a fresh one) opens the app's window.
    pub fn write_shortcuts(
        app: &AppHandle,
        slug: &str,
        title: &str,
        name: &str,
        icon: &[u8],
        _png_path: &Path,
        _desktop: bool,
    ) -> Result<Vec<PathBuf>, String> {
        let root = folder(app).ok_or("No home folder")?;
        let bundle = root.join(format!("{name}.app"));
        let contents = bundle.join("Contents");
        let macos = contents.join("MacOS");
        let resources = contents.join("Resources");
        fs::create_dir_all(&macos).map_err(|e| e.to_string())?;
        fs::create_dir_all(&resources).map_err(|e| e.to_string())?;
        let launcher = macos.join("launch");
        fs::write(&launcher, format!("#!/bin/sh\nexec /usr/bin/open \"cognitrix://app/{slug}\"\n"))
            .map_err(|e| e.to_string())?;
        fs::set_permissions(&launcher, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
        fs::write(resources.join("icon.icns"), icns_from_png(icon)).map_err(|e| e.to_string())?;
        let plist = format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIdentifier</key><string>com.theonlyamos.cognitrix.app.{slug}</string>
  <key>CFBundleName</key><string>{name}</string>
  <key>CFBundleDisplayName</key><string>{title}</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
"#,
            slug = slug,
            name = xml(name),
            title = xml(title),
        );
        fs::write(contents.join("Info.plist"), plist).map_err(|e| e.to_string())?;
        // Finder caches icons; touching the bundle makes it look again.
        let _ = std::process::Command::new("/usr/bin/touch").arg(&bundle).status();
        Ok(vec![bundle])
    }

    pub fn after_change(app: &AppHandle) {
        if let Some(folder) = folder(app) {
            let _ = fs::remove_dir(folder);
        }
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
mod platform {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    pub const PLACE: &str = "your apps menu";
    pub const HAS_DESKTOP_SHORTCUT: bool = true;

    fn applications(app: &AppHandle) -> Option<PathBuf> {
        app.path().data_dir().ok().map(|d| d.join("applications"))
    }

    pub fn roots(app: &AppHandle) -> Vec<PathBuf> {
        [applications(app), app.path().desktop_dir().ok()].into_iter().flatten().collect()
    }

    /// The program to start: the AppImage itself when running from one.
    fn program() -> Result<PathBuf, String> {
        if let Ok(image) = std::env::var("APPIMAGE") {
            if !image.is_empty() {
                return Ok(PathBuf::from(image));
            }
        }
        tauri::utils::platform::current_exe().map_err(|e| e.to_string())
    }

    pub fn desktop_entry(program: &Path, slug: &str, title: &str, icon: &Path) -> String {
        // Exec quoting (Desktop Entry spec): quote the path, backslash-escape
        // " ` $ \ inside it, then escape backslashes again as a string value.
        let quoted: String = program
            .display()
            .to_string()
            .chars()
            .flat_map(|c| match c {
                '"' | '`' | '$' | '\\' => vec!['\\', c],
                '%' => vec!['%', '%'],
                _ => vec![c],
            })
            .collect();
        let exec = format!("\"{}\" {OPEN_APP_FLAG}{slug}", quoted).replace('\\', "\\\\");
        let one_line = |s: &str| s.replace(['\n', '\r'], " ");
        format!(
            "[Desktop Entry]\nType=Application\nVersion=1.0\nName={}\nComment=An app in Cognitrix\nExec={}\nIcon={}\nTerminal=false\nCategories=Utility;\nStartupNotify=false\n",
            one_line(title),
            exec,
            one_line(&icon.display().to_string()),
        )
    }

    pub fn write_shortcuts(
        app: &AppHandle,
        slug: &str,
        title: &str,
        _name: &str,
        _icon: &[u8],
        png_path: &Path,
        desktop: bool,
    ) -> Result<Vec<PathBuf>, String> {
        let text = desktop_entry(&program()?, slug, title, png_path);
        let folder = applications(app).ok_or("No applications folder")?;
        fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
        let file = format!("cognitrix-app-{slug}.desktop");
        let entry = folder.join(&file);
        fs::write(&entry, &text).map_err(|e| e.to_string())?;
        let _ = fs::set_permissions(&entry, fs::Permissions::from_mode(0o755));
        let mut made = vec![entry];
        if desktop {
            if let Ok(dir) = app.path().desktop_dir() {
                let link = dir.join(&file);
                if fs::create_dir_all(&dir).is_ok() && fs::write(&link, &text).is_ok() {
                    let _ = fs::set_permissions(&link, fs::Permissions::from_mode(0o755));
                    made.push(link);
                }
            }
        }
        after_change(app);
        Ok(made)
    }

    pub fn after_change(app: &AppHandle) {
        if let Some(folder) = applications(app) {
            let _ = std::process::Command::new("update-desktop-database")
                .arg(folder)
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs_and_links() {
        assert!(valid_slug("support-queue") && valid_slug("a1"));
        assert!(!valid_slug("") && !valid_slug("Bad") && !valid_slug("a/b") && !valid_slug("-x"));
        assert_eq!(slug_from_args(["cognitrix.exe", "--open-app=board"]), Some("board".into()));
        assert_eq!(slug_from_args(["x", "--open-app=../evil"]), None);
        assert_eq!(slug_from_args(["x", "--minimized"]), None);
        assert_eq!(slug_from_link("cognitrix://app/board"), Some("board".into()));
        assert_eq!(slug_from_link("cognitrix://app/board/?x=1"), Some("board".into()));
        assert_eq!(slug_from_link("cognitrix://app/Evil"), None);
        assert_eq!(slug_from_link("cognitrix://pair?x"), None);
    }

    #[test]
    fn file_names_are_safe() {
        assert_eq!(file_name("Support queue", "s"), "Support queue");
        assert_eq!(file_name("a/b:c*?", "s"), "a b c");
        assert_eq!(file_name("...", "slug"), "slug");
        assert_eq!(file_name("CON", "slug"), "slug");
        assert_eq!(file_name("x\n\ty", "s"), "x y");
        let other = InstalledApp { slug: "b".into(), title: "Board".into(), ..Default::default() };
        assert_eq!(shortcut_name(&[other.clone()], "a", "Board"), "Board (a)");
        assert_eq!(shortcut_name(&[other], "b", "Board"), "Board");
    }

    #[test]
    fn icon_files_wrap_the_png() {
        let png = [0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3];
        assert!(is_png(&png) && !is_png(b"GIF89a....."));
        let ico = ico_from_png(&png);
        assert_eq!(&ico[..6], &[0, 0, 1, 0, 1, 0]);
        assert_eq!(u32::from_le_bytes(ico[14..18].try_into().unwrap()), png.len() as u32);
        assert_eq!(&ico[22..], &png);
        let icns = icns_from_png(&png);
        assert_eq!(&icns[..4], b"icns");
        assert_eq!(u32::from_be_bytes(icns[4..8].try_into().unwrap()) as usize, icns.len());
        assert_eq!(&icns[8..12], b"ic08");
        assert_eq!(&icns[16..], &png);
    }

    #[test]
    fn tray_menus_are_cleaned() {
        let tray = clean_tray(TrayConfig {
            tooltip: "Inbox: 3".into(),
            title: "3 new messages waiting here".into(),
            items: (0..20)
                .map(|i| TrayEntry { id: if i == 1 { "bad id!".into() } else { format!("i{}", i % 15) }, label: format!("Item {i}") })
                .collect(),
        });
        assert_eq!(tray.title.chars().count(), 24);
        assert_eq!(tray.items.len(), MAX_TRAY_ITEMS);
        assert!(tray.items.iter().all(|i| i.id != "bad id!"));
        let mut ids: Vec<_> = tray.items.iter().map(|i| i.id.clone()).collect();
        ids.dedup();
        assert_eq!(ids.len(), MAX_TRAY_ITEMS);
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    #[test]
    fn desktop_entries_quote_the_program() {
        let text = platform::desktop_entry(Path::new("/opt/My $App/cognitrix"), "board", "Board\nX", Path::new("/i.png"));
        assert!(text.contains("Exec=\"/opt/My \\\\$App/cognitrix\" --open-app=board\n"), "{text}");
        assert!(text.contains("Name=Board X\n"));
    }
}
