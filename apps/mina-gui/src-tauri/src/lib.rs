// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Write};
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, PhysicalPosition, PhysicalSize, Position, Size, WebviewUrl, WebviewWindow, WebviewWindowBuilder, Window};

static VOICE_LOG: Mutex<Vec<String>> = Mutex::new(Vec::new());
static VOICE_CHILD: Mutex<Option<Child>> = Mutex::new(None);
static VOICE_PID: Mutex<Option<u32>> = Mutex::new(None);
static CAPTURE_ACTIVE: Mutex<bool> = Mutex::new(false);

#[derive(Serialize)]
struct HealthStatus {
    ok: bool,
    status_code: Option<u16>,
    detail: String,
}

#[derive(Serialize)]
struct VoiceStatus {
    running: bool,
    detail: String,
}

#[derive(Serialize)]
struct VoiceFeed {
    lines: Vec<String>,
}

#[derive(Serialize)]
struct VoiceDevice {
    index: i32,
    name: String,
    is_input: bool,
    is_output: bool,
    is_default_input: bool,
    is_default_output: bool,
}

#[derive(Serialize)]
struct VoiceDeviceConfig {
    input_device: i32,
    output_device: i32,
}

#[derive(Serialize)]
struct ChatReply {
    text: String,
}

#[derive(Deserialize, Serialize)]
struct WindowPlacement {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    maximized: bool,
    fullscreen: bool,
    monitor_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MonitorTarget {
    name: Option<String>,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

fn select_monitor_target(monitors: &[MonitorTarget], primary_name: Option<&str>) -> Option<MonitorTarget> {
    monitors
        .iter()
        .find(|monitor| monitor.name.as_deref() != primary_name)
        .cloned()
        .or_else(|| monitors.first().cloned())
}

fn window_placement_path(window: &Window) -> Result<std::path::PathBuf, String> {
    let directory = window
        .app_handle()
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    Ok(directory.join("window-placement.json"))
}

fn placement_geometry_valid(placement: &WindowPlacement) -> bool {
    placement.x > -30000
        && placement.y > -30000
        && placement.width >= 800
        && placement.height >= 600
}

fn read_window_placement(window: &Window) -> Option<WindowPlacement> {
    let path = window_placement_path(window).ok()?;
    let content = fs::read_to_string(path).ok()?;
    let placement = serde_json::from_str::<WindowPlacement>(&content).ok()?;
    placement_geometry_valid(&placement).then_some(placement)
}

fn write_window_placement(window: &Window, placement: &WindowPlacement) -> Result<(), String> {
    let content = serde_json::to_string_pretty(placement).map_err(|error| error.to_string())?;
    fs::write(window_placement_path(window)?, content).map_err(|error| error.to_string())
}

fn capture_window_placement(window: &Window) -> Result<WindowPlacement, String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let minimized = window.is_minimized().map_err(|error| error.to_string())?;
    if minimized || position.x <= -30000 || position.y <= -30000 || size.width < 800 || size.height < 600 {
        return Err("window_geometry_transient".to_string());
    }
    let monitor_name = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .and_then(|monitor| monitor.name().cloned());

    Ok(WindowPlacement {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
        maximized: window.is_maximized().map_err(|error| error.to_string())?,
        fullscreen: window.is_fullscreen().map_err(|error| error.to_string())?,
        monitor_name,
    })
}

fn persist_window_placement(window: &Window) -> Result<WindowPlacement, String> {
    let maximized = window.is_maximized().map_err(|error| error.to_string())?;
    let fullscreen = window.is_fullscreen().map_err(|error| error.to_string())?;
    let placement = if maximized || fullscreen {
        let mut saved = read_window_placement(window).ok_or("normal_window_geometry_unavailable")?;
        saved.maximized = maximized;
        saved.fullscreen = fullscreen;
        saved.monitor_name = window
            .current_monitor()
            .map_err(|error| error.to_string())?
            .and_then(|monitor| monitor.name().cloned());
        saved
    } else {
        capture_window_placement(window)?
    };
    write_window_placement(window, &placement)?;
    Ok(placement)
}

#[tauri::command]
fn window_save_placement(window: Window) -> Result<WindowPlacement, String> {
    persist_window_placement(&window)
}

#[tauri::command]
fn window_restore_placement(window: Window) -> Result<serde_json::Value, String> {
    let path = window_placement_path(&window)?;
    if !path.exists() {
        return Ok(serde_json::json!({ "ok": true, "restored": false }));
    }

    let content = fs::read_to_string(path).map_err(|error| error.to_string())?;
    let placement: WindowPlacement = serde_json::from_str(&content).map_err(|error| error.to_string())?;
    if !placement_geometry_valid(&placement) {
        return Ok(serde_json::json!({ "ok": true, "restored": false, "reason": "saved_geometry_invalid" }));
    }
    let monitors = window.available_monitors().map_err(|error| error.to_string())?;
    let target_monitor = placement
        .monitor_name
        .as_ref()
        .and_then(|name| monitors.iter().find(|monitor| monitor.name() == Some(name)))
        .or_else(|| {
            monitors.iter().find(|monitor| {
                let position = monitor.position();
                let size = monitor.size();
                placement.x >= position.x
                    && placement.x < position.x + size.width as i32
                    && placement.y >= position.y
                    && placement.y < position.y + size.height as i32
            })
        });

    let Some(monitor) = target_monitor else {
        return Ok(serde_json::json!({ "ok": true, "restored": false, "reason": "saved_monitor_unavailable" }));
    };

    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let width = placement.width.clamp(800, monitor_size.width);
    let height = placement.height.clamp(600, monitor_size.height);
    let max_x = monitor_position.x + monitor_size.width.saturating_sub(width) as i32;
    let max_y = monitor_position.y + monitor_size.height.saturating_sub(height) as i32;
    let x = placement.x.clamp(monitor_position.x, max_x);
    let y = placement.y.clamp(monitor_position.y, max_y);

    window.set_fullscreen(false).map_err(|error| error.to_string())?;
    window.unmaximize().map_err(|error| error.to_string())?;
    window
        .set_position(Position::Physical(PhysicalPosition::new(x, y)))
        .map_err(|error| error.to_string())?;
    window
        .set_size(Size::Physical(PhysicalSize::new(width, height)))
        .map_err(|error| error.to_string())?;

    if placement.maximized {
        window.maximize().map_err(|error| error.to_string())?;
    }
    if placement.fullscreen {
        window.set_fullscreen(true).map_err(|error| error.to_string())?;
    }

    Ok(serde_json::json!({
        "ok": true,
        "restored": true,
        "monitor": monitor.name(),
        "fullscreen": placement.fullscreen,
        "maximized": placement.maximized,
    }))
}

#[tauri::command]
fn window_move_to_secondary(window: Window) -> Result<serde_json::Value, String> {
    let monitors = window.available_monitors().map_err(|error| error.to_string())?;
    if monitors.len() < 2 {
        return Ok(serde_json::json!({ "ok": false, "error": "secondary_monitor_not_found" }));
    }

    let primary_name = window
        .primary_monitor()
        .map_err(|error| error.to_string())?
        .and_then(|monitor| monitor.name().cloned());
    let target = monitors
        .iter()
        .find(|monitor| monitor.name() != primary_name.as_ref())
        .unwrap_or(&monitors[1]);

    window.set_fullscreen(false).map_err(|error| error.to_string())?;
    window.unmaximize().map_err(|error| error.to_string())?;
    window
        .set_position(Position::Physical(*target.position()))
        .map_err(|error| error.to_string())?;
    window
        .set_size(Size::Physical(*target.size()))
        .map_err(|error| error.to_string())?;
    let placement = WindowPlacement {
        x: target.position().x,
        y: target.position().y,
        width: target.size().width,
        height: target.size().height,
        maximized: false,
        fullscreen: true,
        monitor_name: target.name().cloned(),
    };
    write_window_placement(&window, &placement)?;
    window.set_fullscreen(true).map_err(|error| error.to_string())?;

    Ok(serde_json::json!({
        "ok": true,
        "monitor": target.name(),
        "fullscreen": placement.fullscreen,
    }))
}

#[tauri::command]
fn window_toggle_fullscreen(window: Window) -> Result<serde_json::Value, String> {
    let fullscreen = window.is_fullscreen().map_err(|error| error.to_string())?;
    window.set_fullscreen(!fullscreen).map_err(|error| error.to_string())?;
    let placement = persist_window_placement(&window)?;
    Ok(serde_json::json!({ "ok": true, "fullscreen": placement.fullscreen }))
}

#[tauri::command]
async fn open_ostranauts_companion(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    if let Some(companion) = app.get_webview_window("ostranauts-companion") {
        let primary_name = companion
            .primary_monitor()
            .map_err(|error| error.to_string())?
            .and_then(|monitor| monitor.name().cloned());
        let monitor_targets = companion
            .available_monitors()
            .map_err(|error| error.to_string())?
            .into_iter()
            .map(|monitor| {
                let position = monitor.position();
                let size = monitor.size();
                MonitorTarget {
                    name: monitor.name().cloned(),
                    x: position.x,
                    y: position.y,
                    width: size.width,
                    height: size.height,
                }
            })
            .collect::<Vec<_>>();
        let target = select_monitor_target(&monitor_targets, primary_name.as_deref())
            .ok_or("monitor_not_found")?;

        companion.set_fullscreen(false).map_err(|error| error.to_string())?;
        companion
            .set_position(Position::Physical(PhysicalPosition::new(target.x, target.y)))
            .map_err(|error| error.to_string())?;
        companion
            .set_size(Size::Physical(PhysicalSize::new(target.width, target.height)))
            .map_err(|error| error.to_string())?;
        companion.set_fullscreen(true).map_err(|error| error.to_string())?;
        companion.show().map_err(|error| error.to_string())?;
        companion.set_focus().map_err(|error| error.to_string())?;
        if let Some(main) = app.get_webview_window("main") {
            main.hide().map_err(|error| error.to_string())?;
        }
        return Ok(serde_json::json!({ "ok": true, "created": false }));
    }

    let companion = WebviewWindowBuilder::new(
        &app,
        "ostranauts-companion",
        WebviewUrl::App("companion.html".into()),
    )
    .title("Mina // Ostranauts Companion")
    .inner_size(1280.0, 720.0)
    .min_inner_size(800.0, 600.0)
    .decorations(false)
    .visible(false)
    .build()
    .map_err(|error| error.to_string())?;

    let app_on_destroy = app.clone();
    companion.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            if let Some(main) = app_on_destroy.get_webview_window("main") {
                let _ = main.show();
                let _ = main.set_focus();
            }
        }
    });

    let primary_name = companion
        .primary_monitor()
        .map_err(|error| error.to_string())?
        .and_then(|monitor| monitor.name().cloned());
    let monitor_targets = companion
        .available_monitors()
        .map_err(|error| error.to_string())?
        .into_iter()
        .map(|monitor| {
            let position = monitor.position();
            let size = monitor.size();
            MonitorTarget {
                name: monitor.name().cloned(),
                x: position.x,
                y: position.y,
                width: size.width,
                height: size.height,
            }
        })
        .collect::<Vec<_>>();
    let target = select_monitor_target(&monitor_targets, primary_name.as_deref())
        .ok_or("monitor_not_found")?;

    companion
        .set_position(Position::Physical(PhysicalPosition::new(target.x, target.y)))
        .map_err(|error| error.to_string())?;
    companion
        .set_size(Size::Physical(PhysicalSize::new(target.width, target.height)))
        .map_err(|error| error.to_string())?;
    companion.set_fullscreen(true).map_err(|error| error.to_string())?;
    companion.show().map_err(|error| error.to_string())?;
    companion.set_focus().map_err(|error| error.to_string())?;

    if let Some(main) = app.get_webview_window("main") {
        main.hide().map_err(|error| error.to_string())?;
    }

    Ok(serde_json::json!({
        "ok": true,
        "created": true,
        "monitor": target.name.as_deref().unwrap_or("primary"),
        "x": target.x,
        "y": target.y,
        "width": target.width,
        "height": target.height,
    }))
}

