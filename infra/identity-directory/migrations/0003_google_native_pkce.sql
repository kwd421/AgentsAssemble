ALTER TABLE google_handoffs ADD COLUMN flow_kind TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE google_handoffs ADD COLUMN code_challenge TEXT;
ALTER TABLE google_handoffs ADD COLUMN redirect_uri TEXT;
ALTER TABLE google_handoffs ADD COLUMN redirect_state TEXT;
ALTER TABLE google_handoffs ADD COLUMN authorization_code_hash TEXT;
