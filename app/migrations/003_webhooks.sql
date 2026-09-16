CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    secret_obf TEXT NOT NULL,
    events TEXT[] NOT NULL DEFAULT ARRAY['job.completed', 'job.failed'],
    disabled_at TIMESTAMPTZ,
    last_delivery_at TIMESTAMPTZ,
    last_status INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_user_active ON webhook_endpoints(user_id) WHERE disabled_at IS NULL;
