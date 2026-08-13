use std::{
    env,
    fs::{self, File},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdout, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Manager};
use url::Url;

#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::server_url::{agentsassemble_server_is_ready, normalized_server_url};

const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);
const DESKTOP_RUNTIME_URL_PREFIX: &str = "AgentsAssemble desktop runtime: ";

#[derive(Default)]
pub struct LocalRuntime {
    startup: Mutex<()>,
    child: Mutex<Option<Child>>,
    server: Mutex<Option<Url>>,
}

impl LocalRuntime {
    pub fn ensure_running(&self, app: &AppHandle) -> Result<Url, String> {
        let _startup = self.startup.lock().expect("local runtime startup lock");
        // Same absolute product data root as CLI `gui` (identity, rooms).
        // Port/process ownership stay separate; only on-disk state is shared.
        let runtime_root = default_user_data_root();
        fs::create_dir_all(&runtime_root)
            .map_err(|error| format!("cannot create local runtime directory: {error}"))?;
        let stderr_path = runtime_root.join("server.stderr.log");

        if self.owned_runtime_is_running()? {
            let server = self
                .current_server()
                .ok_or_else(|| "The owned local runtime did not report its address.".to_owned())?;
            return self.wait_until_ready(&server, &stderr_path);
        }
        // Prefer one engine per shared data root: attach to a healthy registry
        // entry instead of starting a second process.
        if let Some(existing) = discover_reusable_engine(&runtime_root) {
            *self.server.lock().expect("local runtime server lock") = Some(existing.clone());
            return Ok(existing);
        }
        if let Some(mut stale_child) = self.child.lock().expect("local runtime lock").take() {
            terminate_process_tree(&mut stale_child);
        }
        self.server
            .lock()
            .expect("local runtime server lock")
            .take();

        let (mut command, description) = local_server_command(app, &runtime_root)?;
        configure_process_group(&mut command);
        let stdout_path = runtime_root.join("server.stdout.log");
        let stdout_log = create_log(&stdout_path)?;
        // Breadcrumb so empty logs mean "spawn never returned" rather than
        // "child started and produced no output".
        {
            let mut breadcrumb = stdout_log.try_clone().map_err(|error| {
                format!("cannot clone startup log {}: {error}", stdout_path.display())
            })?;
            writeln!(
                breadcrumb,
                "AgentsAssemble desktop: launching {description}"
            )
            .map_err(|error| {
                format!(
                    "cannot write startup log {}: {error}",
                    stdout_path.display()
                )
            })?;
        }
        command
            .env("AGENTSASSEMBLE_DESKTOP_RUNTIME", "1")
            .env(
                "PATH",
                prepend_user_cli_path(env::var_os("PATH").unwrap_or_default()),
            )
            .stdout(Stdio::piped());
        command.stderr(Stdio::from(create_log(&stderr_path)?));
        let mut child = command
            .spawn()
            .map_err(|error| format!("cannot start {description}: {error}"))?;
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                terminate_process_tree(&mut child);
                return Err(format!("cannot capture startup output from {description}"));
            }
        };
        let reported_server = capture_reported_server(stdout, stdout_log);
        *self.child.lock().expect("local runtime lock") = Some(child);

        let server = self.wait_for_reported_server(reported_server, &stderr_path)?;
        *self.server.lock().expect("local runtime server lock") = Some(server.clone());
        let ready = self.wait_until_ready(&server, &stderr_path)?;
        if let Some(child) = self.child.lock().expect("local runtime lock").as_ref() {
            let _ = write_local_engine_registry(&runtime_root, &ready, child.id());
        }
        Ok(ready)
    }

    fn wait_for_reported_server(
        &self,
        reported_server: mpsc::Receiver<Result<Url, String>>,
        stderr_path: &Path,
    ) -> Result<Url, String> {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while Instant::now() < deadline {
            match reported_server.recv_timeout(Duration::from_millis(100)) {
                Ok(Ok(server)) => return Ok(server),
                Ok(Err(error)) => return self.fail_startup(&error, stderr_path),
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return self.fail_startup(
                        "The local runtime closed its startup output before reporting an address.",
                        stderr_path,
                    );
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            if let Some(status) = self.owned_child_status()? {
                return self.fail_startup(
                    &format!("The local runtime exited with {status} before reporting an address."),
                    stderr_path,
                );
            }
        }
        self.fail_startup(
            "The local runtime did not report its address within 45 seconds.",
            stderr_path,
        )
    }

    fn wait_until_ready(&self, server: &Url, stderr_path: &Path) -> Result<Url, String> {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while Instant::now() < deadline {
            if let Some(status) = self.owned_child_status()? {
                return self.fail_startup(
                    &format!("The local runtime exited with {status}."),
                    stderr_path,
                );
            }
            if agentsassemble_server_is_ready(server) {
                return Ok(server.clone());
            }
            thread::sleep(Duration::from_millis(100));
        }

        self.fail_startup(
            "The local runtime did not become ready within 45 seconds.",
            stderr_path,
        )
    }

    fn owned_child_status(&self) -> Result<Option<std::process::ExitStatus>, String> {
        let mut owned_child = self.child.lock().expect("local runtime lock");
        let Some(child) = owned_child.as_mut() else {
            return Err("The local runtime startup was cancelled.".to_owned());
        };
        child
            .try_wait()
            .map_err(|error| format!("cannot inspect local runtime: {error}"))
    }

    fn fail_startup<T>(&self, message: &str, stderr_path: &Path) -> Result<T, String> {
        if let Some(mut child) = self.child.lock().expect("local runtime lock").take() {
            terminate_process_tree(&mut child);
        }
        self.server
            .lock()
            .expect("local runtime server lock")
            .take();
        Err(format!("{message} Details: {}", stderr_path.display()))
    }

    fn owned_runtime_is_running(&self) -> Result<bool, String> {
        let mut owned_child = self.child.lock().expect("local runtime lock");
        let Some(child) = owned_child.as_mut() else {
            return Ok(false);
        };
        if child
            .try_wait()
            .map_err(|error| format!("cannot inspect local runtime: {error}"))?
            .is_none()
        {
            return Ok(true);
        }
        Ok(owned_process_group_is_running(child))
    }

    pub fn current_server(&self) -> Option<Url> {
        self.server
            .lock()
            .expect("local runtime server lock")
            .clone()
    }

    pub fn stop(&self) {
        let owned_url = self.current_server();
        let owned_pid = self
            .child
            .lock()
            .expect("local runtime lock")
            .as_ref()
            .map(Child::id);
        if let Some(mut child) = self.child.lock().expect("local runtime lock").take() {
            terminate_process_tree(&mut child);
        }
        self.server
            .lock()
            .expect("local runtime server lock")
            .take();
        if let (Some(url), Some(pid)) = (owned_url, owned_pid) {
            clear_local_engine_registry(&default_user_data_root(), &url, pid);
        }
    }
}