#[cfg(test)]
mod tests {
    use super::MonitorTarget;
    use super::select_monitor_target;

    #[test]
    fn select_monitor_target_prefers_non_primary_monitor_when_available() {
        let monitors = vec![
            MonitorTarget {
                name: Some("primary".to_string()),
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
            },
            MonitorTarget {
                name: Some("secondary".to_string()),
                x: 1920,
                y: 0,
                width: 1920,
                height: 1080,
            },
        ];

        let target = select_monitor_target(&monitors, Some("primary"));
        assert_eq!(target.unwrap().name.as_deref(), Some("secondary"));
    }

    #[test]
    fn select_monitor_target_falls_back_to_primary_monitor() {
        let monitors = vec![MonitorTarget {
            name: Some("primary".to_string()),
            x: 0,
            y: 0,
            width: 1920,
            height: 1080,
        }];

        let target = select_monitor_target(&monitors, Some("primary"));
        assert_eq!(target.unwrap().name.as_deref(), Some("primary"));
    }
}

#[tauri::command]
fn close_ostranauts_companion(window: WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    if window.label() != "ostranauts-companion" {
        return Err("not_companion_window".to_string());
    }

    if let Some(main) = app.get_webview_window("main") {
        main.show().map_err(|error| error.to_string())?;
        main.set_focus().map_err(|error| error.to_string())?;
    }
    window.close().map_err(|error| error.to_string())
}

