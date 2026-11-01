//! Starts, watches and stops the bundled Python engine (FastAPI + the task
//! worker, frozen with PyInstaller as `engine/cognitrix-engine.exe`).

use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

use crate::settings;

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// First start unpacks ONNX models and creates the database, so be generous.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Starting,
    Running,
    Stopped,
    Failed,
}

#[derive(Clone, Debug, Serialize)]
pub struct EngineStatus {
    pub status: Status,
    pub url: Option<String>,
    pub port: u16,
    pub error: Option<String>,
    pub log_path: String,
    /// True when the app talks to an engine it didn't start
    /// (COGNITRIX_ENGINE_URL, used while developing the UI).
    pub external: bool,
}

pub struct Engine {
    child: Mutex<Option<Child>>,
    status: Mutex<EngineStatus>,
    /// Bumped on every start/stop so stale watcher threads exit.
    generation: AtomicU64,
}

impl Engine {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            status: Mutex::new(EngineStatus {
                status: Status::Starting,
                url: None,
                port: 0,
                error: None,
                log_path: String::new(),
                external: false,
            }),
            generation: AtomicU64::new(0),
        }
    }

    pub fn status(&self) -> EngineStatus {
        self.status.lock().unwrap().clone()
    }
}

pub fn log_path(app: &AppHandle) -> PathBuf {
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| settings::data_folder(app).join("logs"))
        .join("engine.log")
}

fn set_status(app: &AppHandle, update: impl FnOnce(&mut EngineStatus)) {
    let engine = app.state::<Engine>();
    let snapshot = {
        let mut status = engine.status.lock().unwrap();
        update(&mut status);
        status.clone()
    };
    let _ = app.emit("engine-status", &snapshot);
    crate::tray::refresh(app, &snapshot);
}

/// The preferred port if it's free, otherwise any free port.
fn pick_port(preferred: u16) -> u16 {
    if preferred != 0 && TcpListener::bind(("127.0.0.1", preferred)).is_ok() {
        return preferred;
    }
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(preferred)
}

/// The engine answers any HTTP request once uvicorn is serving. /health can
/// still be a 503 (task queue not ready yet); that counts as up.
fn responds(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut head = [0u8; 5];
    stream.read_exact(&mut head).is_ok() && &head == b"HTTP/"
}

fn engine_command(app: &AppHandle) -> Result<Command, String> {
    // Developer override: run the engine from a Python checkout instead of
    // the bundled build, e.g. COGNITRIX_ENGINE_CMD="python -m cognitrix.desktop".
    if let Ok(custom) = std::env::var("COGNITRIX_ENGINE_CMD") {
        let mut parts = custom.split_whitespace();
        let program = parts.next().ok_or("COGNITRIX_ENGINE_CMD is empty")?;
        let mut cmd = Command::new(program);
        cmd.args(parts);
        return Ok(cmd);
    }
    let exe = if cfg!(windows) { "cognitrix-engine.exe" } else { "cognitrix-engine" };
    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let path = resource_dir.join("engine").join(exe);
    if !path.is_file() {
        return Err(format!(
            "The engine isn't installed at {}. Reinstall Cognitrix, or build it with `python desktop/scripts/build_engine.py`.",
            path.display()
        ));
    }
    Ok(Command::new(path))
}

pub fn start(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || start_blocking(&app));
}

