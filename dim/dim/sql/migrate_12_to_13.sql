-- Add Pseudo user check
ALTER TABLE `user` ADD COLUMN `is_pseudo` tinyint(1) DEFAULT NULL;

UPDATE schemainfo SET version=13;
COMMIT;
