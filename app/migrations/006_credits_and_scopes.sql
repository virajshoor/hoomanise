CREATE UNIQUE INDEX IF NOT EXISTS uniq_usage_job ON usage_records(job_id) WHERE job_id IS NOT NULL;

ALTER TABLE api_keys ALTER COLUMN scopes SET DEFAULT ARRAY['humanize', 'jobs:read'];
UPDATE api_keys SET scopes = array_append(scopes, 'jobs:read')
WHERE NOT ('jobs:read' = ANY(scopes));
