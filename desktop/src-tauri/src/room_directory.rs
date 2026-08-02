use std::{fs, path::PathBuf};
#[cfg(desktop)]
use std::{
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    time::Duration,
};

use serde_json::{json, Map, Value};
use tauri::{AppHandle, Manager};
use url::Url;

const ROOM_DIRECTORY_FILE: &str = "room-directory-v1.json";
const MAX_CACHED_ROOMS: usize = 128;
const MAX_PAYLOAD_BYTES: usize = 512 * 1024;

fn cache_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|root| root.join(ROOM_DIRECTORY_FILE))
        .map_err(|error| format!("cannot locate native room cache: {error}"))
}

fn bounded_text(value: Option<&Value>, max_chars: usize) -> String {
    value
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .filter(|character| !matches!(character, '\r' | '\n' | '\t'))
        .take(max_chars)
        .collect::<String>()
        .trim()
        .to_owned()
}

fn server_origin(value: Option<&Value>) -> Option<String> {
    let url = Url::parse(value?.as_str()?).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return None;
    }
    Some(url.origin().ascii_serialization())
}

fn sanitized_appearance(value: Option<&Value>) -> Option<Value> {
    let source = value?.as_object()?;
    let mut appearance = Map::new();
    for (key, max_chars) in [
        ("bannerPreset", 16),
        ("bannerImage", 256),
        ("iconImage", 256),
        ("iconLabel", 2),
        ("inviteScope", 16),
    ] {
        let text = bounded_text(source.get(key), max_chars);
        if !text.is_empty() {
            appearance.insert(key.to_owned(), Value::String(text));
        }
    }
    (!appearance.is_empty()).then_some(Value::Object(appearance))
}

pub fn sanitize_directory(payload: &str) -> Result<String, String> {
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err("native room cache is too large".to_owned());
    }
    let parsed: Value = serde_json::from_str(payload)
        .map_err(|error| format!("native room cache is invalid JSON: {error}"))?;
    let rooms = parsed
        .as_array()
        .ok_or_else(|| "native room cache must be an array".to_owned())?;
    let sanitized = rooms
        .iter()
        .take(MAX_CACHED_ROOMS)
        .filter_map(|room| {
            let source = room.as_object()?;
            let meeting_id = bounded_text(source.get("meetingId"), 128);
            if meeting_id.is_empty() {
                return None;
            }
            let remote_origin = server_origin(source.get("serverOrigin"));
            let room_origin = if source.get("roomOrigin").and_then(Value::as_str)
                == Some("remote_server")
                && remote_origin.is_some()
            {
                "remote_server"
            } else {
                "local"
            };
            let label = bounded_text(source.get("label"), 80);
            let mut output = json!({
                "id": bounded_text(source.get("id"), 128),
                "label": if label.is_empty() { meeting_id.clone() } else { label },
                "meetingId": meeting_id,
                "roomOrigin": room_origin,
                "topic": bounded_text(source.get("topic"), 160),
                "shortLabel": bounded_text(source.get("shortLabel"), 4),
                "createdAt": bounded_text(source.get("createdAt"), 64),
                "tone": bounded_text(source.get("tone"), 16),
            });
            if let Some(origin) = remote_origin.filter(|_| room_origin == "remote_server") {
                output["serverOrigin"] = Value::String(origin);
            }
            if let Some(appearance) = sanitized_appearance(source.get("appearance")) {
                output["appearance"] = appearance;
            }
            Some(output)
        })
        .collect::<Vec<_>>();
    serde_json::to_string(&sanitized)
        .map_err(|error| format!("cannot encode desktop room cache: {error}"))
}

pub fn load(app: &AppHandle) -> Result<String, String> {
    let path = cache_path(app)?;
    match fs::read_to_string(&path) {
        Ok(payload) => sanitize_directory(&payload),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok("[]".to_owned()),
        Err(error) => Err(format!("cannot read {}: {error}", path.display())),
    }
}

pub fn store(app: &AppHandle, payload: &str) -> Result<(), String> {
    let sanitized = sanitize_directory(payload)?;
    let path = cache_path(app)?;
    let parent = path
        .parent()
        .ok_or_else(|| "desktop room cache has no parent directory".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("cannot create {}: {error}", parent.display()))?;
    fs::write(&path, sanitized).map_err(|error| format!("cannot write {}: {error}", path.display()))
}

