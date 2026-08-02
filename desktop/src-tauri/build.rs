fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "start_local_runtime",
                "open_server",
                "load_cached_room_directory",
                "open_google_account_login",
                "check_desktop_update",
                "install_desktop_update",
            ]),
        ),
    )
    .expect("failed to build AgentsAssemble desktop metadata")
}
