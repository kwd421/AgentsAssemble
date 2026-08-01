mod local_runtime;
mod server_url;

use std::{
    env, process,
    sync::{Arc, RwLock},
};

use local_runtime::LocalRuntime;
use server_url::{is_local_app_url, normalized_server_url, same_origin};
use tauri::{Manager, RunEvent, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

const SERVER_URL_ENV: &str = "AGENTSASSEMBLE_SERVER_URL";

#[derive(Default)]
struct NavigationState {
    server_origin: Arc<RwLock<Option<Url>>>,
}

fn server_argument<I, S>(args: I) -> Result<Option<String>, String>
where
    I: IntoIterator<Item = S>,
    S: Into<String>,
{
    let mut args = args.into_iter().map(Into::into);
    while let Some(argument) = args.next() {
        if argument == "--server" {
            return args
                .next()
                .filter(|value| !value.trim().is_empty())
                .map(Some)
                .ok_or_else(|| "--server requires an HTTP(S) URL".to_owned());
        }
        if let Some(value) = argument.strip_prefix("--server=") {
            if value.trim().is_empty() {
                return Err("--server requires an HTTP(S) URL".to_owned());
            }
            return Ok(Some(value.to_owned()));
        }
    }
    Ok(None)
}

fn configured_startup_server<I, S>(
    args: I,
    environment_url: Option<&str>,
) -> Result<Option<Url>, String>
where
    I: IntoIterator<Item = S>,
    S: Into<String>,
{
    server_argument(args)?
        .or_else(|| environment_url.map(str::to_owned))
        .map(|raw| normalized_server_url(&raw))
        .transpose()
}

fn startup_page(server: Option<&Url>) -> WebviewUrl {
    let Some(server) = server else {
        return WebviewUrl::App("index.html".into());
    };
    let encoded: String =
        url::form_urlencoded::byte_serialize(server.as_str().as_bytes()).collect();
    WebviewUrl::App(format!("index.html?server={encoded}").into())
}

fn caller_is_local_shell(window: &WebviewWindow) -> Result<(), String> {
    let current = window
        .url()
        .map_err(|error| format!("cannot inspect desktop window location: {error}"))?;
    if is_local_app_url(&current) {
        Ok(())
    } else {
        Err("This action is available only from the desktop connection screen.".to_owned())
    }
}

#[tauri::command]
fn start_local_runtime(
    window: WebviewWindow,
    app: tauri::AppHandle,
    runtime: State<'_, LocalRuntime>,
) -> Result<String, String> {
    caller_is_local_shell(&window)?;
    runtime.ensure_running(&app).map(|url| url.to_string())
}

#[tauri::command]
fn open_server(
    window: WebviewWindow,
    navigation: State<'_, NavigationState>,
    server: String,
) -> Result<(), String> {
    caller_is_local_shell(&window)?;
    let server = normalized_server_url(&server)?;
    *navigation
        .server_origin
        .write()
        .expect("navigation state lock") = Some(server.clone());
    if let Err(error) = window.navigate(server) {
        *navigation
            .server_origin
            .write()
            .expect("navigation state lock") = None;
        return Err(format!("cannot open the selected server: {error}"));
    }
    Ok(())
}

fn main() {
    let startup_server = configured_startup_server(
        env::args().skip(1),
        env::var(SERVER_URL_ENV).ok().as_deref(),
    )
    .unwrap_or_else(|message| {
        eprintln!("AgentsAssemble desktop configuration error: {message}");
        process::exit(2);
    });

    let navigation = NavigationState::default();
    let navigation_guard = navigation.server_origin.clone();
    let app = tauri::Builder::default()
        .manage(navigation)
        .manage(LocalRuntime::default())
        .invoke_handler(tauri::generate_handler![start_local_runtime, open_server])
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", startup_page(startup_server.as_ref()))
                .title("AgentsAssemble")
                .inner_size(1440.0, 900.0)
                .min_inner_size(900.0, 620.0)
                .on_navigation(move |candidate| {
                    let selected = navigation_guard.read().expect("navigation state lock");
                    match selected.as_ref() {
                        Some(server) => same_origin(candidate, server),
                        None => is_local_app_url(candidate),
                    }
                })
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("AgentsAssemble desktop client failed to initialize");

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            app.state::<LocalRuntime>().stop();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn startup_server_is_optional_and_command_line_wins() {
        assert!(configured_startup_server(Vec::<String>::new(), None)
            .unwrap()
            .is_none());
        let configured = configured_startup_server(
            ["--server", "https://rooms.example.test:9443"],
            Some("https://ignored.example.test"),
        )
        .unwrap()
        .unwrap();
        assert_eq!(configured.as_str(), "https://rooms.example.test:9443/");
    }

    #[test]
    fn startup_page_round_trips_an_explicit_cloud_server() {
        let server = normalized_server_url("https://rooms.example.test:9443").unwrap();
        let WebviewUrl::App(path) = startup_page(Some(&server)) else {
            panic!("connection screen must remain a bundled asset");
        };
        let path = path.to_string_lossy();
        let (_, query) = path.split_once('?').expect("startup query");
        let selected = url::form_urlencoded::parse(query.as_bytes())
            .find(|(key, _)| key == "server")
            .map(|(_, value)| value.into_owned());
        assert_eq!(selected.as_deref(), Some(server.as_str()));
    }
}