fn selected_server_rooms(payload: &str, server: &Url) -> Result<Vec<Value>, String> {
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err("native room cache is too large".to_owned());
    }
    let parsed: Value = serde_json::from_str(payload)
        .map_err(|error| format!("native room cache is invalid JSON: {error}"))?;
    let rooms = parsed
        .as_array()
        .ok_or_else(|| "native room cache must be an array".to_owned())?;
    let selected_origin = server.origin().ascii_serialization();
    let selected_is_local = server.host_str().is_some_and(|host| {
        matches!(
            host.trim_matches(['[', ']']),
            "localhost" | "127.0.0.1" | "::1"
        )
    });
    let selected_rooms = rooms
        .iter()
        .filter_map(|room| {
            let mut room = room.clone();
            let Some(source) = room.as_object_mut() else {
                return None;
            };
            if source.get("roomOrigin").and_then(Value::as_str) == Some("remote_server")
                && server_origin(source.get("serverOrigin")).as_deref()
                    != Some(selected_origin.as_str())
            {
                return None;
            }
            if selected_is_local {
                source.insert("roomOrigin".to_owned(), Value::String("local".to_owned()));
                source.remove("serverOrigin");
            } else {
                source.insert(
                    "roomOrigin".to_owned(),
                    Value::String("remote_server".to_owned()),
                );
                source.insert(
                    "serverOrigin".to_owned(),
                    Value::String(selected_origin.clone()),
                );
            }
            Some(room)
        })
        .collect::<Vec<_>>();
    Ok(selected_rooms)
}

fn merged_selected_server_directory(
    existing_payload: &str,
    payload: &str,
    server: &Url,
) -> Result<String, String> {
    let existing: Value = serde_json::from_str(&sanitize_directory(existing_payload)?)
        .map_err(|error| format!("native room cache is invalid JSON: {error}"))?;
    let selected_origin = server.origin().ascii_serialization();
    let selected_is_local = server.host_str().is_some_and(|host| {
        matches!(
            host.trim_matches(['[', ']']),
            "localhost" | "127.0.0.1" | "::1"
        )
    });
    let mut combined = existing
        .as_array()
        .into_iter()
        .flatten()
        .filter(|room| {
            if selected_is_local {
                return room.get("roomOrigin").and_then(Value::as_str) != Some("local");
            }
            room.get("roomOrigin").and_then(Value::as_str) != Some("remote_server")
                || server_origin(room.get("serverOrigin")).as_deref()
                    != Some(selected_origin.as_str())
        })
        .cloned()
        .collect::<Vec<_>>();
    combined.extend(selected_server_rooms(payload, server)?);
    sanitize_directory(
        &serde_json::to_string(&combined)
            .map_err(|error| format!("cannot encode native room cache: {error}"))?,
    )
}

pub fn store_selected_server_rooms(
    app: &AppHandle,
    payload: &str,
    server: &Url,
) -> Result<(), String> {
    let existing = load(app)?;
    store(
        app,
        &merged_selected_server_directory(&existing, payload, server)?,
    )
}

