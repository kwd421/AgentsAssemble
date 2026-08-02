use std::{sync::Mutex, time::Duration};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use tauri_plugin_updater::{Update, UpdaterExt};
use url::Url;

use crate::caller_is_local_shell;

const UPDATE_ENDPOINT: Option<&str> = option_env!("AGENTSASSEMBLE_UPDATE_ENDPOINT");
const UPDATE_PUBLIC_KEY: Option<&str> = option_env!("AGENTSASSEMBLE_UPDATE_PUBLIC_KEY");

#[derive(Default)]
pub struct PendingDesktopUpdate(Mutex<Option<Update>>);

struct UpdateSource {
    endpoint: Url,
    public_key: String,
}

impl UpdateSource {
    fn configured(
        endpoint: Option<&str>,
        public_key: Option<&str>,
    ) -> Result<Option<Self>, String> {
        let endpoint = endpoint.unwrap_or_default().trim();
        let public_key = public_key.unwrap_or_default().trim();
        if endpoint.is_empty() && public_key.is_empty() {
            return Ok(None);
        }
        if endpoint.is_empty() || public_key.is_empty() {
            return Err(
                "Desktop updates require both AGENTSASSEMBLE_UPDATE_ENDPOINT and AGENTSASSEMBLE_UPDATE_PUBLIC_KEY at build time."
                    .to_owned(),
            );
        }
        let endpoint = Url::parse(endpoint)
            .map_err(|_| "The configured desktop update endpoint is invalid.".to_owned())?;
        if endpoint.scheme() != "https" || endpoint.host_str().is_none() {
            return Err("The desktop update endpoint must be an HTTPS URL.".to_owned());
        }
        Ok(Some(Self {
            endpoint,
            public_key: public_key.to_owned(),
        }))
    }

    fn from_build() -> Result<Option<Self>, String> {
        Self::configured(UPDATE_ENDPOINT, UPDATE_PUBLIC_KEY)
    }
}

#[tauri::command]
pub async fn check_desktop_update(window: WebviewWindow, app: AppHandle) -> Result<Value, String> {
    caller_is_local_shell(&window)?;
    let Some(source) = UpdateSource::from_build()? else {
        return Ok(json!({"state": "not_configured"}));
    };
    let updater = app
        .updater_builder()
        .endpoints(vec![source.endpoint])
        .map_err(|error| format!("cannot configure desktop update endpoint: {error}"))?
        .pubkey(source.public_key)
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|error| format!("cannot initialize desktop updater: {error}"))?;
    let Some(update) = updater
        .check()
        .await
        .map_err(|error| format!("desktop update check failed: {error}"))?
    else {
        return Ok(json!({"state": "current"}));
    };
    let response = json!({
        "state": "available",
        "version": update.version,
        "notes": update.body,
        "published_at": update.date.map(|value| value.to_string()),
    });
    *app.state::<PendingDesktopUpdate>()
        .0
        .lock()
        .expect("desktop update lock") = Some(update);
    Ok(response)
}

#[tauri::command]
pub async fn install_desktop_update(window: WebviewWindow, app: AppHandle) -> Result<(), String> {
    caller_is_local_shell(&window)?;
    let update = app
        .state::<PendingDesktopUpdate>()
        .0
        .lock()
        .expect("desktop update lock")
        .take()
        .ok_or_else(|| "No checked desktop update is ready to install.".to_owned())?;
    let progress_app = app.clone();
    let mut downloaded = 0_u64;
    update
        .download_and_install(
            move |chunk, total| {
                downloaded = downloaded.saturating_add(chunk as u64);
                let _ = progress_app.emit(
                    "desktop-update-progress",
                    json!({
                        "phase": "downloading",
                        "downloaded": downloaded,
                        "total": total.unwrap_or_default(),
                    }),
                );
            },
            {
                let progress_app = app.clone();
                move || {
                    let _ =
                        progress_app.emit("desktop-update-progress", json!({"phase": "finished"}));
                }
            },
        )
        .await
        .map_err(|error| format!("desktop update install failed: {error}"))?;
    app.request_restart();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::UpdateSource;

    #[test]
    fn update_source_stays_disabled_until_both_build_inputs_exist() {
        assert!(UpdateSource::configured(None, None).unwrap().is_none());
        assert!(
            UpdateSource::configured(Some("https://updates.example.test/latest.json"), None)
                .is_err()
        );
        assert!(UpdateSource::configured(None, Some("public-key")).is_err());
    }

    #[test]
    fn update_source_rejects_an_insecure_release_origin() {
        assert!(UpdateSource::configured(
            Some("http://updates.example.test/latest.json"),
            Some("public-key")
        )
        .is_err());
    }
}