fn start_blocking(app: &AppHandle) {
    let engine = app.state::<Engine>();
    let generation = engine.generation.fetch_add(1, Ordering::SeqCst) + 1;
    let log = log_path(app);
    let prefs = settings::load(app);

    // Talk to an engine that's already running (UI development).
    if let Ok(url) = std::env::var("COGNITRIX_ENGINE_URL") {
        let url = url.trim_end_matches('/').to_string();
        set_status(app, |s| {
            *s = EngineStatus {
                status: Status::Running,
                url: Some(url.clone()),
                port: 0,
                error: None,
                log_path: log.display().to_string(),
                external: true,
            }
        });
        crate::on_engine_ready(app, &url);
        return;
    }

    let port = pick_port(prefs.preferred_port);
    set_status(app, |s| {
        s.status = Status::Starting;
        s.port = port;
        s.url = None;
        s.error = None;
        s.log_path = log.display().to_string();
    });

    let fail = |message: String| {
        set_status(app, |s| {
            s.status = Status::Failed;
            s.error = Some(message.clone());
        });
    };

    let mut cmd = match engine_command(app) {
        Ok(cmd) => cmd,
        Err(message) => return fail(message),
    };
    let working_folder = settings::working_folder(app, &prefs);
    let _ = fs::create_dir_all(&working_folder);
    if let Some(dir) = log.parent() {
        let _ = fs::create_dir_all(dir);
    }
    let log_file = match OpenOptions::new().create(true).append(true).open(&log) {
        Ok(file) => file,
        Err(e) => return fail(format!("Could not open the engine log {}: {e}", log.display())),
    };
    let log_err = log_file.try_clone().ok();

    cmd.arg("engine")
        .arg("--port")
        .arg(port.to_string())
        .arg("--parent-pid")
        .arg(std::process::id().to_string())
        .arg("--working-folder")
        .arg(&working_folder)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_file))
        .stderr(log_err.map(Stdio::from).unwrap_or_else(Stdio::null));
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let child = match cmd.spawn() {
        Ok(child) => child,
        Err(e) => return fail(format!("Could not start the engine: {e}")),
    };
    *engine.child.lock().unwrap() = Some(child);

    let started = Instant::now();
    loop {
        if engine.generation.load(Ordering::SeqCst) != generation {
            return;
        }
        if let Some(code) = exited(app) {
            return fail(format!(
                "The engine stopped while starting (exit code {code}). Port {port} may be in use, or see the engine log."
            ));
        }
        if responds(port) {
            break;
        }
        if started.elapsed() > STARTUP_TIMEOUT {
            stop(app);
            return fail("The engine took too long to start. See the engine log.".into());
        }
        thread::sleep(Duration::from_millis(300));
    }

    let url = format!("http://127.0.0.1:{port}");
    set_status(app, |s| {
        s.status = Status::Running;
        s.url = Some(url.clone());
    });
    crate::on_engine_ready(app, &url);
    watch(app, generation);
}

/// Exit code if the engine process has exited.
fn exited(app: &AppHandle) -> Option<String> {
    let engine = app.state::<Engine>();
    let mut guard = engine.child.lock().unwrap();
    match guard.as_mut().map(|child| child.try_wait()) {
        Some(Ok(Some(status))) => {
            *guard = None;
            Some(status.code().map(|c| c.to_string()).unwrap_or_else(|| "none".into()))
        }
        None => Some("none".into()),
        _ => None,
    }
}

/// Notice the engine dying after a good start, so the UI and tray can say so.
fn watch(app: &AppHandle, generation: u64) {
    let engine = app.state::<Engine>();
    loop {
        thread::sleep(Duration::from_secs(2));
        if engine.generation.load(Ordering::SeqCst) != generation {
            return;
        }
        if let Some(code) = exited(app) {
            set_status(app, |s| {
                s.status = Status::Stopped;
                s.error = Some(format!("The engine stopped unexpectedly (exit code {code})."));
            });
            crate::notify(app, "Cognitrix engine stopped", "Restart it from the tray or the app window.", Some("/settings"));
            return;
        }
    }
}

/// Stop the engine and the task worker it started.
pub fn stop(app: &AppHandle) {
    let engine = app.state::<Engine>();
    engine.generation.fetch_add(1, Ordering::SeqCst);
    let Some(mut child) = engine.child.lock().unwrap().take() else {
        return;
    };
    #[cfg(windows)]
    {
        // /T takes the worker (a child of the engine) down with it.
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    // Elsewhere the worker watches the engine's pid and exits on its own.
    let _ = child.kill();
    let _ = child.wait();
}

pub fn restart(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || {
        stop(&app);
        set_status(&app, |s| {
            s.status = Status::Starting;
            s.error = None;
        });
        start_blocking(&app);
    });
}
