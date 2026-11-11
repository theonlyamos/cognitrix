//! On-screen agent cursor: shows where an agent clicks and types in other apps
//! (background computer use, cognitrix/computer), with a Stop button.
//!
//! The engine streams agent actions on /api/v1/computer/overlay/events, guarded
//! by the token the app gave it (see engine.rs). Two windows draw them:
//! - "agent-cursor": transparent, click-through, never focused, over every
//!   monitor; draws a pointer that glides to each action, a click ripple and a
//!   label like "Researcher · Clicking Save in Notepad".
//! - "agent-stop": a small pill at the top of the main monitor that can be
//!   clicked without taking focus; Stop keeps the agent off the computer and
//!   stops its chat turn.
//! Both hide after a few idle seconds and are left out of screenshots, so the
//! agent never sees its own cursor. Settings → "Show the agent cursor" turns
//! them off.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::engine::{self, Status};
use crate::settings;

const CURSOR: &str = "agent-cursor";
const STOP: &str = "agent-stop";
const FEED_PATH: &str = "/api/v1/computer/overlay/events";
const STOP_PATH: &str = "/api/v1/computer/overlay/stop";
/// Hide the cursor and the Stop pill this long after the last action.
const IDLE_HIDE: Duration = Duration::from_secs(4);
/// The engine pings every 15 s; no data for this long means the feed died.
const FEED_TIMEOUT: Duration = Duration::from_secs(45);
const STOP_WIDTH: f64 = 340.0;
const STOP_HEIGHT: f64 = 44.0;

#[derive(Deserialize, Clone, Debug)]
struct Action {
    action: String,
    x: Option<i32>,
    y: Option<i32>,
    #[serde(default)]
    label: String,
    #[serde(default)]
    agent: String,
    #[serde(default)]
    agent_id: String,
}

/// What the overlay page draws, in its own CSS pixels.
#[derive(Serialize)]
struct Draw<'a> {
    x: Option<f64>,
    y: Option<f64>,
    click: bool,
    label: &'a str,
    agent: &'a str,
    agent_id: &'a str,
}

#[derive(Default)]
pub struct OverlayState {
    last_action: Mutex<Option<Instant>>,
    /// The agent acting most recently: (id, name), for the Stop pill.
    agent: Mutex<Option<(String, String)>>,
}

pub fn watch(app: &AppHandle) {
    app.manage(OverlayState::default());
    let feed_app = app.clone();
    thread::spawn(move || loop {
        if let Err(e) = follow_feed(&feed_app) {
            log::debug!("agent cursor feed: {e}");
        }
        thread::sleep(Duration::from_secs(3));
    });
    let idle_app = app.clone();
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(500));
        let state = idle_app.state::<OverlayState>();
        let idle = state.last_action.lock().unwrap().map(|t| t.elapsed() > IDLE_HIDE).unwrap_or(false);
        if idle {
            *state.last_action.lock().unwrap() = None;
            hide(&idle_app);
        }
    });
}

/// (port, token) once the engine this app started is running.
fn engine_endpoint(app: &AppHandle) -> Option<(u16, String)> {
    let status = app.state::<engine::Engine>().status();
    if status.status != Status::Running {
        return None;
    }
    let token = engine::desktop_token(app)?;
    let url = status.url?;
    let port = url.rsplit(':').next()?.trim_end_matches('/').parse().ok()?;
    Some((port, token))
}

fn connect(port: u16) -> std::io::Result<TcpStream> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_secs(2))
}

/// Read the engine's event stream until it ends; returns to reconnect.
fn follow_feed(app: &AppHandle) -> Result<(), String> {
    let Some((port, token)) = engine_endpoint(app) else {
        return Ok(());
    };
    let mut stream = connect(port).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(FEED_TIMEOUT)).map_err(|e| e.to_string())?;
    let request = format!(
        "GET {FEED_PATH} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\
         X-Cognitrix-Desktop-Token: {token}\r\nConnection: keep-alive\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).map_err(|e| e.to_string())?;
    let mut reader = BufReader::new(stream);

    let mut status_line = String::new();
    reader.read_line(&mut status_line).map_err(|e| e.to_string())?;
    if !status_line.contains(" 200") {
        thread::sleep(Duration::from_secs(10)); // feed off (no token) or engine not ready
        return Err(format!("feed refused: {}", status_line.trim()));
    }
    let mut chunked = false;
    loop {
        let mut header = String::new();
        reader.read_line(&mut header).map_err(|e| e.to_string())?;
        let header = header.trim();
        if header.is_empty() {
            break;
        }
        let lower = header.to_ascii_lowercase();
        if lower.starts_with("transfer-encoding:") && lower.contains("chunked") {
            chunked = true;
        }
    }

    let mut pending = Vec::new();
    let mut event = String::new();
    loop {
        let bytes = if chunked { read_chunk(&mut reader)? } else { read_some(&mut reader)? };
        if bytes.is_empty() {
            return Ok(()); // the engine closed the stream
        }
        pending.extend_from_slice(&bytes);
        while let Some(end) = pending.iter().position(|b| *b == b'\n') {
            let line: Vec<u8> = pending.drain(..=end).collect();
            let line = String::from_utf8_lossy(&line).trim_end_matches(['\r', '\n']).to_string();
            if let Some(name) = line.strip_prefix("event:") {
                event = name.trim().to_string();
            } else if let Some(data) = line.strip_prefix("data:") {
                if event == "action" {
                    if let Ok(action) = serde_json::from_str::<Action>(data.trim()) {
                        show_action(app, action);
                    }
                }
            } else if line.is_empty() {
                event.clear();
            }
        }
    }
}

