#!/usr/bin/env python3
"""
verify-dnssec-zone.py - Validate DNSSEC status, RFC 6781 backdating, and cryptographic integrity.

Usage:
  python3 verify-dnssec-zone.py <zone> [@nameserver]
"""

import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime


def run_cmd(cmd_list):
    try:
        res = subprocess.run(cmd_list, capture_output=True, text=True, check=True)
        return res.stdout
    except subprocess.CalledProcessError as e:
        print(
            f"Error running command {' '.join(cmd_list)}: {e.stderr}", file=sys.stderr
        )
        return ""


def parse_iso_time(s):
    return datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def verify_cryptography(zone, server):
    """Verifies cryptographic signatures via AXFR dump using ldns-verify-zone or VerifyZone."""
    dig_axfr_cmd = ["dig", f"@{server}", zone, "axfr"]
    axfr_output = run_cmd(dig_axfr_cmd)
    if not axfr_output.strip() or "Transfer failed." in axfr_output:
        print(
            f" [FAIL] Could not perform AXFR for {zone} from @{server} to verify cryptography."
        )
        sys.exit(3)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".zone", delete=False) as f:
        f.write(axfr_output)
        temp_path = f.name

    try:
        # 1. Try ldns-verify-zone if available
        ldns_bin = "/usr/bin/ldns-verify-zone"
        if os.path.exists(ldns_bin):
            res = subprocess.run(
                [ldns_bin, temp_path], check=False, capture_output=True, text=True
            )
            combined = res.stdout + "\n" + res.stderr
            if (
                "Zone is verified and complete" in combined
                and "Error:" not in combined
                and "There were errors" not in combined
            ):
                print(" [PASS] CRYPTOGRAPHIC INTEGRITY VERIFIED (ldns-verify-zone):")
                print(
                    f"        All signatures in {zone} mathematically verify against zone keys!"
                )
                return True
            else:
                print(
                    f" [FAIL] ldns-verify-zone rejected signatures in {zone}:\n{combined}"
                )
                sys.exit(3)

        # 2. Try com.verisignlabs.dnssec.cl.VerifyZone via Java
        jar_path = "/opt/dim/pdns-output.jar"
        if os.path.exists(jar_path):
            java_cmd = [
                "java",
                "-cp",
                jar_path,
                "com.verisignlabs.dnssec.cl.VerifyZone",
                "--ignore-duplicate-rrs",
                temp_path,
            ]
            res = subprocess.run(java_cmd, check=False, capture_output=True, text=True)
            combined = res.stdout + "\n" + res.stderr
            if (
                "zone verified." in combined
                and "zone did not verify." not in combined
                and "WARNING: Signature failed" not in combined
            ):
                print(" [PASS] CRYPTOGRAPHIC INTEGRITY VERIFIED (VerifyZone):")
                print(
                    f"        All signatures in {zone} mathematically verify against zone keys!"
                )
                return True
            else:
                print(f" [FAIL] VerifyZone rejected signatures in {zone}:\n{combined}")
                sys.exit(3)

        print(
            " [FAIL] Neither ldns-verify-zone nor pdns-output.jar VerifyZone found for cryptographic verification!"
        )
        sys.exit(3)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(1)

    zone = args[0].rstrip(".")
    server = "127.0.0.1"

    if len(args) > 1:
        if args[1].startswith("@"):
            server = args[1][1:]
        elif re.match(r"^\d+\.\d+\.\d+\.\d+$", args[1]):
            server = args[1]

    print("\n=======================================================")
    print(f" DNSSEC Cryptographic & Inception Verification for {zone}")
    print(f" Target Server: @{server}")
    print("=======================================================\n")

    # 1. Query SOA + RRSIG
    dig_soa_cmd = [
        "dig",
        f"@{server}",
        f"{zone}.",
        "SOA",
        "+dnssec",
        "+noall",
        "+answer",
    ]
    soa_output = run_cmd(dig_soa_cmd)
    if not soa_output.strip():
        print(f"[FAIL] No SOA answer returned from @{server} for {zone}")
        sys.exit(2)

    # 2. Query DNSKEY + RRSIG
    dig_key_cmd = [
        "dig",
        f"@{server}",
        f"{zone}.",
        "DNSKEY",
        "+dnssec",
        "+noall",
        "+answer",
    ]
    key_output = run_cmd(dig_key_cmd)
    if not key_output.strip():
        print(f"[FAIL] No DNSKEY answer returned from @{server} for {zone}")
        sys.exit(2)

    print("--- 1. Authoritative Records Retrieved ---")
    soa_lines = [l.strip() for l in soa_output.strip().splitlines() if l.strip()]
    for l in soa_lines:
        print(" ", l)

    # Parse SOA serial and TTL
    soa_match = re.search(r"(\S+)\s+(\d+)\s+IN\s+SOA\s+\S+\s+\S+\s+(\d+)", soa_output)
    if not soa_match:
        print(f"[FAIL] Could not parse SOA record for {zone}.")
        sys.exit(2)
    soa_ttl = int(soa_match.group(2))
    soa_serial = int(soa_match.group(3))

    # Parse RRSIG SOA timestamps
    rrsig_match = re.search(
        r"IN\s+RRSIG\s+SOA\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d{14})\s+(\d{14})\s+(\d+)",
        soa_output,
    )
    if not rrsig_match:
        print(
            f"\n[INFO] Zone '{zone}' is an UNSIGNED zone (no RRSIG covering SOA found)."
        )
        sys.exit(0)

    exp_str, inc_str = rrsig_match.group(4), rrsig_match.group(5)
    exp_dt = parse_iso_time(exp_str)
    inc_dt = parse_iso_time(inc_str)
    now_utc = datetime.now(UTC)

    print("\n--- 2. RFC 6781 Inception & Validity Analysis ---")
    print(f" Current UTC:     {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f" Signature Inc:   {inc_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({inc_str})")
    print(f" Signature Exp:   {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({exp_str})")
    print(f" SOA Serial:      {soa_serial}")
    print(f" SOA TTL:         {soa_ttl}s")

    is_in_past = inc_dt <= now_utc
    is_exp_future = exp_dt > now_utc

    if is_in_past and is_exp_future:
        print(
            " [PASS] Plausibility Gate: Inception is in the past, Expiration is in the future."
        )
    else:
        print(
            f" [FAIL] Plausibility Gate: Timestamps implausible! (Inception in past: {is_in_past}, Exp in future: {is_exp_future})"
        )
        sys.exit(2)

    # RFC 6781 Backdating: Inception must be backdated relative to now_utc (typically ~3600 seconds)
    skew_seconds = (now_utc - inc_dt).total_seconds()
    if skew_seconds >= 1800:
        print(
            f" [PASS] RFC 6781 Backdating: Inception is backdated by {int(skew_seconds)}s (~1 hour) relative to current UTC time."
        )
    else:
        print(
            f" [WARN] Inception backdating: backdated by only {int(skew_seconds)}s (expected >= 1800s)."
        )

    # 3. Cryptographic Validation
    print("\n--- 3. Cryptographic Validation (RSA / DNSSEC Engine) ---")
    verify_cryptography(zone, server)

    print("\n=======================================================")
    print(f" RESULT: Zone {zone} is FULLY OPERATIONAL & VALIDATED! ")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
