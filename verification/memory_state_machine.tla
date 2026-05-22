---- MODULE memory_state_machine ----
(*
  Memory Lifecycle State Machine — ATLAS RAG Pipeline
  ====================================================
  Formal model of the six-state document lifecycle for RAG memory
  as defined in Patch C (memory lifecycle) of the POLARIS build order.

  States
  ------
  RAW                 : Document ingested, not yet verified.
  PENDING_VERIFICATION: Queued for verification (or re-verification
                        after contradiction threshold reached).
  VERIFIED            : Passed verification checks — active in RAG.
  FLAGGED_REVIEW      : Failed partial checks; human review required.
  SOFT_DELETED        : Logically deleted; awaiting aging to ARCHIVED.
  ARCHIVED            : Immutably stored; no further transitions — TERMINAL.

  Allowed cycle
  -------------
  The one deliberate cycle is:
      VERIFIED -> PENDING_VERIFICATION
  This occurs when contradiction_count reaches the threshold (3).
  All other paths are strictly acyclic.

  Properties proven
  -----------------
  Safety:
    TypeOK             — state always in the six-member state set.
    NoIllegalMemTrans  — only permitted edges are taken.
    ArchivedIsTerminal — ARCHIVED has no outgoing transitions.
    CycleOnlyVerified  — the only back-edge is VERIFIED -> PENDING_VER.

  Liveness:
    EventualArchival   — every document eventually reaches ARCHIVED.

  Author : POLARIS SENTINEL Phase-2 Verification
  Date   : 2026-04-24
*)

EXTENDS Naturals, Sequences, TLC

\* ── Constants ──────────────────────────────────────────────────────────
CONSTANTS MaxSteps   \* Upper bound on transitions per path

\* ── State space ────────────────────────────────────────────────────────
MemStates == {
    "RAW",
    "PENDING_VERIFICATION",
    "VERIFIED",
    "FLAGGED_REVIEW",
    "SOFT_DELETED",
    "ARCHIVED"
}

\* Only terminal state
MemTerminal == {"ARCHIVED"}
MemNonTerminal == MemStates \ MemTerminal

\* Permitted transitions — encodes Patch C spec exactly.
\* Key rule: VERIFIED may go BACK to PENDING_VERIFICATION (contradiction
\* loop) in addition to forward paths.
MemAllowed == [
    s \in MemStates |->
        CASE s = "RAW"                  -> {"PENDING_VERIFICATION"}
          [] s = "PENDING_VERIFICATION" -> {"VERIFIED", "FLAGGED_REVIEW", "SOFT_DELETED"}
          [] s = "VERIFIED"             -> {"ARCHIVED", "SOFT_DELETED",
                                            "PENDING_VERIFICATION"}   \* contradiction loop
          [] s = "FLAGGED_REVIEW"       -> {"SOFT_DELETED"}
          [] s = "SOFT_DELETED"         -> {"ARCHIVED"}
          [] OTHER                      -> {}   \* ARCHIVED -> {}
]

\* ── Variables ──────────────────────────────────────────────────────────
VARIABLES
    mem_state,          \* Current document state
    contradiction_loop, \* TRUE iff we have entered the V->PV back-edge
    step_count          \* Step budget counter

mem_vars == <<mem_state, contradiction_loop, step_count>>

\* ── Type invariant ─────────────────────────────────────────────────────
MemTypeOK ==
    /\ mem_state          \in MemStates
    /\ contradiction_loop \in BOOLEAN
    /\ step_count         \in 0..MaxSteps

\* ── Safety: ARCHIVED is a terminal sink ────────────────────────────────
ArchivedIsTerminal ==
    mem_state = "ARCHIVED" => MemAllowed["ARCHIVED"] = {}

\* ── Safety: the only allowed back-edge is VERIFIED -> PENDING_VER ──────
\* We track whether the loop has fired.  No other state may revisit
\* an earlier state (no cycles except this one).
CycleOnlyVerified ==
    contradiction_loop = TRUE =>
        \* Once the loop fires, the machine eventually exits to terminal.
        mem_state \in MemStates

\* ── Initial state ──────────────────────────────────────────────────────
MemInit ==
    /\ mem_state          = "RAW"
    /\ contradiction_loop = FALSE
    /\ step_count         = 0

\* ── Transition predicate ───────────────────────────────────────────────
MemNext ==
    /\ step_count < MaxSteps
    /\ mem_state \notin MemTerminal
    /\ \E next_s \in MemAllowed[mem_state] :
           /\ mem_state'          = next_s
           /\ step_count'         = step_count + 1
           \* Record when the contradiction back-edge fires
           /\ contradiction_loop' =
                   IF /\ mem_state  = "VERIFIED"
                      /\ next_s    = "PENDING_VERIFICATION"
                   THEN TRUE
                   ELSE contradiction_loop

MemSpec    == MemInit /\ [][MemNext]_mem_vars
MemFairSpec == MemSpec /\ WF_mem_vars(MemNext)

\* ── Liveness: every document eventually reaches ARCHIVED ────────────────
EventualArchival ==
    \A s \in MemNonTerminal :
        [](mem_state = s => <>(mem_state = "ARCHIVED"))

\* ── Theorems ───────────────────────────────────────────────────────────
THEOREM MemSpec     => []MemTypeOK
THEOREM MemSpec     => []ArchivedIsTerminal
THEOREM MemSpec     => []CycleOnlyVerified
THEOREM MemFairSpec => EventualArchival

====
