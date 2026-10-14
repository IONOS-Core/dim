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
-- DROP and ADD stay in one statement, otherwise the table is briefly without
-- the index DIM expects by name.
--
-- ALGORITHM=INPLACE, LOCK=NONE is an assertion, not a request: if the server
-- cannot do the change in place and without locking, the statement aborts with
-- ER_ALTER_OPERATION_NOT_SUPPORTED (1846) instead of silently falling back to a
-- copying, locking ALTER. Measured on MariaDB 10.6: ~0.6 s per million rows,
-- concurrent INSERTs keep working.
--
-- This only relaxes the constraint, so no existing row can violate it and no
-- data cleanup is needed. Databases where the index was already widened by hand
-- (ITOUDP-7795) are fine: the index is simply rebuilt with the same columns.

ALTER TABLE `ipblock`
      DROP INDEX `address`,
      ADD UNIQUE KEY `address` (`address`,`prefix`,`layer3domain_id`,`version`),
      ALGORITHM=INPLACE, LOCK=NONE;

UPDATE schemainfo SET version=14;
COMMIT;
