#!/bin/bash
set -eu

export NDCLI_SERVER="http://localhost:5000"
export NDCLI_USERNAME="admin"

echo "==> Seeding DIM testbed with demo zone and sample records..."
ndcli login -p p >/dev/null 2>&1 || true

# Ensure output and zone-group exist
ndcli create output out plugin pdns-db db-uri "mysql://pdns:pdns@127.0.0.1:3307/pdns1" >/dev/null 2>&1 || true
ndcli create zone-group zg >/dev/null 2>&1 || true
ndcli modify output out add zone-group zg >/dev/null 2>&1 || true

# Provision demo zone test.example.com
echo "--> Creating zone test.example.com..."
ndcli create zone test.example.com >/dev/null 2>&1 || true
ndcli modify zone-group zg add zone test.example.com >/dev/null 2>&1 || true

echo "--> Adding sample records (www -> 1.2.3.4, mail -> 1.2.3.5)..."
ndcli modify zone test.example.com create rr www A 1.2.3.4 >/dev/null 2>&1 || true
ndcli modify zone test.example.com create rr mail A 1.2.3.5 >/dev/null 2>&1 || true

echo "[OK] Testbed seeded successfully!"
echo "     Verify with: dig www.test.example.com"