fn create_log(path: &Path) -> Result<File, String> {
    File::create(path).map_err(|error| format!("cannot open {}: {error}", path.display()))
}

fn capture_reported_server(
    stdout: ChildStdout,
    mut log: File,
) -> mpsc::Receiver<Result<Url, String>> {
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut reported = false;
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    let _ = log.write_all(line.as_bytes());
                    let _ = log.flush();
                    if !reported {
                        if let Some(raw_url) = line.trim().strip_prefix(DESKTOP_RUNTIME_URL_PREFIX)
                        {
                            reported = true;
                            let result = validate_reported_server(raw_url);
                            let _ = sender.send(result);
                        }
                    }
                }
                Err(error) => {
                    if !reported {
                        let _ = sender.send(Err(format!(
                            "cannot read local runtime startup output: {error}"
                        )));
                    }
                    break;
                }
            }
        }
    });
    receiver
}

fn validate_reported_server(raw: &str) -> Result<Url, String> {
    let server = normalized_server_url(raw)?;
    if server.scheme() != "http"
        || server.host_str() != Some("127.0.0.1")
        || server.port().is_none_or(|port| port == 0)
    {
        return Err("The local runtime reported a non-loopback or invalid address.".to_owned());
    }
    Ok(server)
}

fn local_server_command(app: &AppHandle, output_root: &Path) -> Result<(Command, String), String> {
    if let Some(executable) = bundled_server_executable(app) {
        let mut command = Command::new(&executable);
        command.arg("gui");
        add_server_arguments(&mut command, output_root);
        return Ok((command, executable.display().to_string()));
    }

    let source_root = source_checkout_root();
    if !source_root.join("pyproject.toml").is_file() {
        return Err(
            "This development build has no bundled local runtime. Rebuild it from an AgentsAssemble checkout or connect to a cloud server."
                .to_owned(),
        );
    }
    let python = source_python(&source_root);
    let mut command = Command::new(&python);
    command
        .arg("-m")
        .arg("agentsassemble.cli")
        .arg("gui")
        .current_dir(&source_root);
    add_server_arguments(&mut command, output_root);
    Ok((
        command,
        format!("{} -m agentsassemble.cli", python.display()),
    ))
}