fn push_voice_log(line: String) {
    if let Ok(mut log) = VOICE_LOG.lock() {
        log.push(line);
        if log.len() > 200 {
            let keep = 200;
            let drop_count = log.len().saturating_sub(keep);
            log.drain(0..drop_count);
        }
    }
}

fn run_powershell(command: &str) -> Result<std::process::Output, String> {
    #[cfg(target_os = "windows")]
    let mut cmd = Command::new(powershell_executable());

    #[cfg(not(target_os = "windows"))]
    let mut cmd = Command::new("powershell.exe");

    cmd.arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-Command")
        .arg(command)
        .output()
        .map_err(|e| format!("powershell exec failed: {e}"))
}

fn api_status_ok(timeout_ms: u64) -> bool {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(timeout_ms))
        .build();

    let Ok(client) = client else {
        return false;
    };

    match client.get("http://127.0.0.1:8000/status").send() {
        Ok(resp) => resp.status().is_success(),
        Err(_) => false,
    }
}

fn wait_for_api_up(timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if api_status_ok(800) {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn wait_for_api_down(timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if !api_status_ok(500) {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn mk1_root_dir() -> String {
    env::var("MK1_ROOT").unwrap_or_else(|_| "E:\\Mina_MK1".to_string())
}

fn mk1_script_path(script_name: &str) -> String {
    Path::new(&mk1_root_dir())
        .join(script_name)
        .to_string_lossy()
        .to_string()
}

fn powershell_executable() -> String {
    #[cfg(target_os = "windows")]
    {
        let system_root = env::var("SystemRoot")
            .or_else(|_| env::var("WINDIR"))
            .unwrap_or_else(|_| "C:\\Windows".to_string());
        let candidate = Path::new(&system_root)
            .join("System32")
            .join("WindowsPowerShell")
            .join("v1.0")
            .join("powershell.exe");
        if candidate.exists() {
            return candidate.to_string_lossy().to_string();
        }
        return "powershell.exe".to_string();
    }

    #[cfg(not(target_os = "windows"))]
    {
        "powershell.exe".to_string()
    }
}

fn spawn_detached_powershell_file(script_path: &str, cwd: &str) -> Result<u32, String> {
    #[cfg(target_os = "windows")]
    let cmd = Command::new(powershell_executable());

    #[cfg(not(target_os = "windows"))]
    let mut cmd = Command::new("powershell.exe");

    let mut configured = cmd;
    configured
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(script_path)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    #[cfg(target_os = "windows")]
    {
        configured.creation_flags(0x00000008 | 0x00000200);
    }

    let child = configured
        .spawn()
        .map_err(|e| format!("failed to launch script: {e}"))?;
    Ok(child.id())
}

fn spawn_visible_powershell_file(script_path: &str, cwd: &str) -> Result<u32, String> {
    #[cfg(target_os = "windows")]
    let cmd = Command::new(powershell_executable());

    #[cfg(not(target_os = "windows"))]
    let mut cmd = Command::new("powershell.exe");

    let script_path_owned = script_path.to_string();
    let cwd_owned = cwd.to_string();

    let mut configured = cmd;
    configured
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-NoExit")
        .arg("-Command")
        .arg(format!(
            "Set-Location -LiteralPath '{}' ; & '{}'",
            cwd_owned.replace("'", "''"),
            script_path_owned.replace("'", "''")
        ))
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = configured
        .spawn()
        .map_err(|e| format!("failed to launch visible script: {e}"))?;
    Ok(child.id())
}

fn read_pid_from_file(path: &Path) -> Option<u32> {
    let raw = fs::read_to_string(path).ok()?;
    raw.trim().parse::<u32>().ok()
}

fn is_pid_running(pid: u32) -> bool {
    #[cfg(target_os = "windows")]
    {
        let probe = format!(
            "$p = Get-Process -Id {} -ErrorAction SilentlyContinue; if ($p) {{ 'RUNNING' }} else {{ 'STOPPED' }}",
            pid
        );
        if let Ok(out) = run_powershell(&probe) {
            if out.status.success() {
                let txt = String::from_utf8_lossy(&out.stdout).to_string();
                return txt.to_ascii_uppercase().contains("RUNNING");
            }
        }
        false
    }

    #[cfg(not(target_os = "windows"))]
    {
        let output = Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .output();
        output.map(|o| o.status.success()).unwrap_or(false)
    }
}

fn get_tracked_pid() -> Option<u32> {
    VOICE_PID.lock().ok().and_then(|g| *g)
}

fn set_tracked_pid(pid: Option<u32>) {
    if let Ok(mut g) = VOICE_PID.lock() {
        *g = pid;
    }
}

fn find_windows_voice_pid() -> Result<Option<u32>, String> {
    let script_name = voice_helper_script_name();
    let probe = format!(
        "Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{}*' }} | Select-Object -First 1 -ExpandProperty ProcessId",
        script_name
    );
    let out = run_powershell(&probe)?;
    if !out.status.success() {
        return Ok(None);
    }
    let raw = String::from_utf8_lossy(&out.stdout).to_string();
    let pid = raw
        .lines()
        .find_map(|line| line.trim().parse::<u32>().ok());
    Ok(pid)
}

fn find_voice_monitor_pid() -> Result<Option<u32>, String> {
    let probe = r#"
$matches = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -like '*start_mina_voice_monitor.ps1*' -or
            $_.CommandLine -like '*mina_windows_voice_loop.py*'
        )
    } |
    Select-Object -First 1 -ExpandProperty ProcessId
$matches
"#;

    let out = run_powershell(probe)?;
    if !out.status.success() {
        return Ok(None);
    }

    let raw = String::from_utf8_lossy(&out.stdout).to_string();
    let pid = raw
        .lines()
        .find_map(|line| line.trim().parse::<u32>().ok());
    Ok(pid)
}

fn wait_for_voice_monitor_running(timeout: Duration) -> Result<Option<u32>, String> {
    let started = Instant::now();
    let pid_file = Path::new(&mk1_root_dir()).join(".mk1_voice_monitor.pid");

    while started.elapsed() < timeout {
        let status = voice_status()?;
        if status.running {
            if let Some(pid) = get_tracked_pid() {
                return Ok(Some(pid));
            }
            if let Some(pid) = read_pid_from_file(&pid_file) {
                return Ok(Some(pid));
            }
        }
        thread::sleep(Duration::from_millis(400));
    }

    Ok(None)
}

fn ensure_voice_helper() -> Result<(), String> {
    let mut guard = VOICE_CHILD.lock().map_err(|_| "voice child mutex poisoned")?;

    if let Some(child) = guard.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Ok(());
        }
        *guard = None;
    }
    let python_path = ps_single_quote(&voice_python_path());
    let script_path = ps_single_quote(&voice_helper_script_path());
    let helper_cmd = format!(
        "$py='{python}'; if (!(Test-Path $py)) {{ $py='python' }}; $script='{script}'; if (!(Test-Path $script)) {{ Write-Error 'helper script not found'; exit 2 }}; & $py $script",
        python = python_path,
        script = script_path,
    );
    #[cfg(target_os = "windows")]
    let mut proc = Command::new(powershell_executable());
    #[cfg(not(target_os = "windows"))]
    let mut proc = Command::new("powershell.exe");

    let mut child = proc
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-Command")
        .arg(helper_cmd)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to start gui capture helper: {e}"))?;

    if let Some(stdout) = child.stdout.take() {
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                let t = line.trim();
                if !t.is_empty() {
                    push_voice_log(t.to_string());
                }
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let t = line.trim();
                if !t.is_empty() {
                    push_voice_log(format!("[stderr] {t}"));
                }
            }
        });
    }

    *guard = Some(child);
    set_tracked_pid(find_windows_voice_pid()?);
    Ok(())
}

