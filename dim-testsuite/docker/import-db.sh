#!/usr/bin/env bash
#
# dim-testsuite/docker/import-db.sh
# Generic database dump import utility for local DIM testbed.
# Imports SQL dumps (plain, .gz, .bz2, .xz, .zst) from local files or HTTP/HTTPS URLs
# into testbed MariaDB (dim, pdns1, pdns2, and isolated local output databases).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/dim-testsuite/compose.yaml"

# Colors for terminal output
RED="$(printf '\033[0;31m')"
GREEN="$(printf '\033[0;32m')"
YELLOW="$(printf '\033[1;33m')"
BLUE="$(printf '\033[0;34m')"
BOLD="$(printf '\033[1m')"
NC="$(printf '\033[0m')"

DIM_DUMP="${DIM_DUMP:-}"
PDNS1_DUMP="${PDNS1_DUMP:-}"
PDNS2_DUMP="${PDNS2_DUMP:-}"
DUMPS_DIR="${DUMPS_DIR:-}"
PRIMARY_OUTPUT="${PRIMARY_OUTPUT:-}"
SECONDARY_OUTPUT="${SECONDARY_OUTPUT:-}"
SANITIZE_OUTPUTS=1
KEEP_OUTPUTUPDATE=0

TEMP_FILES=()

cleanup_temp() {
    if [[ ${#TEMP_FILES[@]} -gt 0 ]]; then
        rm -f "${TEMP_FILES[@]}" 2>/dev/null || true
    fi
}
trap cleanup_temp EXIT INT TERM

usage() {
    cat <<EOF
${BOLD}Usage:${NC} $(basename "$0") [OPTIONS]

Generic utility to import database dumps into the local DIM testbed MariaDB container.
Supports local files and HTTP/HTTPS URLs with automatic compression detection
(plain .sql, .gz, .bz2, .xz, .zst).

${BOLD}Options:${NC}
  --dim <path-or-url>       Path or URL to DIM database dump
  --pdns1 <path-or-url>     Path or URL to PowerDNS 1 (auth) database dump
  --pdns2 <path-or-url>     Path or URL to PowerDNS 2 database dump
  --dir <dir>               Directory containing dump files to auto-detect (*dim*, *pdns1*, *pdns2*)
  --primary-output <name>   Output name to map to pdns1 (default: first output or matching public/external)
  --secondary-output <name> Output name to map to pdns2 (default: second output or matching internal)
  --keep-outputupdate       Preserve existing rows in outputupdate table (default: truncate backlog)
  --no-sanitize-outputs     Do not rewrite output db_uri endpoints to local testbed databases
  -h, --help                Show this help message

${BOLD}Environment Variables:${NC}
  DIM_DUMP, PDNS1_DUMP, PDNS2_DUMP, DUMPS_DIR, PRIMARY_OUTPUT, SECONDARY_OUTPUT

${BOLD}Examples:${NC}
  $(basename "$0") --dim ~/dumps/dim-dump.sql.gz --pdns1 ~/dumps/pdns-dump.sql.gz
  $(basename "$0") --dim https://example.com/dumps/dim-sample.sql.gz
  $(basename "$0") --dir ~/dumps/
EOF
    exit 0
}

# Parse command line options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dim)
            DIM_DUMP="$2"
            shift 2
            ;;
        --pdns1)
            PDNS1_DUMP="$2"
            shift 2
            ;;
        --pdns2)
            PDNS2_DUMP="$2"
            shift 2
            ;;
        --dir)
            DUMPS_DIR="$2"
            shift 2
            ;;
        --primary-output)
            PRIMARY_OUTPUT="$2"
            shift 2
            ;;
        --secondary-output)
            SECONDARY_OUTPUT="$2"
            shift 2
            ;;
        --keep-outputupdate)
            KEEP_OUTPUTUPDATE=1
            shift
            ;;
        --no-sanitize-outputs)
            SANITIZE_OUTPUTS=0
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}[ERROR] Unknown option:${NC} $1" >&2
            usage
            ;;
    esac