fn add_server_arguments(command: &mut Command, output_root: &Path) {
    command
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg("0")
        .arg("--output-root")
        .arg(output_root);
}

fn bundled_server_executable(app: &AppHandle) -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "agentsassemble-server.exe"
    } else {
        "agentsassemble-server"
    };
    let beside_application = env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join(name)));
    beside_application
        .filter(|path| path.is_file())
        .or_else(|| {
            app.path()
                .resource_dir()
                .ok()
                .map(|root| root.join("bin").join(name))
                .filter(|path| path.is_file())
        })
}

fn source_checkout_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .components()
        .collect()
}

fn source_python(source_root: &Path) -> PathBuf {
    if let Some(explicit) = env::var_os("AGENTSASSEMBLE_PYTHON") {
        return PathBuf::from(explicit);
    }
    let virtualenv = if cfg!(windows) {
        source_root.join(".venv/Scripts/python.exe")
    } else {
        source_root.join(".venv/bin/python")
    };
    if virtualenv.is_file() {
        return virtualenv;
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

fn write_local_engine_registry(output_root: &Path, server: &Url, pid: u32) -> Result<(), String> {
    let path = output_root.join("runtime/local-engine.json");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create {}: {error}", parent.display()))?;
    }
    let payload = serde_json::json!({
        "schema": 1,
        "server_url": server.as_str(),
        "pid": pid,
        "instance_id": format!("desktop-{pid}"),
        "updated_at": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_secs_f64())
            .unwrap_or(0.0),
    });
    let encoded = serde_json::to_string_pretty(&payload)
        .map_err(|error| format!("cannot encode local engine registry: {error}"))?;
    fs::write(&path, encoded + "\n")
        .map_err(|error| format!("cannot write {}: {error}", path.display()))
}