fn send_helper_command(cmd: &str) -> Result<(), String> {
    ensure_voice_helper()?;
    let mut guard = VOICE_CHILD.lock().map_err(|_| "voice child mutex poisoned")?;
    let child = guard.as_mut().ok_or("voice helper missing")?;
    let stdin = child.stdin.as_mut().ok_or("voice helper stdin unavailable")?;
    stdin
        .write_all(format!("{}\n", cmd).as_bytes())
        .map_err(|e| format!("helper stdin write failed: {e}"))?;
    stdin.flush().map_err(|e| format!("helper stdin flush failed: {e}"))?;
    Ok(())
}

fn is_capture_active() -> bool {
    CAPTURE_ACTIVE.lock().map(|v| *v).unwrap_or(false)
}

fn set_capture_active(v: bool) {
    if let Ok(mut s) = CAPTURE_ACTIVE.lock() {
        *s = v;
    }
}

fn ps_single_quote(value: &str) -> String {
    value.replace('\'', "''")
}

fn voice_helper_script_path() -> String {
    env::var("MK1_GUI_CAPTURE_SCRIPT")
        .unwrap_or_else(|_| "C:\\Users\\Admin\\mina-voice\\mina_windows_gui_capture.py".to_string())
}

fn voice_python_path() -> String {
    env::var("MK1_GUI_VOICE_PYTHON")
        .unwrap_or_else(|_| "C:\\Users\\Admin\\mina-voice\\.venv\\Scripts\\python.exe".to_string())
}

