PRAGMA foreign_keys = ON;

CREATE TABLE persons (
    person_id TEXT PRIMARY KEY,
    identity_kind TEXT NOT NULL CHECK (identity_kind IN ('guest', 'google')),
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE external_identities (
    identity_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    issuer TEXT NOT NULL,
    subject_hmac TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (issuer, subject_hmac)
);
CREATE INDEX idx_external_identities_person ON external_identities(person_id);

CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    public_key_jwk TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX idx_devices_person ON devices(person_id, revoked_at);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX idx_sessions_person ON sessions(person_id, revoked_at);
CREATE INDEX idx_sessions_device ON sessions(device_id, revoked_at);

CREATE TABLE recovery_credentials (
    credential_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL UNIQUE REFERENCES persons(person_id) ON DELETE CASCADE,
    verifier TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    rotated_at INTEGER,
    revoked_at INTEGER
);

CREATE TABLE google_handoffs (
    handoff_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    device_public_key_jwk TEXT NOT NULL,
    device_label TEXT NOT NULL DEFAULT '',
    browser_token_hash TEXT NOT NULL,
    poll_token_hash TEXT NOT NULL,
    google_nonce TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'consumed')),
    person_id TEXT REFERENCES persons(person_id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    consumed_at INTEGER
);
CREATE INDEX idx_google_handoffs_expiry ON google_handoffs(expires_at, status);

CREATE TABLE servers (
    server_id TEXT PRIMARY KEY,
    owner_person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    host_public_key_jwk TEXT NOT NULL,
    host_key_fingerprint TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX idx_servers_owner ON servers(owner_person_id, revoked_at);

CREATE TABLE server_endpoints (
    server_id TEXT PRIMARY KEY REFERENCES servers(server_id) ON DELETE CASCADE,
    origin TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK (state IN ('online', 'offline')),
    generation INTEGER NOT NULL,
    lease_expires_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE person_servers (
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN ('owner', 'bookmark')),
    alias TEXT NOT NULL DEFAULT '',
    first_seen_at INTEGER NOT NULL,
    last_connected_at INTEGER,
    PRIMARY KEY (person_id, server_id)
);
CREATE INDEX idx_person_servers_server ON person_servers(server_id);

CREATE TABLE request_nonces (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    nonce TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, nonce)
);
CREATE INDEX idx_request_nonces_expiry ON request_nonces(expires_at);

CREATE TABLE host_request_nonces (
    server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    nonce TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (server_id, nonce)
);
CREATE INDEX idx_host_request_nonces_expiry ON host_request_nonces(expires_at);

CREATE TABLE rate_limits (
    bucket TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (bucket, window_start)
);
CREATE INDEX idx_rate_limits_window ON rate_limits(window_start);
