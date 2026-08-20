#[cfg(desktop)]
use std::{
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    time::Duration,
};

use url::Url;

const DEFAULT_CENTRAL_DIRECTORY_ORIGIN: &str =
    "https://agentsassemble-identity-directory.seinel.workers.dev";

pub fn central_directory_origin() -> &'static str {
    option_env!("AGENTSASSEMBLE_CENTRAL_URL")
        .unwrap_or(DEFAULT_CENTRAL_DIRECTORY_ORIGIN)
}

fn plaintext_http_allowed(url: &Url) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    if host.eq_ignore_ascii_case("localhost") {
        return true;
    }
    host.trim_matches(['[', ']'])
        .parse::<std::net::IpAddr>()
        .is_ok_and(|address| address.is_loopback())
}

fn require_secure_remote_transport(url: &Url, label: &str) -> Result<(), String> {
    if url.scheme() == "https" || (url.scheme() == "http" && plaintext_http_allowed(url)) {
        return Ok(());
    }
    Err(format!(
        "{label} must use https unless it targets literal loopback or localhost"
    ))
}

pub fn normalized_server_url(raw: &str) -> Result<Url, String> {
    let mut url = Url::parse(raw.trim()).map_err(|_| "server URL is invalid".to_owned())?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("server URL must use http or https".to_owned());
    }
    if url.host_str().is_none() {
        return Err("server URL must include a host".to_owned());
    }
    require_secure_remote_transport(&url, "server URL")?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err("server URL must not contain credentials".to_owned());
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err("server URL must not contain a query or fragment".to_owned());
    }
    if !matches!(url.path(), "" | "/") {
        return Err("server URL must identify an origin, not a path".to_owned());
    }
    url.set_path("/");
    Ok(url)
}

pub fn normalized_navigation_url(raw: &str) -> Result<(Url, Url), String> {
    let destination = Url::parse(raw.trim()).map_err(|_| "room link is invalid".to_owned())?;
    if !matches!(destination.scheme(), "http" | "https") {
        return Err("room link must use http or https".to_owned());
    }
    if destination.host_str().is_none() {
        return Err("room link must include a host".to_owned());
    }
    require_secure_remote_transport(&destination, "room link")?;
    if !destination.username().is_empty() || destination.password().is_some() {
        return Err("room link must not contain embedded credentials".to_owned());
    }
    let mut server = destination.clone();
    server.set_path("/");
    server.set_query(None);
    server.set_fragment(None);
    Ok((destination, server))
}

pub fn same_origin(candidate: &Url, server: &Url) -> bool {
    candidate.scheme() == server.scheme()
        && candidate.host_str() == server.host_str()
        && candidate.port_or_known_default() == server.port_or_known_default()
}

