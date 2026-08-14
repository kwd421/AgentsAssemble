#[cfg(desktop)]
mod app_update;
#[cfg(desktop)]
mod local_runtime;
mod room_directory;
mod server_url;

use std::sync::{Arc, RwLock};

#[cfg(desktop)]
use local_runtime::LocalRuntime;
use server_url::{
    central_directory_origin, central_google_handoff_url, google_account_handoff_url,
    is_local_app_url, normalized_navigation_url, normalized_server_url, same_origin,
};
use tauri::Manager;
#[cfg(desktop)]
use tauri::RunEvent;
use tauri::{State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

#[derive(Default)]
struct NavigationState {
    server_origin: Arc<RwLock<Option<Url>>>,
}

fn caller_is_local_shell(window: &WebviewWindow) -> Result<(), String> {
    let current = window
        .url()
        .map_err(|error| format!("cannot inspect client window location: {error}"))?;
    if is_local_app_url(&current) {
        Ok(())
    } else {
        Err("This action is available only from the client connection screen.".to_owned())
    }
}

fn caller_selected_server(
    window: &WebviewWindow,
    navigation: &NavigationState,
) -> Result<Url, String> {
    let current = window
        .url()
        .map_err(|error| format!("cannot inspect client window location: {error}"))?;
    let selected = navigation
        .server_origin
        .read()
        .expect("navigation state lock");
    let server = selected
        .as_ref()
        .ok_or_else(|| "No room server is open in this client window.".to_owned())?;
    if !same_origin(&current, server) {
        return Err("This action is available only from the selected room server.".to_owned());
    }
    Ok(server.clone())
}

fn server_is_loopback(server: &Url) -> bool {
    let Some(host) = server.host_str() else {
        return false;
    };
    host.eq_ignore_ascii_case("localhost")
        || host
            .trim_matches(['[', ']'])
            .parse::<std::net::IpAddr>()
            .is_ok_and(|address| address.is_loopback())
}

fn caller_can_open_central_google(
    window: &WebviewWindow,
    navigation: &NavigationState,
) -> Result<(), String> {
    if caller_is_local_shell(window).is_ok() {
        return Ok(());
    }
    let selected = caller_selected_server(window, navigation)?;
    if selected.scheme() == "http" && server_is_loopback(&selected) {
        return Ok(());
    }
    Err(
        "Central Google login is available only from the bundled shell or local engine."
            .to_owned(),
    )
}

#[tauri::command]
fn client_platform(window: WebviewWindow) -> Result<&'static str, String> {
    caller_is_local_shell(&window)?;
    #[cfg(mobile)]
    return Ok("mobile");
    #[cfg(not(mobile))]
    Ok("desktop")
}

#[tauri::command]
fn central_directory_url(window: WebviewWindow) -> Result<&'static str, String> {
    caller_is_local_shell(&window)?;
    normalized_server_url(central_directory_origin())
        .map_err(|error| format!("central directory configuration is invalid: {error}"))?;
    Ok(central_directory_origin())
}

#[tauri::command]
fn open_central_google_login(
    window: WebviewWindow,
    navigation: State<'_, NavigationState>,
    url: String,
) -> Result<(), String> {
    caller_can_open_central_google(&window, &navigation)?;
    let handoff = central_google_handoff_url(&url)?;
    tauri_plugin_opener::open_url(handoff.as_str(), None::<&str>)
        .map_err(|error| format!("cannot open the system browser: {error}"))
}

#[cfg(desktop)]
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

#[cfg(desktop)]
fn navigate_to_server(
    window: &WebviewWindow,
    navigation: &NavigationState,
    destination: Url,
    server: Url,
) -> Result<(), String> {
    *navigation
        .server_origin
        .write()
        .expect("navigation state lock") = Some(server);
    if let Err(error) = window.navigate(destination) {
        *navigation
            .server_origin
            .write()
            .expect("navigation state lock") = None;
        return Err(format!("cannot open the selected server: {error}"));
    }
    Ok(())
}

#[cfg(mobile)]
fn navigate_to_server(
    app: &tauri::AppHandle,
    navigation: &NavigationState,
    destination: Url,
    server: Url,
) -> Result<(), String> {
    if let Some(existing) = app.get_webview_window("room") {
        existing
            .destroy()
            .map_err(|error| format!("cannot close the previous room view: {error}"))?;
    }

    *navigation
        .server_origin
        .write()
        .expect("navigation state lock") = Some(server.clone());
    let navigation_server = server.clone();
    let result = WebviewWindowBuilder::new(app, "room", WebviewUrl::External(destination))
        .title("AgentsAssemble Room")
        .on_navigation(move |candidate| same_origin(candidate, &navigation_server))
        .build();
    if let Err(error) = result {
        *navigation
            .server_origin
            .write()
            .expect("navigation state lock") = None;
        return Err(format!("cannot open the selected server: {error}"));
    }
    Ok(())
}