fn voice_helper_script_name() -> String {
    Path::new(&voice_helper_script_path())
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("mina_windows_gui_capture.py")
        .to_string()
}

#[tauri::command]
fn voice_list_devices() -> Result<Vec<VoiceDevice>, String> {
    // Use a temp Python file to avoid PowerShell quoting issues with inline -c payloads.
    let py = ps_single_quote(&voice_python_path());
    let script = format!(
        r#"
$py='{py}'
@'
import json
import sounddevice as sd

d = sd.query_devices()
de = sd.default.device
di = de[0] if de and de[0] is not None else -1
do = de[1] if de and de[1] is not None else -1

out = []
for i, x in enumerate(d):
    out.append(dict(
        index=i,
        name=x.get("name", "unknown"),
        is_input=int(x.get("max_input_channels", 0)) > 0,
        is_output=int(x.get("max_output_channels", 0)) > 0,
        is_default_input=(i == di),
        is_default_output=(i == do),
    ))

print(json.dumps(out))
'@ | & $py -
$ec=$LASTEXITCODE
exit $ec
"#,
        py = py
    );

    let out = run_powershell(&script)?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr).to_string();
        let stdout = String::from_utf8_lossy(&out.stdout).to_string();
        return Err(format!("list devices failed: {err}; stdout={stdout}"));
    }

    let raw = String::from_utf8_lossy(&out.stdout).to_string();
    let json_text = raw.trim();
    let value: serde_json::Value = serde_json::from_str(json_text)
        .map_err(|e| format!("failed to parse device json: {e}; raw={json_text}"))?;

    let mut devices = Vec::new();
    if let Some(arr) = value.as_array() {
        for item in arr {
            let index = item.get("index").and_then(|v| v.as_i64()).unwrap_or(-1) as i32;
            let name = item
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            let is_input = item
                .get("is_input")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let is_output = item
                .get("is_output")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let is_default_input = item
                .get("is_default_input")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let is_default_output = item
                .get("is_default_output")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            devices.push(VoiceDevice {
                index,
                name,
                is_input,
                is_output,
                is_default_input,
                is_default_output,
            });
        }
    }

    Ok(devices)
}

