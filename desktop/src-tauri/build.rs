fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "client_platform",
            "central_directory_url",
            "open_central_google_login",
            "start_local_runtime",
            "open_server",
            "open_server_link",
            "load_cached_room_directory",
            "cache_selected_room_directory",
            "check_desktop_update",
            "install_desktop_update",
        ]),
    ))
    .expect("failed to build AgentsAssemble desktop metadata")
}
