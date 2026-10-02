ALTER TABLE servers ADD COLUMN host_os TEXT
  CHECK (host_os IS NULL OR host_os IN ('macos', 'windows', 'linux', 'other'));
