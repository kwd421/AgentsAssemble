use std::{
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    time::Duration,
};

use url::Url;

pub const DEFAULT_LOCAL_SERVER_URL: &str = "http://127.0.0.1:8765/";

pub fn normalized_server_url(raw: &str) -> Result<Url, String> {
    let mut url = Url::parse(raw.trim()).map_err(|_| "server URL is invalid".to_owned())?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("server URL must use http or https".to_owned());
    }
    if url.host_str().is_none() {
        return Err("server URL must include a host".to_owned());
    }
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

pub fn same_origin(candidate: &Url, server: &Url) -> bool {
    candidate.scheme() == server.scheme()
        && candidate.host_str() == server.host_str()
        && candidate.port_or_known_default() == server.port_or_known_default()
}

pub fn is_local_app_url(candidate: &Url) -> bool {
    matches!(candidate.scheme(), "tauri" | "asset")
        || candidate.host_str() == Some("tauri.localhost")
}

pub fn tcp_endpoint_is_reachable(server: &Url) -> bool {
    let Some(host) = server.host_str() else {
        return false;
    };
    let Some(port) = server.port_or_known_default() else {
        return false;
    };
    let Ok(addresses) = (host, port).to_socket_addrs() else {
        return false;
    };
    addresses
        .take(4)
        .any(|address| TcpStream::connect_timeout(&address, Duration::from_millis(350)).is_ok())
}

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
            "https://user:secret@rooms.example.test",
            "https://rooms.example.test/another-app",
            "https://rooms.example.test/?token=secret",
            "https://rooms.example.test/#room",
        ] {
            assert!(normalized_server_url(raw).is_err(), "accepted {raw}");
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
}
