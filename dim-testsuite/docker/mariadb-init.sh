#!/bin/bash
#
# Creates the databases and users expected by the test suite and loads the pdns
# schema into both pdns databases.
#
set -e

mysql -uroot <<'EOF'
CREATE DATABASE IF NOT EXISTS dim;
CREATE DATABASE IF NOT EXISTS pdns1;
CREATE DATABASE IF NOT EXISTS pdns2;

-- the dim server and runtest.py (which recreates the dim database)
CREATE USER IF NOT EXISTS 'dim'@'%' IDENTIFIED BY 'dim';
GRANT ALL ON dim.* TO 'dim'@'%';
GRANT ALL ON pdns1.* TO 'dim'@'%';
GRANT ALL ON pdns2.* TO 'dim'@'%';

-- used by the pdns servers and by the db-uris in t/
CREATE USER IF NOT EXISTS 'pdns'@'%' IDENTIFIED BY 'pdns';
GRANT ALL ON pdns1.* TO 'pdns'@'%';
GRANT ALL ON pdns2.* TO 'pdns'@'%';

-- used by the PDNS1_DB_* / PDNS2_DB_* defaults of the Makefile
CREATE USER IF NOT EXISTS 'pdns1'@'%' IDENTIFIED BY 'pdns';
GRANT ALL ON pdns1.* TO 'pdns1'@'%';
CREATE USER IF NOT EXISTS 'pdns2'@'%' IDENTIFIED BY 'pdns';
GRANT ALL ON pdns2.* TO 'pdns2'@'%';
EOF

mysql -uroot pdns1 < /pdns.sql
mysql -uroot pdns2 < /pdns.sql

# testenv/pdns.sql is older than the pdns version used here: pdns >= 4.7 selects
# domains.options and domains.catalog in getDomainInfo(), which AXFR needs
for db in pdns1 pdns2; do
    mysql -uroot "$db" <<'EOF'
ALTER TABLE domains
    ADD COLUMN IF NOT EXISTS options TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS catalog VARCHAR(255) DEFAULT NULL;
CREATE INDEX IF NOT EXISTS catalog_idx ON domains(catalog);
EOF
done
