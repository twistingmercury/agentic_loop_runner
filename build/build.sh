#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJ_ROOT}/dist}"

BUILD_VER="${BUILD_VER:-$(git -C "${PROJ_ROOT}" describe --tags --abbrev=0 2>/dev/null || echo '0.0.0.dev0')}"

run_quiet() {
    local log
    local rc=0
    log="$(mktemp 2>/dev/null || mktemp -t 'gralph-build')"
    "$@" >"${log}" 2>&1 || rc=$?
    if [ "${rc}" -ne 0 ]; then
        cat "${log}"
    fi

    rm -f "${log}"
    return "${rc}"
}

COMPOSE_FILE_PATH="${PROJ_ROOT}/tests/docker-compose.yaml"

# Export writes into dist/ without deleting, so an old build would otherwise
# sit beside the new one and could be mistaken for it.
clean_output() {
    mkdir -p "${OUTPUT_DIR}"
    find "${OUTPUT_DIR}" -maxdepth 1 -type f \( -name 'alr-*.whl' -o -name 'alr-*.tar.gz' \) -delete
    return 0
}

build() {
    clean_output

    printf "building alr..."
    run_quiet docker build --rm --no-cache --pull --progress=plain \
        --file "${SCRIPT_DIR}/Dockerfile" \
        --build-arg BUILD_VER="${BUILD_VER}" \
        --target export \
        --output "type=local,dest=${OUTPUT_DIR}" \
        --tag "alr:${BUILD_VER}" \
        "${PROJ_ROOT}"

    printf "\nBinaries exported to: %s\n" "${OUTPUT_DIR}"
}

# Prints the file name of the newest wheel in the output directory.
newest_wheel() {
    local candidate
    local newest=""

    for candidate in "${OUTPUT_DIR}"/alr-*.whl; do
        if [ ! -f "${candidate}" ]; then
            continue
        fi

        if [ -z "${newest}" ] || [ "${candidate}" -nt "${newest}" ]; then
            newest="${candidate}"
        fi
    done

    printf "%s" "${newest##*/}"
    return 0
}

e2e_tests() {
    local wheel
    local rc=0

    wheel="$(newest_wheel)"
    if [ -z "${wheel}" ]; then
        printf "ERROR: no alr wheel in %s; run make build first\n" "${OUTPUT_DIR}" >&2
        return 1
    fi

    # The Dockerfile copies only this file, so no other wheel can be installed.
    export ALR_WHEEL="${wheel}"

    printf "starting end-to-end tests with %s..." "${wheel}"
    run_quiet docker compose --progress=plain -f "${COMPOSE_FILE_PATH}" build
    docker compose -f "${COMPOSE_FILE_PATH}" up --remove-orphans --exit-code-from tests || rc=$?
    docker compose -f "${COMPOSE_FILE_PATH}" down --remove-orphans --rmi local >/dev/null 2>&1 || true

    if [ "${rc}" -ne 0 ]; then
        printf "failed\n"
        return "${rc}"
    fi

    printf "done\n"
    return 0
}

main() {
    local mode="${1:-all}"

    if [ "${mode}" = "e2e" ]; then
        e2e_tests
        return $?
    fi

    if [ "${mode}" != "all" ]; then
        printf "ERROR: unknown mode %s; use no argument or e2e\n" "${mode}" >&2
        return 1
    fi

    build
    e2e_tests
}

main "$@"
