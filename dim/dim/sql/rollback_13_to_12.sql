-- Add Pseudo user check
ALTER TABLE `user` DROP COLUMN `is_pseudo`;

UPDATE schemainfo SET version=12;
COMMIT;