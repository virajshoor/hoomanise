UPDATE api_keys SET revoked_at = now()
WHERE revoked_at IS NULL AND id NOT IN (
    SELECT DISTINCT ON (user_id) id
    FROM api_keys
    WHERE revoked_at IS NULL
    ORDER BY user_id, created_at DESC
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_api_key_per_user
    ON api_keys(user_id) WHERE revoked_at IS NULL;

ALTER TABLE webhook_endpoints DROP COLUMN IF EXISTS secret_obf;
