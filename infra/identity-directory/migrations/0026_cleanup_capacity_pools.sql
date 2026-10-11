-- Stage 2 owner decision: raise pools before the first real slot measurement.
-- 0025 retains the 9990 aggregate ceiling until slot activation, then 24000.
-- Update limits only: preserve spent units, UTC days and cleanup controls.
UPDATE creation_budgets SET daily_limit = CASE purpose
    WHEN 'AUTH' THEN 2050
    WHEN 'ANONYMOUS' THEN 2900
    WHEN 'GENERAL' THEN 11250
    WHEN 'ENDPOINT' THEN 950
    WHEN 'OWNER_GRANT' THEN 4050
    WHEN 'OWNER_REDEEM' THEN 1550
    ELSE daily_limit END;
UPDATE member_sync_budget SET admission_limit = 1250 WHERE id = 1;
-- Retain 0018 actor caps: GENERAL 240/account, 150/session, 120/host;
-- ENDPOINT 16/server and signing key; AUTH source/edge/active-grant caps unchanged.
-- member_sync daily_limit stays 1800; writers enforce MIN with admission_limit.
