-- System settings table for storing application-wide configuration
-- Run this migration to create the system_settings table

CREATE TABLE IF NOT EXISTS system_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(100) UNIQUE NOT NULL,
    value JSONB NOT NULL DEFAULT '{}',
    description TEXT,
    category VARCHAR(50) NOT NULL DEFAULT 'general',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create index for faster lookups by key
CREATE INDEX IF NOT EXISTS idx_system_settings_key ON system_settings(key);
CREATE INDEX IF NOT EXISTS idx_system_settings_category ON system_settings(category);

-- Insert default settings
INSERT INTO system_settings (key, value, description, category) VALUES
    ('debug_mode', 'false', 'Enable verbose logging for debugging purposes (development only)', 'system'),
    ('maintenance_mode', 'false', 'Temporarily disable access for non-admin users', 'system'),
    ('log_level', '"INFO"', 'Application log level: DEBUG, INFO, WARNING, ERROR, CRITICAL', 'system'),
    ('incident_sla_hours', '4', 'Default SLA hours for incident resolution', 'sla'),
    ('task_sla_hours', '24', 'Default SLA hours for task completion', 'sla'),
    ('critical_threshold_percent', '75', 'SLA threshold percentage for critical status', 'sla'),
    ('at_risk_threshold_percent', '50', 'SLA threshold percentage for at-risk status', 'sla'),
    ('enable_location_tracking', 'true', 'Enable GPS location tracking for technicians', 'location'),
    ('location_stale_threshold_hours', '24', 'Hours before a location is considered stale', 'location'),
    ('geofence_default_radius_meters', '100', 'Default geofence radius in meters', 'location'),
    ('enable_email_notifications', 'true', 'Enable email notifications', 'notifications'),
    ('enable_sms_notifications', 'false', 'Enable SMS notifications', 'notifications'),
    ('alert_on_sla_breach', 'true', 'Send alerts when SLA is breached', 'notifications'),
    ('alert_on_critical', 'true', 'Send alerts for critical incidents', 'notifications'),
    ('enable_request_logging', 'false', 'Log all API request/response bodies (debug mode)', 'debug'),
    ('enable_sql_logging', 'false', 'Log SQL queries with timing (debug mode)', 'debug'),
    ('enable_performance_headers', 'false', 'Add X-Response-Time header to responses', 'debug')
ON CONFLICT (key) DO NOTHING;

-- Function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_system_settings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update updated_at
DROP TRIGGER IF EXISTS trigger_update_system_settings_updated_at ON system_settings;
CREATE TRIGGER trigger_update_system_settings_updated_at
    BEFORE UPDATE ON system_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_system_settings_updated_at();

-- Grant permissions
GRANT SELECT, INSERT, UPDATE ON system_settings TO authenticated;
