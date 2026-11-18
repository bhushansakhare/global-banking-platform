// Meridian desktop shell.
//
// 1. Starts the bundled Python backend (API + worker + scheduler) bound to 127.0.0.1 only.
// 2. Waits for /api/v1/health, then points the window at the backend, which serves the
//    same React build used on the web (same origin => httpOnly cookies + CSRF work unchanged).
// 3. Stops the backend when the window closes.
//
// The webview is granted no shell/filesystem permissions; all access goes through the
// authenticated HTTP API.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};

struct Backend(Mutex<Option<Child>>);

const HOST: &str = "127.0.0.1";

fn port() -> u16 {
    std::env::var("GBP_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8000)
}

/// Backend directory: GBP_BACKEND_DIR, else ../backend relative to the executable (dev layout),
/// else <resources>/backend (installed layout).
fn backend_dir(app: &tauri::AppHandle) -> PathBuf {
    if let Ok(dir) = std::env::var("GBP_BACKEND_DIR") {
        return PathBuf::from(dir);
    }
    let dev = std::env::current_dir().unwrap_or_default().join("..").join("backend");
    if dev.join("main.py").exists() {
        return dev;
    }
    app.path().resource_dir().unwrap_or_default().join("backend")
}

fn healthy(port: u16) -> bool {
    let Ok(mut s) = TcpStream::connect_timeout(&format!("{HOST}:{port}").parse().unwrap(), Duration::from_millis(400)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_millis(800)));
    let req = format!("GET /api/v1/health HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n");
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    let _ = s.read_to_string(&mut buf);
    buf.starts_with("HTTP/1.1 200")
}

fn spawn_backend(dir: &PathBuf, port: u16) -> std::io::Result<Child> {
    let python = dir.join(".venv").join("Scripts").join("python.exe");
    let mut cmd = Command::new(if python.exists() { python } else { PathBuf::from("python") });
    cmd.arg("main.py")
        .current_dir(dir)
        .env("HOST", HOST) // never listen on external interfaces
        .env("PORT", port.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn()
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let port = port();
            // Reuse an already-running backend (e.g. a developer server); otherwise start one.
            if !healthy(port) {
                let dir = backend_dir(&handle);
                match spawn_backend(&dir, port) {
                    Ok(child) => *app.state::<Backend>().0.lock().unwrap() = Some(child),
                    Err(e) => eprintln!("failed to start backend in {}: {e}", dir.display()),
                }
            }
            std::thread::spawn(move || {
                let start = Instant::now();
                while start.elapsed() < Duration::from_secs(60) {
                    if healthy(port) {
                        if let Some(w) = handle.get_webview_window("main") {
                            let url = Url::parse(&format!("http://{HOST}:{port}/")).unwrap();
                            let _ = w.navigate(url);
                        }
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
                if let Some(w) = handle.get_webview_window("main") {
                    let _ = w.eval("document.getElementById('status').textContent = \
                        'The banking engine did not start. Check PostgreSQL and backend/.env, then restart.'");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Meridian");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Backend>().0.lock().unwrap().take() {
                let _ = child.kill(); // worker jobs left 'running' are recovered on next start
                let _ = child.wait();
            }
        }
    });
}
