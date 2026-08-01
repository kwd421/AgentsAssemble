use std::{
    env,
    fs::{self, File},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Manager};
use url::Url;

#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::server_url::{
    agentsassemble_server_is_ready, normalized_server_url, tcp_endpoint_is_reachable,
    DEFAULT_LOCAL_SERVER_URL,
};

const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Default)]
pub struct LocalRuntime {
    child: Mutex<Option<Child>>,
}

impl LocalRuntime {
    pub fn ensure_running(&self, app: &AppHandle) -> Result<Url, String> {
        let server = normalized_server_url(DEFAULT_LOCAL_SERVER_URL)?;
        if agentsassemble_server_is_ready(&server) {
            return Ok(server);
        }
        if tcp_endpoint_is_reachable(&server) {
            return Err(
                "Port 8765 is in use by something other than AgentsAssemble. Close it or choose another server."
                    .to_owned(),
            );
        }

        let data_root = app
            .path()
            .app_local_data_dir()
            .map_err(|error| format!("cannot locate desktop data directory: {error}"))?;
        let runtime_root = data_root.join("local-runtime");
        fs::create_dir_all(&runtime_root)
            .map_err(|error| format!("cannot create local runtime directory: {error}"))?;

        let (mut command, description) = local_server_command(app, &runtime_root)?;
        configure_process_group(&mut command);
        let stdout_path = runtime_root.join("server.stdout.log");
        let stderr_path = runtime_root.join("server.stderr.log");
        command.stdout(Stdio::from(create_log(&stdout_path)?));
        command.stderr(Stdio::from(create_log(&stderr_path)?));
        let mut child = command
            .spawn()
            .map_err(|error| format!("cannot start {description}: {error}"))?;

        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while Instant::now() < deadline {
            if agentsassemble_server_is_ready(&server) {
                *self.child.lock().expect("local runtime lock") = Some(child);
                return Ok(server);
            }
            if let Some(status) = child
                .try_wait()
                .map_err(|error| format!("cannot inspect local runtime: {error}"))?
            {
                return Err(format!(
                    "The local runtime exited with {status}. Details: {}",
                    stderr_path.display()
                ));
            }
            thread::sleep(Duration::from_millis(100));
        }

        terminate_process_tree(&mut child);
        Err(format!(
            "The local runtime did not become ready within 45 seconds. Details: {}",
            stderr_path.display()
        ))
    }

    pub fn stop(&self) {
        let Some(mut child) = self.child.lock().expect("local runtime lock").take() else {
            return;
        };
        terminate_process_tree(&mut child);
    }
}

fn create_log(path: &Path) -> Result<File, String> {
    File::create(path).map_err(|error| format!("cannot open {}: {error}", path.display()))
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
        .arg("8765")
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

#[cfg(windows)]
fn terminate_process_tree(child: &mut Child) {
    let _ = Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .creation_flags(0x0800_0000)
        .status();
    let _ = child.wait();
}
