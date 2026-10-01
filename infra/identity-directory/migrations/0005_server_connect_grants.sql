CREATE TABLE server_connect_grants (
    grant_id TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    endpoint_origin TEXT NOT NULL,
    endpoint_generation INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_used_at INTEGER
);
CREATE INDEX idx_server_connect_grants_expiry
ON server_connect_grants(expires_at);
CREATE INDEX idx_server_connect_grants_session
ON server_connect_grants(session_id, expires_at);
