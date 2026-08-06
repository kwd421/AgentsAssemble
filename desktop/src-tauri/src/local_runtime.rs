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
        let data_root = app
            .path()
            .app_local_data_dir()
            .map_err(|error| format!("cannot locate desktop data directory: {error}"))?;
        let runtime_root = data_root.join("local-runtime");
        fs::create_dir_all(&runtime_root)
            .map_err(|error| format!("cannot create local runtime directory: {error}"))?;
        let stderr_path = runtime_root.join("server.stderr.log");

        if self.owned_child_is_running()? {
            let server = self
                .current_server()
                .ok_or_else(|| "The owned local runtime did not report its address.".to_owned())?;
            return self.wait_until_ready(&server, &stderr_path);
        }
        self.server
            .lock()
            .expect("local runtime server lock")
            .take();

        let (mut command, description) = local_server_command(app, &runtime_root)?;
        configure_process_group(&mut command);
        let stdout_path = runtime_root.join("server.stdout.log");
        let stdout_log = create_log(&stdout_path)?;
        command
            .env("AGENTSASSEMBLE_DESKTOP_RUNTIME", "1")
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
        self.wait_until_ready(&server, &stderr_path)
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

    fn owned_child_is_running(&self) -> Result<bool, String> {
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
        owned_child.take();
        Ok(false)
    }

    pub fn current_server(&self) -> Option<Url> {
        self.server
            .lock()
            .expect("local runtime server lock")
            .clone()
    }

    pub fn stop(&self) {
        if let Some(mut child) = self.child.lock().expect("local runtime lock").take() {
            terminate_process_tree(&mut child);
        }
        self.server
            .lock()
            .expect("local runtime server lock")
            .take();
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
    if child.try_wait().ok().flatten().is_none() {
        unsafe {
            libc::kill(process_group, libc::SIGKILL);
        }
    }
    let _ = child.wait();
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
