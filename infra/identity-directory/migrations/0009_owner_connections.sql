-- Entry grants stay short; active owner connections retain their central provenance.
CREATE TABLE server_owner_connections (
    connection_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    endpoint_origin TEXT NOT NULL,
    endpoint_generation INTEGER NOT NULL,
    browser_fingerprint TEXT NOT NULL,
    lease_expires_at INTEGER NOT NULL
);
CREATE INDEX idx_owner_connections_session ON server_owner_connections(session_id, lease_expires_at);
CREATE INDEX idx_owner_connections_expiry ON server_owner_connections(lease_expires_at);