#[tauri::command]
fn open_server(
    window: WebviewWindow,
    app: tauri::AppHandle,
    navigation: State<'_, NavigationState>,
    server: String,
) -> Result<(), String> {
    caller_is_local_shell(&window)?;
    let server = normalized_server_url(&server)?;
    #[cfg(desktop)]
    {
        let _ = app;
        navigate_to_server(&window, &navigation, server.clone(), server)
    }
    #[cfg(mobile)]
    {
        navigate_to_server(&app, &navigation, server.clone(), server)
    }
}

#[tauri::command]
fn open_server_link(
    window: WebviewWindow,
    app: tauri::AppHandle,
    navigation: State<'_, NavigationState>,
    url: String,
) -> Result<(), String> {
    caller_is_local_shell(&window)?;
    let (destination, server) = normalized_navigation_url(&url)?;
    #[cfg(desktop)]
    {
        let _ = app;
        navigate_to_server(&window, &navigation, destination, server)
    }
    #[cfg(mobile)]
    {
        navigate_to_server(&app, &navigation, destination, server)
    }
}

#[tauri::command]
fn load_cached_room_directory(
    window: WebviewWindow,
    app: tauri::AppHandle,
) -> Result<String, String> {
    caller_is_local_shell(&window)?;
    room_directory::load(&app)
}

#[tauri::command]
fn cache_selected_room_directory(
    window: WebviewWindow,
    navigation: State<'_, NavigationState>,
    app: tauri::AppHandle,
    rooms: String,
) -> Result<(), String> {
    let server = caller_selected_server(&window, &navigation)?;
    room_directory::store_selected_server_rooms(&app, &rooms, &server)
}

#[tauri::command]
fn open_google_account_login(
    window: WebviewWindow,
    navigation: State<'_, NavigationState>,
    url: String,
) -> Result<(), String> {
    let server = caller_selected_server(&window, &navigation)?;
    let handoff = google_account_handoff_url(&url, &server)?;
    tauri_plugin_opener::open_url(handoff.as_str(), None::<&str>)
        .map_err(|error| format!("cannot open the system browser: {error}"))
}

fn build_main_window(
    app: &mut tauri::App,
    navigation_guard: Arc<RwLock<Option<Url>>>,
) -> tauri::Result<()> {
    let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("AgentsAssemble")
        .on_navigation(move |candidate| {
            let selected = navigation_guard.read().expect("navigation state lock");
            match selected.as_ref() {
                Some(server) => same_origin(candidate, server),
                None => is_local_app_url(candidate),
            }
        });
    #[cfg(desktop)]
    let builder = builder
        .inner_size(1440.0, 900.0)
        .min_inner_size(900.0, 620.0);
    builder.build()?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(desktop)]
    if std::env::var_os("AGENTSASSEMBLE_CENTRAL_URL").is_none() {
        // The URL is public configuration, not a credential. Passing it via
        // the parent process lets the bundled Python engine enable the same
        // central identity directory without persisting any central bearer.
        std::env::set_var("AGENTSASSEMBLE_CENTRAL_URL", central_directory_origin());
    }

    let navigation = NavigationState::default();
    let navigation_guard = navigation.server_origin.clone();
    let builder = tauri::Builder::default()
        .manage(navigation)
        .plugin(tauri_plugin_opener::init());

    #[cfg(desktop)]
    let builder = builder
        .manage(LocalRuntime::default())
        .manage(app_update::PendingDesktopUpdate::default())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            app_update::check_desktop_update,
            app_update::install_desktop_update,
            client_platform,
            central_directory_url,
            open_central_google_login,
            start_local_runtime,
            open_server,
            open_server_link,
            load_cached_room_directory,
            cache_selected_room_directory,
            open_google_account_login
        ]);

    #[cfg(mobile)]
    let builder = builder
        .plugin(tauri_plugin_barcode_scanner::init())
        .invoke_handler(tauri::generate_handler![
            client_platform,
            central_directory_url,
            open_central_google_login,
            open_server,
            open_server_link,
            load_cached_room_directory,
            cache_selected_room_directory,
            open_google_account_login
        ]);

    let app = builder
        .setup(move |app| {
            build_main_window(app, navigation_guard)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("AgentsAssemble client failed to initialize");

    #[cfg(desktop)]
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            let runtime = app.state::<LocalRuntime>();
            if let Some(server) = runtime.current_server() {
                let _ = room_directory::refresh_local_rooms(app, &server);
            }
            runtime.stop();
        }
    });

    #[cfg(mobile)]
    app.run(|_, _| {});
}