#[tauri::command]
fn voice_get_input_device() -> Result<VoiceDeviceConfig, String> {
    let script_path = ps_single_quote(&voice_helper_script_path());
    let script = format!(
        "$p='{path}'; $in=Select-String -Path $p -Pattern '^INPUT_DEVICE\\s*=\\s*(\\d+)' | Select-Object -First 1; $out=Select-String -Path $p -Pattern '^OUTPUT_DEVICE\\s*=\\s*(\\d+)' | Select-Object -First 1; $i=if($in){{$in.Matches[0].Groups[1].Value}}else{{'1'}}; $o=if($out){{$out.Matches[0].Groups[1].Value}}else{{'3'}}; Write-Output \"$i,$o\"",
        path = script_path,
    );
    let out = run_powershell(&script)?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr).to_string();
        return Err(format!("read input/output device failed: {err}"));
    }
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let mut parts = text.split(',');
    let input_device = parts.next().unwrap_or("1").trim().parse::<i32>().unwrap_or(1);
    let output_device = parts.next().unwrap_or("3").trim().parse::<i32>().unwrap_or(3);
    Ok(VoiceDeviceConfig {
        input_device,
        output_device,
    })
}

#[tauri::command]
fn voice_set_input_device(input_device: i32, output_device: Option<i32>) -> Result<VoiceDeviceConfig, String> {
    let out_dev = output_device.unwrap_or(3);
    let script_path = ps_single_quote(&voice_helper_script_path());
    let script = format!(
        "$p='{path}'; $c=Get-Content $p -Raw; $c=$c -replace 'INPUT_DEVICE\\s*=\\s*\\d+', 'INPUT_DEVICE = {}'; if($c -match 'OUTPUT_DEVICE\\s*=\\s*\\d+'){{$c=$c -replace 'OUTPUT_DEVICE\\s*=\\s*\\d+', 'OUTPUT_DEVICE = {}'}} else {{$c=$c + \"`nOUTPUT_DEVICE = {}\"}}; Set-Content $p $c",
        input_device,
        out_dev,
        out_dev,
        path = script_path,
    );
    let out = run_powershell(&script)?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr).to_string();
        return Err(format!("set input/output device failed: {err}"));
    }
    push_voice_log(format!("[system] input/output set to {}/{}", input_device, out_dev));
    Ok(VoiceDeviceConfig {
        input_device,
        output_device: out_dev,
    })
}

#[tauri::command]
async fn gui_chat_send(
    message: String,
    _agent_id: Option<String>,
    _haven_session_id: Option<String>
) -> Result<ChatReply, String> {

    let client = reqwest::Client::new();

    let payload = serde_json::json!({
        "message": message
    });

    let resp = client
        .post("http://127.0.0.1:8000/chat")
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("chat http error: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("chat http status {}", resp.status()));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("chat json parse failed: {e}"))?;

    let reply = json
        .get("reply")
        .and_then(|v| v.as_str())
        .unwrap_or("(no reply)")
        .to_string();

    Ok(ChatReply { text: reply })
}

#[tauri::command]
fn ping_http(url: String) -> Result<HealthStatus, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .map_err(|e| format!("client init failed: {e}"))?;

    let response = client.get(&url).send();
    match response {
        Ok(resp) => {
            let code = resp.status().as_u16();
            let ok = resp.status().is_success();
            Ok(HealthStatus {
                ok,
                status_code: Some(code),
                detail: if ok {
                    "reachable".into()
                } else {
                    format!("http {code}")
                },
            })
        }
        Err(e) => Ok(HealthStatus {
            ok: false,
            status_code: None,
            detail: e.to_string(),
        }),
    }
}

