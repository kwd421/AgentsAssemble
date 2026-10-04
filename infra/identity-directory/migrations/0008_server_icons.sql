ALTER TABLE servers ADD COLUMN icon TEXT NOT NULL DEFAULT '';

CREATE TABLE server_icons (
    server_id TEXT PRIMARY KEY REFERENCES servers(server_id) ON DELETE CASCADE,
    icon TEXT NOT NULL,
    png BLOB NOT NULL CHECK (length(png) <= 1100000)
);