fn clear_local_engine_registry(output_root: &Path, server: &Url, pid: u32) {
    let path = output_root.join("runtime/local-engine.json");
    let Ok(raw) = fs::read_to_string(&path) else {
        return;
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return;
    };
    let file_pid = value.get("pid").and_then(|v| v.as_u64()).unwrap_or(0);
    let file_url = value
        .get("server_url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .trim_end_matches('/');
    let want = server.as_str().trim_end_matches('/');
    if file_pid == u64::from(pid) && file_url == want {
        let _ = fs::remove_file(path);
    }
}

/// Read `runtime/local-engine.json` written by a live GUI for this data root.
fn discover_reusable_engine(output_root: &Path) -> Option<Url> {
    let path = output_root.join("runtime/local-engine.json");
    let raw = fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    if value.get("schema").and_then(|v| v.as_u64()) != Some(1) {
        return None;
    }
    let server_url = value.get("server_url")?.as_str()?.trim();
    if server_url.is_empty() {
        return None;
    }
    if let Some(pid) = value.get("pid").and_then(|v| v.as_u64()) {
        if pid > 0 && !process_is_running(pid as u32) {
            return None;
        }
    }
    let server = validate_reported_server(server_url).ok()?;
    if !agentsassemble_server_is_ready(&server) {
        return None;
    }
    Some(server)
}

#[cfg(unix)]
fn process_is_running(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    let result = unsafe { libc::kill(pid as i32, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(windows)]
fn process_is_running(pid: u32) -> bool {
    // Best-effort: absence of a cheap cross-check still allows readiness probe.
    let _ = pid;
    true
}

/// Shared with Python `agentsassemble.application.user_data_root.default_output_root`.
fn default_user_data_root() -> PathBuf {
    if let Ok(override_root) = env::var("AGENTSASSEMBLE_OUTPUT_ROOT") {
        let trimmed = override_root.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    #[cfg(target_os = "macos")]
    {
        if let Some(home) = home {
            return home.join("Library/Application Support/AgentsAssemble");
        }
    }
    #[cfg(target_os = "windows")]
    {
        if let Some(appdata) = env::var_os("APPDATA").map(PathBuf::from) {
            return appdata.join("AgentsAssemble");
        }
        if let Some(home) = home {
            return home.join("AppData/Roaming/AgentsAssemble");
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        if let Some(xdg) = env::var_os("XDG_DATA_HOME").map(PathBuf::from) {
            return xdg.join("AgentsAssemble");
        }
        if let Some(home) = home {
            return home.join(".local/share/AgentsAssemble");
        }
    }
    PathBuf::from("AgentsAssemble")
}

/// GUI-launched apps often inherit a short PATH. Prepend the same user CLI
/// locations the Python catalog uses so subscription providers stay discoverable.
fn prepend_user_cli_path(existing: std::ffi::OsString) -> std::ffi::OsString {
    use std::ffi::OsString;

    let mut parts: Vec<PathBuf> = Vec::new();
    let mut seen = std::collections::BTreeSet::new();
    let push = |parts: &mut Vec<PathBuf>, seen: &mut std::collections::BTreeSet<PathBuf>, path: PathBuf| {
        if path.is_dir() && seen.insert(path.clone()) {
            parts.push(path);
        }
    };

    if let Some(home) = env::var_os("HOME").map(PathBuf::from) {
        push(&mut parts, &mut seen, home.join(".local/bin"));
        push(&mut parts, &mut seen, home.join(".grok/bin"));
        push(&mut parts, &mut seen, home.join(".cargo/bin"));
    }
    #[cfg(target_os = "macos")]
    {
        push(&mut parts, &mut seen, PathBuf::from("/opt/homebrew/bin"));
        push(&mut parts, &mut seen, PathBuf::from("/usr/local/bin"));
    }

    for part in env::split_paths(&existing) {
        if !part.as_os_str().is_empty() && seen.insert(part.clone()) {
            parts.push(part);
        }
    }
    env::join_paths(parts).unwrap_or_else(|_| OsString::from(existing))
}

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    command.process_group(0);
}

#[cfg(windows)]
fn configure_process_group(command: &mut Command) {
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
}

#[cfg(unix)]
fn terminate_process_tree(child: &mut Child) {
    let process_group = -(child.id() as i32);
    unsafe {
        libc::kill(process_group, libc::SIGTERM);
    }
    thread::sleep(Duration::from_millis(250));
    if owned_process_group_is_running(child) {
        unsafe {
            libc::kill(process_group, libc::SIGKILL);
        }
    }
    let _ = child.wait();
}

#[cfg(unix)]
fn owned_process_group_is_running(child: &Child) -> bool {
    let result = unsafe { libc::kill(-(child.id() as i32), 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use super::*;

    #[test]
    fn reported_runtime_must_be_an_already_bound_loopback_endpoint() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind owned runtime");
        let address = listener.local_addr().expect("owned runtime address");
        let server =
            validate_reported_server(&format!("http://{address}")).expect("bound loopback report");

        assert_eq!(server.host_str(), Some("127.0.0.1"));
        assert_eq!(server.port(), Some(address.port()));
        assert!(validate_reported_server("http://example.com:8765").is_err());
        assert!(validate_reported_server("https://127.0.0.1:8765").is_err());
    }
}

#[cfg(windows)]
fn terminate_process_tree(child: &mut Child) {
    let _ = Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .creation_flags(0x0800_0000)
        .status();
    let _ = child.wait();
}

#[cfg(windows)]
fn owned_process_group_is_running(_child: &Child) -> bool {
    false
}
