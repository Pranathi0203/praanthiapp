CREATE TABLE IF NOT EXISTS attendance_events (
  event_id UUID PRIMARY KEY,
  user_email VARCHAR(320) NOT NULL,
  organization VARCHAR(50) NOT NULL,
  event_type VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'processed',
  source VARCHAR(50) NOT NULL DEFAULT 'iot-pipeline',
  requested_at TIMESTAMPTZ NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
