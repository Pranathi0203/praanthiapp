CREATE TABLE IF NOT EXISTS auth_events (
  id BIGSERIAL PRIMARY KEY,
  user_email VARCHAR(320) NOT NULL,
  organization VARCHAR(50) NOT NULL,
  actor_type VARCHAR(20) NOT NULL DEFAULT 'employee',
  event_type VARCHAR(20) NOT NULL,
  event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