#[tauri::command]
fn voice_start() -> Result<VoiceStatus, String> {
    if let Ok(mut log) = VOICE_LOG.lock() {
        if log.is_empty() {
            log.push("[system] voice controls loaded".into());
        }
    }

    ensure_voice_helper()?;
    if is_capture_active() {
        let pid = find_windows_voice_pid()?;
        return Ok(VoiceStatus {
            running: true,
            detail: format!("already capturing (pid: {:?})", pid),
        });
    }

    send_helper_command("start")?;
    set_capture_active(true);
    let pid = find_windows_voice_pid()?;
    set_tracked_pid(pid);
    push_voice_log("[system] capture started".into());

    Ok(VoiceStatus {
        running: true,
        detail: format!("capturing (pid: {:?})", pid),
    })
}

#[tauri::command]
fn voice_stop() -> Result<VoiceStatus, String> {
    ensure_voice_helper()?;
    if !is_capture_active() {
        return Ok(VoiceStatus {
            running: true,
            detail: "not capturing; press Start Capture first".into(),
        });
    }

    send_helper_command("stop")?;
    set_capture_active(false);
    push_voice_log("[system] capture stopped; processing".into());

    Ok(VoiceStatus {
        running: true,
        detail: "processing turn (transcribe/reply)".into(),
    })
}

#[tauri::command]
fn voice_restart() -> Result<VoiceStatus, String> {
    let mut guard = VOICE_CHILD.lock().map_err(|_| "voice child mutex poisoned")?;
    if let Some(mut child) = guard.take() {
        if let Some(stdin) = child.stdin.as_mut() {
            let _ = stdin.write_all(b"quit\n");
            let _ = stdin.flush();
        }
        let _ = child.kill();
        let _ = child.wait();
        set_tracked_pid(None);
        set_capture_active(false);
        push_voice_log("[system] helper restarted: stopped".into());
        return Ok(VoiceStatus {
            running: false,
            detail: "restart toggle: stopped (press again to start)".into(),
        });
    }

    drop(guard);
    ensure_voice_helper()?;
    let pid = find_windows_voice_pid()?;
    set_tracked_pid(pid);
    push_voice_log("[system] helper restarted: ready".into());
    Ok(VoiceStatus {
        running: true,
        detail: format!("helper ready (pid: {:?})", pid),
    })
}

#[tauri::command]
fn voice_status() -> Result<VoiceStatus, String> {
    let mut running = false;
    {
        let mut guard = VOICE_CHILD.lock().map_err(|_| "voice child mutex poisoned")?;
        if let Some(child) = guard.as_mut() {
            running = child.try_wait().map_err(|e| e.to_string())?.is_none();
            if !running {
                *guard = None;
            }
        }
    }

    let pid = if running {
        match get_tracked_pid() {
            Some(p) => Some(p),
            None => {
                let discovered = find_windows_voice_pid()?;
                set_tracked_pid(discovered);
                discovered
            }
        }
    } else {
        let pid_file = Path::new(&mk1_root_dir()).join(".mk1_voice_monitor.pid");
        let mut discovered = None;

        if let Some(pid) = read_pid_from_file(&pid_file) {
            if is_pid_running(pid) {
                discovered = Some(pid);
            }
        }

        if discovered.is_none() {
            discovered = find_voice_monitor_pid()?;
            if let Some(pid) = discovered {
                let _ = fs::write(&pid_file, pid.to_string());
            } else {
                let _ = fs::remove_file(&pid_file);
            }
        }

        set_tracked_pid(discovered);
        running = discovered.is_some();
        discovered
    };

    Ok(VoiceStatus {
        running,
        detail: if running {
            let phase = if is_capture_active() { "capturing" } else { "ready" };
            format!("voice helper {} (pid: {:?})", phase, pid)
        } else {
            set_capture_active(false);
            "voice helper stopped".into()
        },
    })
}

#[tauri::command]
fn voice_feed() -> Result<VoiceFeed, String> {
    let log = VOICE_LOG.lock().map_err(|_| "voice log mutex poisoned")?;
    Ok(VoiceFeed { lines: log.clone() })
}

#[tauri::command]
fn service_start_api_local() -> Result<serde_json::Value, String> {
    let script = mk1_script_path("start_mk1_api.ps1");
    if !Path::new(&script).exists() {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "start_script_missing",
            "path": script,
        }));
    }

    let cwd = mk1_root_dir();

    // Run startup script directly so we can return real stdout/stderr if it fails.
    let ps = format!(
        "Set-Location -LiteralPath '{}' ; & '{}'",
        ps_single_quote(&cwd),
        ps_single_quote(&script)
    );

    let out = run_powershell(&ps)?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();

    if !out.status.success() {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "start_script_failed",
            "script": script,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": out.status.code(),
        }));
    }

    let healthy = wait_for_api_up(Duration::from_secs(8));
    if !healthy {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "api_not_reachable_after_start",
            "script": script,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": out.status.code(),
        }));
    }

    Ok(serde_json::json!({
        "ok": true,
        "script": script,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": out.status.code(),
        "verified": true,
    }))
}

