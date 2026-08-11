-- Add `version` to the ipblock uniqueness key.
--
-- 0.0.0.0/0 and ::/0 both have address 0 and prefix 0, so the old key
-- (address, prefix, layer3domain_id) could not hold both in one layer3domain:
-- creating the second one failed with a duplicate key error.
--
-- `version` is appended rather than inserted so that the existing
-- (address, prefix, layer3domain_id) index prefix stays usable, and the index
-- keeps its name -- Ipblock._set_parent() references it via USE INDEX (address).
--
-- This only relaxes the constraint, so no existing row can violate it and no
-- data cleanup is needed.

ALTER TABLE `ipblock`
      DROP INDEX `address`,
      ADD UNIQUE KEY `address` (`address`,`prefix`,`layer3domain_id`,`version`);

UPDATE schemainfo SET version=13;
COMMIT;
