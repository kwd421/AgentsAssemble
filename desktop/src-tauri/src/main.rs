mod local_runtime;
mod room_directory;
mod server_url;

use std::sync::{Arc, RwLock};

use local_runtime::LocalRuntime;
use server_url::{is_local_app_url, normalized_server_url, same_origin};
use tauri::{Manager, RunEvent, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

#[derive(Default)]
struct NavigationState {
    server_origin: Arc<RwLock<Option<Url>>>,
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
async fn start_local_runtime(
    window: WebviewWindow,
    app: tauri::AppHandle,
) -> Result<String, String> {
    caller_is_local_shell(&window)?;
    let runtime_app = app.clone();
    let server = tauri::async_runtime::spawn_blocking(move || {
        let runtime = runtime_app.state::<LocalRuntime>();
        let server = runtime.ensure_running(&runtime_app)?;
        let _ = room_directory::refresh_local_rooms(&runtime_app, &server);
        Ok::<Url, String>(server)
    })
    .await
    .map_err(|error| format!("local runtime startup worker failed: {error}"))??;
    Ok(server.to_string())
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

#[tauri::command]
fn load_cached_room_directory(
    window: WebviewWindow,
    app: tauri::AppHandle,
) -> Result<String, String> {
    caller_is_local_shell(&window)?;
    room_directory::load(&app)
}

fn main() {
    let navigation = NavigationState::default();
    let navigation_guard = navigation.server_origin.clone();
    let app = tauri::Builder::default()
        .manage(navigation)
        .manage(LocalRuntime::default())
        .invoke_handler(tauri::generate_handler![
            start_local_runtime,
            open_server,
            load_cached_room_directory
        ])
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
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
            if let Ok(server) = normalized_server_url(server_url::DEFAULT_LOCAL_SERVER_URL) {
                let _ = room_directory::refresh_local_rooms(app, &server);
            }
            app.state::<LocalRuntime>().stop();
        }
    });
}
