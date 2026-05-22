#!/usr/bin/env bash
# =============================================================================
# verification/run_verification.sh
# =============================================================================
# POLARIS Engine — Phase 2 Formal Verification Runner
#
# Invokes TLC model checker on both TLA+ specifications:
#   1. oms_state_machine.tla   — 9-state OMS lifecycle
#   2. memory_state_machine.tla — 6-state RAG memory lifecycle
#
# Exit codes:
#   0 — All properties hold (no invariant violations, no liveness errors)
#   1 — One or more TLC violations detected
#   2 — TLC toolchain not available (prompts installer)
#   3 — Unexpected error
#
# Usage:
#   ./verification/run_verification.sh            # full run
#   SKIP_LIVENESS=1 ./verification/run_verification.sh   # safety only
#
# TLC installation (if not present):
#   The script auto-downloads tla2tools.jar from the official GitHub
#   release if it is not found.  Requires curl and Java ≥ 11.
# =============================================================================

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLA_OMS="${SCRIPT_DIR}/oms_state_machine.tla"
TLA_MEM="${SCRIPT_DIR}/memory_state_machine.tla"
CFG_OMS="${SCRIPT_DIR}/oms_state_machine.cfg"
CFG_MEM="${SCRIPT_DIR}/memory_state_machine.cfg"

# TLC jar — prefer system-wide, fall back to local download
TLC_JAR="${TLC_JAR:-${SCRIPT_DIR}/tla2tools.jar}"
TLC_DOWNLOAD_URL="https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar"

# ── Colour helpers ────────────────────────────────────────────────────────
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

info()    { echo "${CYAN}[INFO]${RESET}  $*"; }
success() { echo "${GREEN}[PASS]${RESET}  $*"; }
warn()    { echo "${YELLOW}[WARN]${RESET}  $*"; }
fail()    { echo "${RED}[FAIL]${RESET}  $*" >&2; }

# ── Toolchain check ───────────────────────────────────────────────────────
check_java() {
    if ! command -v java &>/dev/null; then
        fail "Java not found. Install OpenJDK ≥ 11:"
        fail "  sudo apt install openjdk-21-jre-headless   (Debian/Ubuntu)"
        fail "  sudo dnf install java-21-openjdk-headless  (Fedora/RHEL)"
        return 1
    fi
    local ver
    ver=$(java -version 2>&1 | head -1)
    info "Java: ${ver}"
    return 0
}

fetch_tlc() {
    if [[ -f "${TLC_JAR}" ]]; then
        info "TLC jar found: ${TLC_JAR}"
        return 0
    fi
    warn "tla2tools.jar not found — downloading from GitHub releases…"
    if ! command -v curl &>/dev/null; then
        fail "curl is required to download tla2tools.jar"
        return 1
    fi
    curl -fsSL -o "${TLC_JAR}" "${TLC_DOWNLOAD_URL}" || {
        fail "Download failed. Manually place tla2tools.jar at: ${TLC_JAR}"
        return 1
    }
    success "Downloaded tla2tools.jar ($(du -sh "${TLC_JAR}" | cut -f1))"
}

# ── Config file generator ──────────────────────────────────────────────────
write_oms_cfg() {
    cat > "${CFG_OMS}" <<'CFG'
\* TLC model configuration for oms_state_machine.tla
INIT     Init
NEXT     Next
CONSTANTS
    MaxSteps = 12
INVARIANTS
    TypeOK
    TerminalSink
PROPERTIES
    EventualTermination
CFG
    info "Wrote ${CFG_OMS}"
}

write_mem_cfg() {
    cat > "${CFG_MEM}" <<'CFG'
\* TLC model configuration for memory_state_machine.tla
INIT     MemInit
NEXT     MemNext
CONSTANTS
    MaxSteps = 20
INVARIANTS
    MemTypeOK
    ArchivedIsTerminal
    CycleOnlyVerified
PROPERTIES
    EventualArchival
CFG
    info "Wrote ${CFG_MEM}"
}

# ── TLC runner ────────────────────────────────────────────────────────────
run_tlc() {
    local spec_name="$1"
    local tla_file="$2"
    local cfg_file="$3"
    local workers="${TLC_WORKERS:-4}"

    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Checking: ${BOLD}${spec_name}${RESET}"
    info "Spec    : ${tla_file}"
    info "Config  : ${cfg_file}"
    info "Workers : ${workers}"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local output
    local exit_code=0

    output=$(java \
        -XX:+UseParallelGC \
        -Xmx2g \
        -jar "${TLC_JAR}" \
        -config "${cfg_file}" \
        -workers "${workers}" \
        -deadlock \
        "${tla_file}" 2>&1) || exit_code=$?

    echo "${output}"

    if echo "${output}" | grep -qE "Error:|Invariant.*violated|Temporal property|No error"; then
        if echo "${output}" | grep -qE "Error:|Invariant.*violated|Temporal property"; then
            fail "${spec_name}: VIOLATIONS DETECTED"
            return 1
        fi
    fi

    if [[ ${exit_code} -ne 0 ]]; then
        fail "${spec_name}: TLC exited with code ${exit_code}"
        return 1
    fi

    success "${spec_name}: All properties hold ✓"
    return 0
}

# ── Python test vector generation ────────────────────────────────────────
run_python_vectors() {
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Running Python state-machine test vectors"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local repo_root="${SCRIPT_DIR}/.."
    local python_bin="${VIRTUAL_ENV:-}/bin/python"
    [[ -x "${python_bin}" ]] || python_bin="$(command -v python3)"

    "${python_bin}" -m pytest \
        "${repo_root}/tests/verification/test_state_machines.py" \
        -v \
        --tb=short \
        2>&1 || {
            fail "Python state-machine tests FAILED"
            return 1
        }

    success "Python test vectors: all passed ✓"
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
    local overall_exit=0

    echo ""
    echo "${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
    echo "${BOLD}║  POLARIS SENTINEL — Phase 2 Formal Verification          ║${RESET}"
    echo "${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
    echo ""

    # 1. Toolchain checks
    if ! check_java; then
        exit 2
    fi

    if ! fetch_tlc; then
        exit 2
    fi

    # 2. Generate config files
    write_oms_cfg
    write_mem_cfg

    # 3. Run OMS model checker
    if ! run_tlc "OMS State Machine (9 states)" "${TLA_OMS}" "${CFG_OMS}"; then
        overall_exit=1
    fi

    echo ""

    # 4. Run memory lifecycle model checker
    if ! run_tlc "Memory Lifecycle (6 states)" "${TLA_MEM}" "${CFG_MEM}"; then
        overall_exit=1
    fi

    echo ""

    # 5. Run Python test vectors (always, even if TLC passes)
    if ! run_python_vectors; then
        overall_exit=1
    fi

    echo ""
    if [[ ${overall_exit} -eq 0 ]]; then
        echo "${GREEN}${BOLD}══ ALL PHASE-2 VERIFICATION GATES PASSED ══${RESET}"
    else
        echo "${RED}${BOLD}══ PHASE-2 VERIFICATION FAILED — SEE ABOVE ══${RESET}"
    fi

    exit "${overall_exit}"
}

main "$@"