done

# Auto-detect dumps if a directory was provided
if [[ -n "${DUMPS_DIR}" ]]; then
    if [[ ! -d "${DUMPS_DIR}" ]]; then
        echo -e "${RED}[ERROR] Dumps directory not found:${NC} ${DUMPS_DIR}" >&2
        exit 1
    fi

    echo -e "${BLUE}==> Scanning directory for database dumps:${NC} ${DUMPS_DIR}"
    shopt -s nullglob

    # Detect DIM dump
    if [[ -z "${DIM_DUMP}" ]]; then
        dim_candidates=("${DUMPS_DIR}"/*dim*.sql*)
        if [[ ${#dim_candidates[@]} -gt 0 ]]; then
            DIM_DUMP="${dim_candidates[0]}"
            echo -e "    Found DIM dump:     ${GREEN}${DIM_DUMP}${NC}"
        fi
    fi

    # Detect PDNS1 dump
    if [[ -z "${PDNS1_DUMP}" ]]; then
        pdns1_candidates=("${DUMPS_DIR}"/*pdns1*.sql*)
        if [[ ${#pdns1_candidates[@]} -gt 0 ]]; then
            PDNS1_DUMP="${pdns1_candidates[0]}"
            echo -e "    Found PDNS1 dump:   ${GREEN}${PDNS1_DUMP}${NC}"
        else
            # Fall back to any pdns dump
            pdns_generic=("${DUMPS_DIR}"/*pdns*.sql*)
            if [[ ${#pdns_generic[@]} -gt 0 ]]; then
                PDNS1_DUMP="${pdns_generic[0]}"
                echo -e "    Found PDNS dump:    ${GREEN}${PDNS1_DUMP}${NC} (mapped to pdns1)"
            fi
        fi
    fi

    # Detect PDNS2 dump
    if [[ -z "${PDNS2_DUMP}" ]]; then
        pdns2_candidates=("${DUMPS_DIR}"/*pdns2*.sql*)
        if [[ ${#pdns2_candidates[@]} -gt 0 ]]; then
            PDNS2_DUMP="${pdns2_candidates[0]}"
            echo -e "    Found PDNS2 dump:   ${GREEN}${PDNS2_DUMP}${NC}"
        fi
    fi
    shopt -u nullglob
fi

if [[ -z "${DIM_DUMP}" && -z "${PDNS1_DUMP}" && -z "${PDNS2_DUMP}" ]]; then
    echo -e "${YELLOW}[WARN] No dump files or URLs specified or detected.${NC}"
    echo "       Please specify --dim, --pdns1, --pdns2, or --dir."
    usage
fi

# Ensure testbed MariaDB container is running
echo -e "${BLUE}==> Checking local testbed status...${NC}"
if ! docker compose -f "${COMPOSE_FILE}" ps --status running -q mariadb >/dev/null 2>&1 || \
   [[ -z "$(docker compose -f "${COMPOSE_FILE}" ps --status running -q mariadb 2>/dev/null)" ]]; then
    echo -e "${RED}[ERROR] Testbed MariaDB container is not running!${NC}"
    echo "        Please run 'make testbed-start' first before importing dumps." >&2
    exit 1
fi

resolve_dump_source() {
    local label="$1"
    local source="$2"

    if [[ "$source" =~ ^https?:// ]]; then
        local tmp_file
        tmp_file="$(mktemp --tmpdir "dim-dump-${label}-XXXXXX")"
        TEMP_FILES+=("${tmp_file}")
        echo -e "${BLUE}--> Downloading ${BOLD}${label}${NC} from ${source}...${NC}"
        if ! curl -fL --progress-bar "$source" -o "$tmp_file"; then
            echo -e "${RED}[ERROR] Failed to download ${label} dump from:${NC} ${source}" >&2
            exit 1
        fi
        echo "$tmp_file"
    else
        if [[ ! -f "$source" ]]; then
            echo -e "${RED}[ERROR] File does not exist:${NC} ${source}" >&2
            exit 1
        fi
        echo "$source"
    fi
}

detect_and_decompress() {
    local file="$1"
    local mime_type
    mime_type="$(file -b --mime-type "${file}" 2>/dev/null || true)"

    case "${mime_type}" in
        application/gzip|application/x-gzip)
            gzip -dc "${file}"
            ;;
        application/x-bzip2|application/bzip2)
            bzip2 -dc "${file}"
            ;;
        application/x-xz|application/xz)
            xz -dc "${file}"
            ;;
        application/zstd|application/x-zstd)
            zstd -dc "${file}"
            ;;
        *)
            # Fallback check on magic bytes if mime type is generic octet-stream
            local magic
            magic="$(head -c 4 "${file}" 2>/dev/null | od -An -tx1 | tr -d ' \n' || true)"
            case "${magic}" in
                1f8b*)
                    gzip -dc "${file}"
                    ;;
                425a*)
                    bzip2 -dc "${file}"
                    ;;
                fd377a58*)
                    xz -dc "${file}"
                    ;;
                28b52ffd*)
                    zstd -dc "${file}"
                    ;;
                *)
                    cat "${file}"
                    ;;
            esac
            ;;
    esac
}

ensure_pdns_schema_compatibility() {
    local target_db="$1"
    echo -e "    Ensuring schema compatibility for ${BOLD}${target_db}${NC} (options, catalog, rev_name)..."
    docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot "${target_db}" <<'EOF'
ALTER TABLE domains
    ADD COLUMN IF NOT EXISTS options TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS catalog VARCHAR(255) DEFAULT NULL;
CREATE INDEX IF NOT EXISTS catalog_idx ON domains(catalog);

ALTER TABLE records ADD COLUMN IF NOT EXISTS rev_name VARCHAR(255) DEFAULT NULL;
CREATE INDEX IF NOT EXISTS rev_name_domid ON records (domain_id, rev_name);
UPDATE records SET rev_name = REVERSE(name) WHERE rev_name IS NULL;
EOF
}

import_single_db() {
    local target_db="$1"
    local dump_source="$2"

    local dump_file
    dump_file="$(resolve_dump_source "${target_db}" "${dump_source}")"

    local file_size
    file_size="$(du -h "${dump_file}" | awk '{print $1}')"
    echo -e "${BLUE}--> Importing ${BOLD}${target_db}${NC} from ${dump_source} (${file_size})...${NC}"

    # Recreate target database with proper permissions
    docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot <<EOF
CREATE DATABASE IF NOT EXISTS \`${target_db}\`;
GRANT ALL ON \`${target_db}\`.* TO 'dim'@'%';
GRANT ALL ON \`${target_db}\`.* TO 'pdns'@'%';
FLUSH PRIVILEGES;
EOF

    # Decompress and stream SQL dump directly into MariaDB container
    detect_and_decompress "${dump_file}" | docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot "${target_db}"

    # Ensure grants are preserved even if the dump contained 'DROP DATABASE'
    docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot <<EOF
GRANT ALL ON \`${target_db}\`.* TO 'dim'@'%';
GRANT ALL ON \`${target_db}\`.* TO 'pdns'@'%';
FLUSH PRIVILEGES;
EOF

    # If PowerDNS database was imported, ensure required schema columns exist
    if [[ "${target_db}" == "pdns1" || "${target_db}" == "pdns2" ]]; then
        ensure_pdns_schema_compatibility "${target_db}"
    fi

    echo -e "    ${GREEN}[OK] Successfully imported ${target_db}!${NC}"
}

sanitize_outputs() {
    echo -e "${BLUE}==> Sanitizing outputs for local testbed isolation...${NC}"

    # 1. Ensure testbed super-admin user 'admin' exists and has Admin role
    echo -e "    Ensuring testbed super-admin 'admin' exists in DIM..."
    docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot dim <<'EOF'
INSERT INTO user (username, user_type_id)
VALUES ('admin', (SELECT id FROM user_type WHERE name = 'Admin'))
ON DUPLICATE KEY UPDATE user_type_id = (SELECT id FROM user_type WHERE name = 'Admin');
EOF

    # 2. Ensure testbed output 'out' exists and points to local pdns1
    docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot dim <<'EOF'
INSERT INTO output (name, plugin, db_uri, last_run)
VALUES ('out', 'pdns-db', 'mysql://pdns:pdns@127.0.0.1:3307/pdns1', NOW())
ON DUPLICATE KEY UPDATE db_uri = 'mysql://pdns:pdns@127.0.0.1:3307/pdns1';

INSERT IGNORE INTO zonegroup (name, comment) VALUES ('zg', 'Testbed zone group');
INSERT IGNORE INTO outputzonegroup (output_id, zonegroup_id)
SELECT (SELECT id FROM output WHERE name = 'out'), (SELECT id FROM zonegroup WHERE name = 'zg');
EOF

    # 3. Query all existing pdns-db outputs from DIM database
    local outputs_raw
    outputs_raw="$(docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot -N -B -e \
        "SELECT id, name FROM output WHERE plugin = 'pdns-db';" dim 2>/dev/null || true)"

    if [[ -z "${outputs_raw}" ]]; then
        echo -e "    No existing pdns-db outputs found in DIM."
        return 0
    fi

    # Read outputs excluding testbed 'out'
    local -a ids=()
    local -a names=()
    while IFS=$'\t' read -r out_id out_name; do
        [[ -z "${out_id}" || -z "${out_name}" ]] && continue
        [[ "${out_name}" == "out" ]] && continue
        ids+=("${out_id}")
        names+=("${out_name}")
    done <<< "${outputs_raw}"

    local count=${#ids[@]}
    if [[ ${count} -eq 0 ]]; then
        echo -e "    No third-party pdns-db outputs to remap."
        return 0
    fi

    local -a targets=()
    for ((i=0; i<count; i++)); do
        targets+=("")
    done

    local primary_done=0
    local secondary_done=0

    # Pass 1: Explicit mapping via --primary-output and --secondary-output
    for ((i=0; i<count; i++)); do
        local name="${names[i]}"
        if [[ -n "${PRIMARY_OUTPUT}" && "${name}" == "${PRIMARY_OUTPUT}" && ${primary_done} -eq 0 ]]; then
            targets[i]="pdns1"
            primary_done=1
        elif [[ -n "${SECONDARY_OUTPUT}" && "${name}" == "${SECONDARY_OUTPUT}" && ${secondary_done} -eq 0 ]]; then
            targets[i]="pdns2"
            secondary_done=1
        fi
    done

    # Pass 2: Heuristic regex matching (public/external/global -> pdns1, internal/private -> pdns2)
    for ((i=0; i<count; i++)); do
        [[ -n "${targets[i]}" ]] && continue
        local name="${names[i]}"
        if [[ -z "${PRIMARY_OUTPUT}" && ${primary_done} -eq 0 && "${name}" =~ (public|external|global) ]]; then
            targets[i]="pdns1"
            primary_done=1
        elif [[ -z "${SECONDARY_OUTPUT}" && ${secondary_done} -eq 0 && "${name}" =~ (internal|private) ]]; then
            targets[i]="pdns2"
            secondary_done=1
        fi
    done

    # Pass 3: Fallback unassigned outputs: first to pdns1, second to pdns2, remaining to isolated db
    for ((i=0; i<count; i++)); do
        [[ -n "${targets[i]}" ]] && continue
        local name="${names[i]}"
        if [[ ${primary_done} -eq 0 ]]; then
            targets[i]="pdns1"
            primary_done=1
        elif [[ ${secondary_done} -eq 0 ]]; then
            targets[i]="pdns2"
            secondary_done=1
        else
            local safe_name
            safe_name="$(echo "${name}" | tr -c 'a-zA-Z0-9_' '_' | tr -s '_' | sed 's/^_//;s/_$//')"
            targets[i]="pdns_${safe_name}"
        fi
    done

    # Apply remapping and database initialization for each output
    for ((i=0; i<count; i++)); do
        local out_id="${ids[i]}"
        local out_name="${names[i]}"
        local target_db="${targets[i]}"

        local local_uri="mysql://pdns:pdns@127.0.0.1:3307/${target_db}"
        echo -e "    Mapping output ${BOLD}${out_name}${NC} -> ${GREEN}${target_db}${NC} (${local_uri})"

        # Update db_uri in DIM database
        docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot dim \
            -e "UPDATE output SET db_uri = '${local_uri}', status = 'OK' WHERE id = ${out_id};"

        # Ensure target database exists in MariaDB with pdns schema
        docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot <<EOF
CREATE DATABASE IF NOT EXISTS \`${target_db}\`;
GRANT ALL ON \`${target_db}\`.* TO 'dim'@'%';
GRANT ALL ON \`${target_db}\`.* TO 'pdns'@'%';
FLUSH PRIVILEGES;
EOF

        # If domains table doesn't exist, load basic PowerDNS schema
        local has_domains
        has_domains="$(docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot -N -B \
            -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '${target_db}' AND table_name = 'domains';" 2>/dev/null || true)"
        if [[ "${has_domains}" != "1" ]]; then
            docker compose -f "${COMPOSE_FILE}" exec -T mariadb sh -c "mysql -uroot '${target_db}' < /pdns.sql" 2>/dev/null || true
        fi

        # Ensure PowerDNS schema compatibility (options, catalog, rev_name)
        ensure_pdns_schema_compatibility "${target_db}"
    done

    # 4. Truncate stale outputupdate backlog unless explicitly requested otherwise
    if [[ "${KEEP_OUTPUTUPDATE}" != "1" ]]; then
        echo -e "    Clearing stale outputupdate queue backlog..."
        docker compose -f "${COMPOSE_FILE}" exec -T mariadb mysql -uroot dim -e "TRUNCATE TABLE outputupdate;" 2>/dev/null || true
    fi

    echo -e "    ${GREEN}[OK] All outputs sanitized and isolated locally!${NC}"
}

# Stop middleware and output daemon before importing to prevent locks and writes
echo -e "${BLUE}==> Stopping dim-mw and dim-output during import...${NC}"
docker compose -f "${COMPOSE_FILE}" stop dim-mw dim-output >/dev/null 2>&1 || true

# Import PDNS databases first if specified
if [[ -n "${PDNS1_DUMP}" ]]; then
    import_single_db "pdns1" "${PDNS1_DUMP}"
fi

if [[ -n "${PDNS2_DUMP}" ]]; then
    import_single_db "pdns2" "${PDNS2_DUMP}"
fi

# Import DIM database dump
if [[ -n "${DIM_DUMP}" ]]; then
    import_single_db "dim" "${DIM_DUMP}"

    if [[ ${SANITIZE_OUTPUTS} -eq 1 ]]; then
        sanitize_outputs
    fi
fi

# Restart middleware and output daemon to refresh connections and caches
echo -e "${BLUE}==> Starting testbed middleware and pdns-output to refresh state...${NC}"
docker compose -f "${COMPOSE_FILE}" start dim-mw dim-output >/dev/null 2>&1 || \
docker compose -f "${COMPOSE_FILE}" restart dim-mw dim-output >/dev/null 2>&1

echo ""
echo -e "${GREEN}${BOLD}[OK] Database bootstrap completed successfully!${NC}"
echo "     • Enter testbed: 'make testbed-shell'"
echo "     • Follow logs:   'make testbed-logs'"
echo "     • Query stats:   'make testbed-status'"
