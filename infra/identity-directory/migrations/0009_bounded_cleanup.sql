-- Additive only: 25fad46a and 0.1.x hosts keep the same schema semantics.
CREATE INDEX idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX idx_sessions_revoked ON sessions(revoked_at) WHERE revoked_at IS NOT NULL;