fn read_chunk(reader: &mut BufReader<TcpStream>) -> Result<Vec<u8>, String> {
    let mut size_line = String::new();
    if reader.read_line(&mut size_line).map_err(|e| e.to_string())? == 0 {
        return Ok(Vec::new());
    }
    let size = usize::from_str_radix(size_line.trim().split(';').next().unwrap_or("0"), 16)
        .map_err(|e| format!("bad chunk size: {e}"))?;
    if size == 0 {
        return Ok(Vec::new());
    }
    let mut data = vec![0u8; size];
    reader.read_exact(&mut data).map_err(|e| e.to_string())?;
    let mut crlf = [0u8; 2];
    reader.read_exact(&mut crlf).map_err(|e| e.to_string())?;
    Ok(data)
}

fn read_some(reader: &mut BufReader<TcpStream>) -> Result<Vec<u8>, String> {
    let mut buf = [0u8; 4096];
    let read = reader.read(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf[..read].to_vec())
}

fn show_action(app: &AppHandle, action: Action) {
    if !settings::load(app).agent_cursor {
        return;
    }
    let state = app.state::<OverlayState>();
    *state.last_action.lock().unwrap() = Some(Instant::now());
    if !action.agent_id.is_empty() {
        *state.agent.lock().unwrap() = Some((action.agent_id.clone(), action.agent.clone()));
    }
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Err(e) = draw(&handle, &action) {
            log::warn!("agent cursor: {e}");
        }
    });
}

fn draw(app: &AppHandle, action: &Action) -> tauri::Result<()> {
    let cursor = cursor_window(app)?;
    let stop = stop_window(app)?;

    // The cursor window spans every monitor; screen pixels -> its CSS pixels.
    let origin = cursor.outer_position()?;
    let scale = cursor.scale_factor()?;
    let to_css = |v: i32, o: i32| (v - o) as f64 / scale;
    let draw = Draw {
        x: action.x.map(|x| to_css(x, origin.x)),
        y: action.y.map(|y| to_css(y, origin.y)),
        click: action.action.contains("click") || action.action.starts_with("element") || action.action.starts_with("screen"),
        label: &action.label,
        agent: &action.agent,
        agent_id: &action.agent_id,
    };
    let json = serde_json::to_string(&draw).unwrap_or_else(|_| "{}".into());
    show_quietly(&cursor);
    let _ = cursor.eval(&format!("window.agentAction && window.agentAction({json})"));
    show_quietly(&stop);
    let _ = stop.eval(&format!("window.agentActive && window.agentActive({json})"));
    Ok(())
}

fn hide(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        for label in [CURSOR, STOP] {
            if let Some(window) = handle.get_webview_window(label) {
                let _ = window.hide();
            }
        }
    });
}

/// The bounding box of every monitor, in physical pixels.
fn virtual_screen(app: &AppHandle) -> (PhysicalPosition<i32>, PhysicalSize<u32>) {
    let monitors = app.available_monitors().unwrap_or_default();
    if monitors.is_empty() {
        return (PhysicalPosition::new(0, 0), PhysicalSize::new(1920, 1080));
    }
    let (mut left, mut top, mut right, mut bottom) = (i32::MAX, i32::MAX, i32::MIN, i32::MIN);
    for m in &monitors {
        let (p, s) = (m.position(), m.size());
        left = left.min(p.x);
        top = top.min(p.y);
        right = right.max(p.x + s.width as i32);
        bottom = bottom.max(p.y + s.height as i32);
    }
    (PhysicalPosition::new(left, top), PhysicalSize::new((right - left) as u32, (bottom - top) as u32))
}

fn cursor_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let window = match app.get_webview_window(CURSOR) {
        Some(window) => window,
        None => {
            let builder = WebviewWindowBuilder::new(app, CURSOR, WebviewUrl::App("overlay.html".into()));
            let window = see_through(builder)
                .title("Cognitrix agent cursor")
                .decorations(false)
                .shadow(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .resizable(false)
                .focused(false)
                .visible(false)
                .additional_browser_args(crate::BROWSER_ARGS)
                .build()?;
            window.set_ignore_cursor_events(true)?;
            native::make_overlay(&window, true);
            window
        }
    };
    // Follow monitor changes (plugged in, resolution, scaling).
    let (position, size) = virtual_screen(app);
    let _ = window.set_position(position);
    let _ = window.set_size(size);
    Ok(window)
}

