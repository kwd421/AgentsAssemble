-- Additive: old hosts retain their registration protocol.
ALTER TABLE servers ADD COLUMN name_revision INTEGER NOT NULL DEFAULT 0 CHECK (name_revision >= 0);