#[tauri::command]
fn service_stop_api_local() -> Result<serde_json::Value, String> {
    let script = mk1_script_path("mk1_api.ps1");
    if !Path::new(&script).exists() {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "shutdown_script_missing",
            "path": script,
        }));
    }

    let cwd = mk1_root_dir();
    let ps = format!(
        "Set-Location -LiteralPath '{}' ; & '{}' shutdown -Port 8000 -ForceShutdown",
        ps_single_quote(&cwd),
        ps_single_quote(&script)
    );

    let out = run_powershell(&ps)?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();

    if !out.status.success() {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "shutdown_script_failed",
            "script": script,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": out.status.code(),
        }));
    }

    let verified_down = wait_for_api_down(Duration::from_secs(20));
    if !verified_down {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "api_not_reachable_after_shutdown",
            "script": script,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": out.status.code(),
        }));
    }

    Ok(serde_json::json!({
        "ok": true,
        "script": script,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": out.status.code(),
        "verified": true,
    }))
}

#[tauri::command]
fn service_start_voice_loop_local() -> Result<serde_json::Value, String> {
    let script = mk1_script_path("start_mina_voice_monitor.ps1");
    if !Path::new(&script).exists() {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "voice_start_script_missing",
            "path": script,
        }));
    }

    let cwd = mk1_root_dir();
    let pid = spawn_visible_powershell_file(&script, &cwd)?;
    let verified_pid = wait_for_voice_monitor_running(Duration::from_secs(12))?;

    if verified_pid.is_none() {
        return Ok(serde_json::json!({
            "ok": false,
            "error": "voice_monitor_failed_to_start",
            "launch_pid": pid,
            "script": script,
            "detail": "voice monitor did not report a live process before timeout",
        }));
    }

    Ok(serde_json::json!({
        "ok": true,
        "pid": pid,
        "monitor_pid": verified_pid,
        "script": script,
        "verified": true,
    }))
}

#[tauri::command]
fn service_stop_voice_loop_local() -> Result<serde_json::Value, String> {
        let root = mk1_root_dir();
        let pid_file = Path::new(&root).join(".mk1_voice_monitor.pid");

        let mut stopped_pid: Option<u32> = None;
        if let Some(pid) = read_pid_from_file(&pid_file) {
                if is_pid_running(pid) {
                        let kill_cmd = format!("Stop-Process -Id {} -Force -ErrorAction SilentlyContinue", pid);
                        let _ = run_powershell(&kill_cmd);
                        stopped_pid = Some(pid);
                }
        }

        // Fallback: kill known monitor/loop processes by command line.
        let sweep = r#"
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -like '*start_mina_voice_monitor.ps1*' -or
            $_.CommandLine -like '*mina_windows_voice_loop.py*'
        )
    } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }
"#;
        let _ = run_powershell(sweep);

        let _ = fs::remove_file(&pid_file);
        let _ = fs::remove_file(Path::new(&env::temp_dir()).join("mina_voice_monitor.mute"));

        set_capture_active(false);
        set_tracked_pid(None);

        Ok(serde_json::json!({
                "ok": true,
                "stopped_pid": stopped_pid,
                "pid_file": pid_file.to_string_lossy().to_string(),
        }))
}

#[tauri::command]
fn service_voice_loop_status() -> Result<VoiceStatus, String> {
    let pid_file = Path::new(&mk1_root_dir()).join(".mk1_voice_monitor.pid");
    if !pid_file.exists() {
        return Ok(VoiceStatus {
            running: false,
            detail: "voice loop not running".into(),
        });
    }

    let Some(pid) = read_pid_from_file(&pid_file) else {
        return Ok(VoiceStatus {
            running: false,
            detail: "voice loop pid file unreadable".into(),
        });
    };

    let running = is_pid_running(pid);
    Ok(VoiceStatus {
        running,
        detail: if running {
            format!("voice loop running (pid: {pid})")
        } else {
            "voice loop pid exists but process is down".into()
        },
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            ping_http,
            gui_chat_send,
            voice_start,
            voice_stop,
            voice_restart,
            voice_status,
            voice_feed,
            voice_list_devices,
            voice_get_input_device,
            voice_set_input_device,
            service_start_api_local,
            service_stop_api_local,
            service_start_voice_loop_local,
            service_stop_voice_loop_local,
            service_voice_loop_status,
            window_save_placement,
            window_restore_placement,
            window_move_to_secondary,
            window_toggle_fullscreen,
            open_ostranauts_companion,
            close_ostranauts_companion
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

