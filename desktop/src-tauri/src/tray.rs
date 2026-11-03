//! Tray icon and its menu: open, quick ask, new chat, launch at startup,
//! restart engine, quit.

use std::sync::Mutex;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Wry};

use crate::engine::{self, EngineStatus, Status};

/// The menu's first line shows the engine state; kept so it can be updated.
pub struct TrayStatus(pub Mutex<Option<MenuItem<Wry>>>);

/// The "Launch at startup" tick, kept so Settings can update it too.
pub struct TrayAutostart(pub Mutex<Option<CheckMenuItem<Wry>>>);

pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "Engine starting…", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open Cognitrix", true, None::<&str>)?;
    let quick = MenuItem::with_id(app, "quick", "Quick ask\tAlt+Space", true, None::<&str>)?;
    let chat = MenuItem::with_id(app, "new-chat", "New chat", true, None::<&str>)?;
    let inbox = MenuItem::with_id(app, "inbox", "Inbox", true, None::<&str>)?;
    let autostart = CheckMenuItem::with_id(
        app,
        "autostart",
        "Launch at startup",
        true,
        crate::autostart_enabled(app),
        None::<&str>,
    )?;
    let restart = MenuItem::with_id(app, "restart", "Restart engine", true, None::<&str>)?;
    let logs = MenuItem::with_id(app, "logs", "Open engine log", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Cognitrix", true, None::<&str>)?;
    let separator = || PredefinedMenuItem::separator(app);
    let menu = Menu::with_items(
        app,
        &[
            &status,
            &separator()?,
            &open,
            &quick,
            &chat,
            &inbox,
            &separator()?,
            &autostart,
            &separator()?,
            &restart,
            &logs,
            &separator()?,
            &quit,
        ],
    )?;
    app.manage(TrayStatus(Mutex::new(Some(status))));
    app.manage(TrayAutostart(Mutex::new(Some(autostart))));

    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip("Cognitrix")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => crate::show_main(app, None),
            "quick" => crate::toggle_quick_ask(app),
            "new-chat" => crate::show_main(app, Some("/home".into())),
            "inbox" => crate::show_main(app, Some("/inbox".into())),
            "autostart" => {
                let enable = !crate::autostart_enabled(app);
                if let Err(e) = crate::set_autostart(app, enable) {
                    log::warn!("could not change launch at startup: {e}");
                }
                set_autostart_tick(app, crate::autostart_enabled(app));
            }
            "restart" => engine::restart(app),
            "logs" => crate::open_engine_log(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                crate::show_main(tray.app_handle(), None);
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

pub fn refresh(app: &AppHandle, status: &EngineStatus) {
    let text = match status.status {
        Status::Starting => "Engine starting…".to_string(),
        Status::Running => format!("Engine running · port {}", status.port),
        Status::Stopped => "Engine stopped".to_string(),
        Status::Failed => "Engine failed to start".to_string(),
    };
    if let Some(state) = app.try_state::<TrayStatus>() {
        if let Some(item) = state.0.lock().unwrap().as_ref() {
            let _ = item.set_text(&text);
        }
    }
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(format!("Cognitrix · {}", text.to_lowercase())));
    }
}

pub fn set_autostart_tick(app: &AppHandle, checked: bool) {
    if let Some(state) = app.try_state::<TrayAutostart>() {
        if let Some(item) = state.0.lock().unwrap().as_ref() {
            let _ = item.set_checked(checked);
        }
    }
}