fn stop_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    if let Some(window) = app.get_webview_window(STOP) {
        return Ok(window);
    }
    let builder = WebviewWindowBuilder::new(app, STOP, WebviewUrl::App("stop.html".into()));
    let window = see_through(builder)
        .title("Cognitrix agent")
        .inner_size(STOP_WIDTH, STOP_HEIGHT)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .additional_browser_args(crate::BROWSER_ARGS)
        .build()?;
    // Top centre of the main monitor, below its top edge.
    if let Ok(Some(monitor)) = window.primary_monitor() {
        let scale = monitor.scale_factor();
        let width = (STOP_WIDTH * scale) as i32;
        let x = monitor.position().x + (monitor.size().width as i32 - width) / 2;
        let y = monitor.position().y + (12.0 * scale) as i32;
        let _ = window.set_position(PhysicalPosition::new(x, y));
    }
    native::make_overlay(&window, false);
    Ok(window)
}

/// A see-through window. macOS needs Tauri's private-API feature for that,
/// which this app doesn't enable, so there the windows stay opaque.
fn see_through<'a>(
    builder: WebviewWindowBuilder<'a, tauri::Wry, AppHandle>,
) -> WebviewWindowBuilder<'a, tauri::Wry, AppHandle> {
    #[cfg(not(target_os = "macos"))]
    let builder = builder.transparent(true);
    builder
}

/// Show a window without activating it (the person keeps their focus).
fn show_quietly(window: &WebviewWindow) {
    if !native::show_no_activate(window) {
        let _ = window.show();
    }
}

/// Stop the agent shown on the pill (or every agent using the computer).
pub fn stop(app: &AppHandle, agent_id: Option<String>) -> Result<(), String> {
    let (port, token) = engine_endpoint(app).ok_or("The engine isn't running")?;
    let body = serde_json::json!({ "agent_id": agent_id }).to_string();
    let request = format!(
        "POST {STOP_PATH} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n\
         X-Cognitrix-Desktop-Token: {token}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let mut stream = connect(port).map_err(|e| e.to_string())?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    stream.write_all(request.as_bytes()).map_err(|e| e.to_string())?;
    let mut head = [0u8; 12];
    stream.read_exact(&mut head).map_err(|e| e.to_string())?;
    if !String::from_utf8_lossy(&head).contains(" 200") {
        return Err("The engine refused the stop request".into());
    }
    *app.state::<OverlayState>().last_action.lock().unwrap() = None;
    hide(app);
    Ok(())
}

/// The agent currently shown on the Stop pill.
pub fn current_agent(app: &AppHandle) -> Option<String> {
    app.try_state::<OverlayState>()?.agent.lock().unwrap().as_ref().map(|(id, _)| id.clone())
}

#[cfg(windows)]
mod native {
    use tauri::WebviewWindow;

    const GWL_EXSTYLE: i32 = -20;
    const WS_EX_TRANSPARENT: isize = 0x0000_0020;
    const WS_EX_TOOLWINDOW: isize = 0x0000_0080;
    const WS_EX_TOPMOST: isize = 0x0000_0008;
    const WS_EX_LAYERED: isize = 0x0008_0000;
    const WS_EX_NOACTIVATE: isize = 0x0800_0000;
    const WDA_EXCLUDEFROMCAPTURE: u32 = 0x0000_0011;
    const SW_SHOWNOACTIVATE: i32 = 4;
    const HWND_TOPMOST: isize = -1;
    const SWP_NOMOVE: u32 = 0x0002;
    const SWP_NOSIZE: u32 = 0x0001;
    const SWP_NOACTIVATE: u32 = 0x0010;

    #[link(name = "user32")]
    extern "system" {
        fn GetWindowLongPtrW(hwnd: isize, index: i32) -> isize;
        fn SetWindowLongPtrW(hwnd: isize, index: i32, value: isize) -> isize;
        fn SetWindowDisplayAffinity(hwnd: isize, affinity: u32) -> i32;
        fn ShowWindow(hwnd: isize, cmd: i32) -> i32;
        fn SetWindowPos(hwnd: isize, after: isize, x: i32, y: i32, cx: i32, cy: i32, flags: u32) -> i32;
    }

    fn handle(window: &WebviewWindow) -> Option<isize> {
        window.hwnd().ok().map(|hwnd| hwnd.0 as isize)
    }

    /// Never activated, kept out of screen captures; the cursor window also
    /// lets every click through.
    pub fn make_overlay(window: &WebviewWindow, click_through: bool) {
        let Some(hwnd) = handle(window) else { return };
        unsafe {
            let mut style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST;
            if click_through {
                style |= WS_EX_TRANSPARENT | WS_EX_LAYERED;
            }
            SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style);
            SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE);
        }
    }

    pub fn show_no_activate(window: &WebviewWindow) -> bool {
        let Some(hwnd) = handle(window) else { return false };
        unsafe {
            ShowWindow(hwnd, SW_SHOWNOACTIVATE);
            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        }
        true
    }
}

#[cfg(not(windows))]
mod native {
    use tauri::WebviewWindow;

    pub fn make_overlay(_window: &WebviewWindow, _click_through: bool) {}

    pub fn show_no_activate(_window: &WebviewWindow) -> bool {
        false
    }
}
