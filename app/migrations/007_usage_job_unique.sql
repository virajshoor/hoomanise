DROP INDEX IF EXISTS uniq_usage_job;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_usage_job ON usage_records(job_id);
