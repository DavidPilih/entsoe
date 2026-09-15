-- Apply before storing fractional charging values if auto is currently an integer column.
BEGIN;
ALTER TABLE device_energy_schedule
    ALTER COLUMN auto TYPE double precision USING auto::double precision;
COMMIT;
