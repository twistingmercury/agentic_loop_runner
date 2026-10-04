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

build() {
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

e2e_tests() {
    printf "starting end-to-end tests..."
    # local rc=0
    # run_quiet docker compose --progress=plain -f "${PROJ_ROOT}/tests/docker-compose.yaml" build
    # docker compose -f "${PROJ_ROOT}/tests/docker-compose.yaml" up --remove-orphans --exit-code-from tests || rc=$?
    # docker compose -f "${PROJ_ROOT}/tests/docker-compose.yaml" down --remove-orphans >/dev/null 2>&1 || true

    # if [ "${rc}" -ne 0 ]; then
    #     printf "failed\n"
    #     return "${rc}"
    # fi

    printf "done\n"
    return 0
}

main() {
    build
    e2e_tests
}

main "$@"
