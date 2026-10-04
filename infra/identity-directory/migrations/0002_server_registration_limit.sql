CREATE TRIGGER servers_per_person_limit
BEFORE INSERT ON servers
WHEN (
    SELECT COUNT(*) FROM servers
    WHERE owner_person_id = NEW.owner_person_id
) >= 20
BEGIN
    SELECT RAISE(ABORT, 'server_limit_reached');
END;