#[cfg(desktop)]
fn local_room_directory(server: &Url) -> Result<Value, String> {
    if server.scheme() != "http" {
        return Err("local room cache refresh requires an HTTP loopback server".to_owned());
    }
    let host = server
        .host_str()
        .ok_or_else(|| "local room server has no host".to_owned())?;
    let port = server
        .port_or_known_default()
        .ok_or_else(|| "local room server has no port".to_owned())?;
    let address = (host, port)
        .to_socket_addrs()
        .map_err(|error| format!("cannot resolve local room server: {error}"))?
        .next()
        .ok_or_else(|| "local room server has no reachable address".to_owned())?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
        .map_err(|error| format!("cannot connect to local room server: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| format!("cannot configure local room cache request: {error}"))?;
    let request = format!(
        "GET /api/rooms?include_archived=true HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("cannot request local room directory: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("cannot read local room directory: {error}"))?;
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "local room directory response is incomplete".to_owned())?;
    if headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        != Some("200")
    {
        return Err("local room directory request failed".to_owned());
    }
    serde_json::from_str(body)
        .map_err(|error| format!("local room directory response is invalid: {error}"))
}

#[cfg(desktop)]
pub fn refresh_local_rooms(app: &AppHandle, server: &Url) -> Result<(), String> {
    let payload = local_room_directory(server)?;
    let local_rooms = payload
        .get("rooms")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|room| {
            let room_id = bounded_text(room.get("room_id"), 128);
            let status = bounded_text(room.get("status"), 16).to_lowercase();
            if room_id.is_empty()
                || room.get("archived").and_then(Value::as_bool) == Some(true)
                || matches!(status.as_str(), "closed" | "archived")
            {
                return None;
            }
            let settings = room.get("room_settings");
            let label = bounded_text(settings.and_then(|value| value.get("label")), 80);
            let fallback_label = bounded_text(room.get("label"), 80);
            let label = if !label.is_empty() {
                label
            } else if !fallback_label.is_empty() {
                fallback_label
            } else {
                room_id.clone()
            };
            Some(json!({
                "id": format!("server-{room_id}"),
                "label": label,
                "meetingId": room_id,
                "roomOrigin": "local",
                "topic": bounded_text(settings.and_then(|value| value.get("topic")), 160),
                "shortLabel": bounded_text(settings.and_then(|value| value.get("short_label")), 4),
                "createdAt": bounded_text(room.get("last_active_at"), 64),
                "tone": "resident",
            }))
        })
        .collect::<Vec<_>>();
    let remote_rooms = serde_json::from_str::<Value>(&load(app)?)
        .ok()
        .and_then(|value| value.as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter(|room| room.get("roomOrigin").and_then(Value::as_str) == Some("remote_server"));
    let combined = local_rooms
        .into_iter()
        .chain(remote_rooms)
        .collect::<Vec<_>>();
    store(
        app,
        &serde_json::to_string(&combined)
            .map_err(|error| format!("cannot encode refreshed room cache: {error}"))?,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_keeps_public_room_metadata_and_drops_unknown_fields() {
        let payload = r#"[{
          "id":"guest-room-1",
          "label":"Remote room",
          "meetingId":"room-1",
          "roomOrigin":"remote_server",
          "serverOrigin":"https://rooms.example.test/path?secret=1",
          "topic":"Cached topic",
          "shortLabel":"R",
          "createdAt":"2026-08-02T00:00:00Z",
          "tone":"resident",
          "sessionToken":"must-not-persist"
        }]"#;

        let sanitized: Value = serde_json::from_str(&sanitize_directory(payload).unwrap()).unwrap();
        let room = sanitized[0].as_object().unwrap();
        assert_eq!(
            room.get("serverOrigin").unwrap(),
            "https://rooms.example.test"
        );
        assert!(!room.contains_key("sessionToken"));
    }

    #[test]
    fn selected_remote_server_cache_can_reopen_rooms_without_persisting_credentials() {
        let server = Url::parse("https://rooms.example.test/").unwrap();
        let payload = r#"[{
          "id":"server-room-1",
          "label":"Shared room",
          "meetingId":"room-1",
          "roomOrigin":"local",
          "sessionToken":"must-not-persist"
        }]"#;

        let cached: Value = serde_json::from_str(
            &merged_selected_server_directory("[]", payload, &server).unwrap(),
        )
        .unwrap();
        let room = cached[0].as_object().unwrap();
        assert_eq!(room.get("roomOrigin").unwrap(), "remote_server");
        assert_eq!(
            room.get("serverOrigin").unwrap(),
            "https://rooms.example.test"
        );
        assert!(!room.contains_key("sessionToken"));
    }

    #[test]
    fn selected_server_refresh_preserves_rooms_owned_by_other_origins() {
        let existing = r#"[
          {"id":"local","meetingId":"local-room","roomOrigin":"local"},
          {"id":"other","meetingId":"other-room","roomOrigin":"remote_server","serverOrigin":"https://other.example.test"},
          {"id":"old-selected","meetingId":"old-room","roomOrigin":"remote_server","serverOrigin":"https://rooms.example.test"}
        ]"#;
        let selected = r#"[
          {"id":"new-selected","meetingId":"new-room","roomOrigin":"local"},
          {"id":"injected-other","meetingId":"injected-room","roomOrigin":"remote_server","serverOrigin":"https://other.example.test"}
        ]"#;
        let server = Url::parse("https://rooms.example.test/").unwrap();

        let merged: Value = serde_json::from_str(
            &merged_selected_server_directory(existing, selected, &server).unwrap(),
        )
        .unwrap();
        let ids = merged
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|room| room.get("id").and_then(Value::as_str))
            .collect::<Vec<_>>();

        assert_eq!(ids, vec!["local", "other", "new-selected"]);
    }
}
