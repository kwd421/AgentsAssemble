-- Durable publication floor belongs to the exact restore receipt. Old receipts
-- have no recoverable historical floor and must never invent one from live state.
ALTER TABLE server_restore_operations ADD COLUMN endpoint_generation INTEGER
    CHECK(endpoint_generation > 0 AND endpoint_generation <= 9007199254740991);
