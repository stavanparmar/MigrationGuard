ALTER TABLE accounts DROP COLUMN legacy_status;

CREATE INDEX accounts_status_idx ON accounts (status);

UPDATE accounts
SET status = 'inactive';