fn base64url_token(value: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn google_desktop_client_id(value: &str) -> bool {
    let Some(prefix) = value.strip_suffix(".apps.googleusercontent.com") else {
        return false;
    };
    (8..=160).contains(&prefix.len())
        && prefix
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn native_google_callback(value: &str) -> bool {
    let Ok(callback) = Url::parse(value) else {
        return false;
    };
    callback.scheme() == "http"
        && matches!(callback.host_str(), Some("127.0.0.1" | "::1"))
        && callback.port().is_some()
        && callback.path() == "/api/central-login/callback"
        && callback.query().is_none()
        && callback.fragment().is_none()
        && callback.username().is_empty()
        && callback.password().is_none()
}

fn native_google_authorization_url(url: &Url) -> bool {
    if url.scheme() != "https"
        || url.host_str() != Some("accounts.google.com")
        || url.port().is_some()
        || url.path() != "/o/oauth2/v2/auth"
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return false;
    }
    let values = url.query_pairs().collect::<Vec<_>>();
    let one = |key: &str| {
        let matches = values
            .iter()
            .filter(|(candidate, _)| candidate == key)
            .collect::<Vec<_>>();
        (matches.len() == 1).then(|| matches[0].1.as_ref())
    };
    values.len() == 9
        && one("client_id").is_some_and(google_desktop_client_id)
        && one("redirect_uri").is_some_and(native_google_callback)
        && one("response_type") == Some("code")
        && one("scope") == Some("openid")
        && one("state").is_some_and(|value| base64url_token(value, 32, 128))
        && one("nonce").is_some_and(|value| base64url_token(value, 16, 128))
        && one("code_challenge").is_some_and(|value| base64url_token(value, 43, 43))
        && one("code_challenge_method") == Some("S256")
        && one("prompt") == Some("select_account")
}

pub fn central_google_handoff_url(raw: &str) -> Result<Url, String> {
    let url =
        Url::parse(raw.trim()).map_err(|_| "central Google login URL is invalid".to_owned())?;
    if native_google_authorization_url(&url) {
        return Ok(url);
    }
    Err("central Google login URL must be Google's scoped desktop OAuth endpoint".to_owned())
}

pub fn is_local_app_url(candidate: &Url) -> bool {
    matches!(candidate.scheme(), "tauri" | "asset")
        || candidate.host_str() == Some("tauri.localhost")
}

#[cfg(desktop)]
pub fn agentsassemble_server_is_ready(server: &Url) -> bool {
    if server.scheme() != "http" {
        return false;
    }
    let Some(host) = server.host_str() else {
        return false;
    };
    let Some(port) = server.port_or_known_default() else {
        return false;
    };
    let Ok(addresses) = (host, port).to_socket_addrs() else {
        return false;
    };

    for address in addresses.take(4) {
        let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350))
        else {
            continue;
        };
        let _ = stream.set_read_timeout(Some(Duration::from_millis(600)));
        let request = format!(
            "GET /api/runtime/version HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        );
        if stream.write_all(request.as_bytes()).is_err() {
            continue;
        }
        let mut response = String::new();
        let _ = stream.read_to_string(&mut response);
        if response
            .lines()
            .next()
            .is_some_and(|status| status.split_whitespace().nth(1) == Some("200"))
            && response.contains("\"protocol_version\"")
            && response.contains("\"frontend_version\"")
        {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_location_accepts_only_an_http_origin_without_embedded_secrets() {
        assert_eq!(
            normalized_server_url("https://rooms.example.test:9443")
                .unwrap()
                .as_str(),
            "https://rooms.example.test:9443/"
        );

        for raw in [
            "file:///tmp/room.html",
            "http://192.168.1.20:8765",
            "http://rooms.example.test",
            "https://user:secret@rooms.example.test",
            "https://rooms.example.test/another-app",
            "https://rooms.example.test/?token=secret",
            "https://rooms.example.test/#room",
        ] {
            assert!(normalized_server_url(raw).is_err(), "accepted {raw}");
        }

        for raw in [
            "http://localhost:8765",
            "http://127.0.0.1:8765",
            "http://[::1]:8765",
        ] {
            assert!(normalized_server_url(raw).is_ok(), "rejected {raw}");
        }
    }

    #[test]
    fn remote_navigation_stays_on_the_selected_server_origin() {
        let server = normalized_server_url("https://rooms.example.test:9443").unwrap();

        assert!(same_origin(
            &Url::parse("https://rooms.example.test:9443/join?token=abc").unwrap(),
            &server,
        ));
        assert!(!same_origin(
            &Url::parse("https://rooms.example.test.evil.test:9443/").unwrap(),
            &server,
        ));
        assert!(!same_origin(
            &Url::parse("http://rooms.example.test:9443/").unwrap(),
            &server,
        ));
    }

    #[test]
    fn room_link_keeps_one_time_join_or_recovery_material_only_in_the_destination() {
        let (destination, server) = normalized_navigation_url(
            "https://rooms.example.test/join?token=one-time#recovery=ABCD",
        )
        .unwrap();

        assert_eq!(
            destination.as_str(),
            "https://rooms.example.test/join?token=one-time#recovery=ABCD"
        );
        assert_eq!(server.as_str(), "https://rooms.example.test/");
    }

    #[test]
    fn central_google_login_opens_only_scoped_native_oauth_urls() {
        let native = concat!(
            "https://accounts.google.com/o/oauth2/v2/auth?",
            "client_id=desktop-client.apps.googleusercontent.com&",
            "redirect_uri=http%3A%2F%2F127.0.0.1%3A43123%2Fapi%2Fcentral-login%2Fcallback&",
            "response_type=code&scope=openid&",
            "state=abcdefghijklmnopqrstuvwxyz_1234567890ABCDEFG&",
            "nonce=abcdefghijklmnopqrstuvwxyz_1234567890&",
            "code_challenge=abcdefghijklmnopqrstuvwxyz_1234567890ABCDEF&",
            "code_challenge_method=S256&prompt=select_account"
        );
        assert_eq!(central_google_handoff_url(native).unwrap().as_str(), native);

        for raw in [
            "https://agentsassemble-identity-directory.seinel.workers.dev/auth/google#handoff=goh_abcdefghijklmnop&browser=abcdefghijklmnopqrstuvwxyz_1234567890ABCDEFG".to_owned(),
            native.replace("accounts.google.com", "accounts.google.com.evil.test"),
            native.replace(
                "http%3A%2F%2F127.0.0.1%3A43123%2Fapi%2Fcentral-login%2Fcallback",
                "https%3A%2F%2Fattacker.example%2Fcallback",
            ),
            native.replace("scope=openid", "scope=openid+email"),
        ] {
            assert!(central_google_handoff_url(&raw).is_err(), "accepted {raw}");
        }
    }
}
