-- Drop `version` from the ipblock uniqueness key.
--
-- This tightens the constraint, so it fails if any two blocks share
-- (address, prefix, layer3domain_id) and differ only in `version` -- the
-- 0.0.0.0/0 plus ::/0 pair being the case migrate_12_to_13.sql allowed.
-- Remove one of each such pair before rolling back:
--
--   SELECT address, prefix, layer3domain_id, COUNT(*) FROM ipblock
--    GROUP BY address, prefix, layer3domain_id HAVING COUNT(*) > 1;

ALTER TABLE `ipblock`
      DROP INDEX `address`,
      ADD UNIQUE KEY `address` (`address`,`prefix`,`layer3domain_id`);

UPDATE schemainfo SET version=12;
COMMIT;
