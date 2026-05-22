"""
tests/verification/test_state_machines.py
==========================================
TLA+-derived test vectors for the PROMETHEUS OMS and ATLAS RAG memory
state machines.

The transition graphs here are the *authoritative Python encoding* of:
  - verification/oms_state_machine.tla   (9 states, Section 11.1)
  - verification/memory_state_machine.tla (6 states, Patch C)

Test strategy
-------------
Each test class contains:
  - Valid paths   : sequences the model checker proved reachable.
  - Invalid paths : sequences the model checker proved unreachable /
                    invariant-violating (illegal edges).
  - Boundary cases: self-loops (same→same), empty paths, etc.

The implementations under test:
  - atlas.rag.state_machine.DocumentStateMachine / DocumentState
  - (OMS) A lightweight OrderStateMachine built here from the allowed
    transition table — PROMETHEUS does not currently expose a pure
    state-machine class; this file also contains the reference impl.
"""

from __future__ import annotations

import pytest
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Reference OMS state machine (derived from TLA+ spec)
# ─────────────────────────────────────────────────────────────────────────────

class OrderState(str, Enum):
    """The nine states of the PROMETHEUS OMS lifecycle."""
    PENDING   = "PENDING"
    SUBMITTED = "SUBMITTED"
    OPEN      = "OPEN"
    PARTIAL   = "PARTIAL"
    FILLED    = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"
    EXPIRED   = "EXPIRED"
    FAILED    = "FAILED"


_OMS_TERMINAL: frozenset[OrderState] = frozenset({
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
    OrderState.FAILED,
})

_OMS_ALLOWED: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING:   frozenset({OrderState.SUBMITTED,
                                     OrderState.CANCELLED,
                                     OrderState.FAILED}),
    OrderState.SUBMITTED: frozenset({OrderState.OPEN,
                                     OrderState.REJECTED,
                                     OrderState.CANCELLED,
                                     OrderState.FAILED}),
    OrderState.OPEN:      frozenset({OrderState.PARTIAL,
                                     OrderState.FILLED,
                                     OrderState.CANCELLED,
                                     OrderState.EXPIRED,
                                     OrderState.FAILED}),
    OrderState.PARTIAL:   frozenset({OrderState.FILLED,
                                     OrderState.CANCELLED,
                                     OrderState.EXPIRED,
                                     OrderState.FAILED}),
    OrderState.FILLED:    frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED:  frozenset(),
    OrderState.EXPIRED:   frozenset(),
    OrderState.FAILED:    frozenset(),
}


class OrderStateMachine:
    """
    Lightweight reference implementation of the OMS state machine,
    derived directly from the TLA+ AllowedTransitions table.

    This is the object that the test vectors exercise.  It raises
    ValueError on any transition not listed in _OMS_ALLOWED.
    """

    def __init__(self) -> None:
        self._state: OrderState = OrderState.PENDING

    @property
    def state(self) -> OrderState:
        """Current OMS state."""
        return self._state

    @property
    def is_terminal(self) -> bool:
        """True iff the order has reached a terminal state."""
        return self._state in _OMS_TERMINAL

    def transition(self, to: OrderState) -> None:
        """
        Attempt a transition to *to*.

        Raises
        ------
        ValueError
            If the transition is not permitted by the OMS spec.
        RuntimeError
            If the order is already in a terminal state.
        """
        if self._state == to:
            return  # no-op — idempotent same-state call

        if self._state in _OMS_TERMINAL:
            raise RuntimeError(
                f"Order is already in terminal state {self._state!r}; "
                f"transition to {to!r} is forbidden."
            )

        allowed = _OMS_ALLOWED[self._state]
        if to not in allowed:
            raise ValueError(
                f"Illegal OMS transition: {self._state!r} → {to!r}. "
                f"Permitted targets: {sorted(s.value for s in allowed)}"
            )

        self._state = to

    def apply_sequence(self, path: list[OrderState]) -> None:
        """Apply a full sequence of states (teleporting if needed for tests)."""
        for state in path:
            self.transition(state)


# ─────────────────────────────────────────────────────────────────────────────
# OMS Tests — derived from TLA+ model paths
# ─────────────────────────────────────────────────────────────────────────────

class TestOMSValidPaths:
    """
    Sequences proven reachable by the TLA+ OMS model checker.

    Each 'path' is a list of states starting AFTER PENDING (the initial
    state).  The machine is initialised at PENDING before each test.
    """

    @pytest.mark.parametrize("path", [
        # Happy path: full fill
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.FILLED],
        # Partial → full fill
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.PARTIAL,
         OrderState.FILLED],
        # Immediate cancel from PENDING
        [OrderState.CANCELLED],
        # Reject at exchange
        [OrderState.SUBMITTED, OrderState.REJECTED],
        # Cancel at SUBMITTED (exchange not yet acked)
        [OrderState.SUBMITTED, OrderState.CANCELLED],
        # Cancel at OPEN
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.CANCELLED],
        # Cancel at PARTIAL
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.PARTIAL,
         OrderState.CANCELLED],
        # Expire at OPEN
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.EXPIRED],
        # Expire at PARTIAL
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.PARTIAL,
         OrderState.EXPIRED],
        # Local failure from PENDING
        [OrderState.FAILED],
        # Network failure after SUBMITTED
        [OrderState.SUBMITTED, OrderState.FAILED],
        # Exchange error after OPEN
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.FAILED],
        # Failure mid-partial
        [OrderState.SUBMITTED, OrderState.OPEN, OrderState.PARTIAL,
         OrderState.FAILED],
    ])
    def test_valid_path_reaches_terminal(self, path: list[OrderState]) -> None:
        """Every valid path must end in a terminal state."""
        sm = OrderStateMachine()
        sm.apply_sequence(path)
        assert sm.is_terminal, (
            f"Expected terminal after path {[s.value for s in path]}, "
            f"got {sm.state!r}"
        )

    def test_initial_state_is_pending(self) -> None:
        """Machine must boot in PENDING — mirrors TLA+ Init predicate."""
        sm = OrderStateMachine()
        assert sm.state == OrderState.PENDING
        assert not sm.is_terminal

    def test_same_state_is_noop(self) -> None:
        """Transitioning to the current state must be a no-op."""
        sm = OrderStateMachine()
        sm.transition(OrderState.PENDING)
        assert sm.state == OrderState.PENDING

    def test_all_terminal_states_are_sinks(self) -> None:
        """TLA+ TerminalSink: no outgoing edges from any terminal state."""
        for terminal in _OMS_TERMINAL:
            assert _OMS_ALLOWED[terminal] == frozenset(), (
                f"Terminal state {terminal!r} has outgoing edges — spec violation"
            )

    def test_non_terminal_states_have_at_least_one_successor(self) -> None:
        """Every non-terminal state must have at least one valid successor."""
        non_terminal = set(OrderState) - _OMS_TERMINAL
        for state in non_terminal:
            assert len(_OMS_ALLOWED[state]) >= 1, (
                f"Non-terminal state {state!r} has no successors — deadlock"
            )


class TestOMSIllegalTransitions:
    """
    Sequences the TLA+ model proved UNREACHABLE or INVARIANT-VIOLATING.

    Each test asserts that ValueError or RuntimeError is raised.
    """

    @pytest.mark.parametrize("from_state,to_state", [
        # Back-edges that are explicitly banned
        (OrderState.FILLED,    OrderState.OPEN),
        (OrderState.FILLED,    OrderState.PENDING),
        (OrderState.FILLED,    OrderState.SUBMITTED),
        (OrderState.CANCELLED, OrderState.PENDING),
        (OrderState.CANCELLED, OrderState.OPEN),
        (OrderState.REJECTED,  OrderState.SUBMITTED),
        (OrderState.EXPIRED,   OrderState.OPEN),
        (OrderState.EXPIRED,   OrderState.PARTIAL),
        # Skipping intermediate states
        (OrderState.PENDING,   OrderState.OPEN),       # skip SUBMITTED
        (OrderState.PENDING,   OrderState.PARTIAL),    # skip 2 states
        (OrderState.PENDING,   OrderState.FILLED),     # jump to terminal
        (OrderState.SUBMITTED, OrderState.PARTIAL),    # skip OPEN
        (OrderState.SUBMITTED, OrderState.FILLED),     # skip OPEN+PARTIAL
        (OrderState.OPEN,      OrderState.SUBMITTED),  # backwards
        (OrderState.PARTIAL,   OrderState.OPEN),       # backwards
        (OrderState.PARTIAL,   OrderState.SUBMITTED),  # backwards
        (OrderState.PARTIAL,   OrderState.PENDING),    # backwards
    ])
    def test_illegal_transition_raises(
        self, from_state: OrderState, to_state: OrderState
    ) -> None:
        """Illegal transitions must raise ValueError."""
        sm = OrderStateMachine()
        # Teleport to from_state via valid path to set the stage
        _teleport_oms(sm, from_state)

        with pytest.raises((ValueError, RuntimeError)):
            sm.transition(to_state)

    def test_transition_from_filled_always_raises(self) -> None:
        """No transition out of FILLED is ever permitted."""
        for target in OrderState:
            if target == OrderState.FILLED:
                continue  # same-state noop is allowed
            with pytest.raises((ValueError, RuntimeError)):
                sm = OrderStateMachine()
                _teleport_oms(sm, OrderState.FILLED)
                sm.transition(target)

    def test_transition_from_cancelled_always_raises(self) -> None:
        """No transition out of CANCELLED is ever permitted."""
        for target in OrderState:
            if target == OrderState.CANCELLED:
                continue
            with pytest.raises((ValueError, RuntimeError)):
                sm = OrderStateMachine()
                _teleport_oms(sm, OrderState.CANCELLED)
                sm.transition(target)


# ── OMS teleport helper ─────────────────────────────────────────────────────

def _teleport_oms(sm: OrderStateMachine, target: OrderState) -> None:
    """
    Force the OMS machine to a specific state via a known-valid path.
    Used by tests that need to start from a non-PENDING state.
    """
    _ROUTES: dict[OrderState, list[OrderState]] = {
        OrderState.PENDING:   [],
        OrderState.SUBMITTED: [OrderState.SUBMITTED],
        OrderState.OPEN:      [OrderState.SUBMITTED, OrderState.OPEN],
        OrderState.PARTIAL:   [OrderState.SUBMITTED, OrderState.OPEN,
                               OrderState.PARTIAL],
        OrderState.FILLED:    [OrderState.SUBMITTED, OrderState.OPEN,
                               OrderState.FILLED],
        OrderState.CANCELLED: [OrderState.CANCELLED],
        OrderState.REJECTED:  [OrderState.SUBMITTED, OrderState.REJECTED],
        OrderState.EXPIRED:   [OrderState.SUBMITTED, OrderState.OPEN,
                               OrderState.EXPIRED],
        OrderState.FAILED:    [OrderState.FAILED],
    }
    sm.apply_sequence(_ROUTES[target])


# ─────────────────────────────────────────────────────────────────────────────
# Memory Lifecycle Tests — derived from TLA+ model paths
# ─────────────────────────────────────────────────────────────────────────────

from atlas.rag.state_machine import DocumentState, DocumentStateMachine  # noqa: E402


_MEM_TERMINAL: frozenset[DocumentState] = frozenset({DocumentState.ARCHIVED})

_MEM_ALLOWED: dict[DocumentState, frozenset[DocumentState]] = {
    DocumentState.RAW: frozenset({DocumentState.PENDING_VERIFICATION}),
    DocumentState.PENDING_VERIFICATION: frozenset({
        DocumentState.VERIFIED,
        DocumentState.FLAGGED_REVIEW,
        DocumentState.SOFT_DELETED,
    }),
    DocumentState.VERIFIED: frozenset({
        DocumentState.ARCHIVED,
        DocumentState.SOFT_DELETED,
        DocumentState.PENDING_VERIFICATION,   # contradiction loop
    }),
    DocumentState.FLAGGED_REVIEW: frozenset({DocumentState.SOFT_DELETED}),
    DocumentState.SOFT_DELETED:   frozenset({DocumentState.ARCHIVED}),
    DocumentState.ARCHIVED:       frozenset(),
}


class _PureMemSM:
    """
    Thin in-memory wrapper around DocumentStateMachine's _validate_transition
    so we can exercise the transition table without a real DB/Redis.
    """

    def __init__(self) -> None:
        self._state: DocumentState = DocumentState.RAW

    @property
    def state(self) -> DocumentState:
        return self._state

    def transition(self, to: DocumentState) -> None:
        if self._state == to:
            return  # idempotent
        # Reuse the real validation logic from the production class
        DocumentStateMachine._validate_transition(None, self._state, to)  # type: ignore[arg-type]
        self._state = to

    def apply_sequence(self, path: list[DocumentState]) -> None:
        for s in path:
            self.transition(s)


class TestMemoryValidPaths:
    """Sequences proven reachable by the TLA+ memory lifecycle model."""

    @pytest.mark.parametrize("path", [
        # Direct verification → archive (fast path)
        [DocumentState.PENDING_VERIFICATION,
         DocumentState.VERIFIED,
         DocumentState.ARCHIVED],
        # Verification → soft delete → archive
        [DocumentState.PENDING_VERIFICATION,
         DocumentState.VERIFIED,
         DocumentState.SOFT_DELETED,
         DocumentState.ARCHIVED],
        # Contradiction loop once, then archived
        [DocumentState.PENDING_VERIFICATION,
         DocumentState.VERIFIED,
         DocumentState.PENDING_VERIFICATION,   # back-edge fires
         DocumentState.VERIFIED,
         DocumentState.ARCHIVED],
        # Contradiction loop → soft delete
        [DocumentState.PENDING_VERIFICATION,
         DocumentState.VERIFIED,
         DocumentState.PENDING_VERIFICATION,
         DocumentState.SOFT_DELETED,
         DocumentState.ARCHIVED],
        # Failed verification → flagged → soft delete → archive
        [DocumentState.PENDING_VERIFICATION,
         DocumentState.FLAGGED_REVIEW,
         DocumentState.SOFT_DELETED,
         DocumentState.ARCHIVED],
        # Direct soft-delete from pending
        [DocumentState.PENDING_VERIFICATION,
         DocumentState.SOFT_DELETED,
         DocumentState.ARCHIVED],
    ])
    def test_valid_path_reaches_archived(
        self, path: list[DocumentState]
    ) -> None:
        """Every valid path starting from RAW must end at ARCHIVED."""
        sm = _PureMemSM()
        sm.apply_sequence(path)
        assert sm.state == DocumentState.ARCHIVED, (
            f"Expected ARCHIVED after path "
            f"{[s.value for s in path]}, got {sm.state!r}"
        )

    def test_initial_state_is_raw(self) -> None:
        """Machine boots in RAW — mirrors TLA+ MemInit predicate."""
        sm = _PureMemSM()
        assert sm.state == DocumentState.RAW

    def test_archived_is_sink(self) -> None:
        """TLA+ ArchivedIsTerminal: no outgoing edges from ARCHIVED."""
        assert _MEM_ALLOWED[DocumentState.ARCHIVED] == frozenset()

    def test_all_non_terminal_have_successors(self) -> None:
        """Every non-ARCHIVED state must have at least one successor."""
        for state in DocumentState:
            if state == DocumentState.ARCHIVED:
                continue
            assert len(_MEM_ALLOWED[state]) >= 1, (
                f"Non-terminal state {state!r} has no successors — deadlock"
            )

    def test_contradiction_loop_is_the_only_back_edge(self) -> None:
        """
        TLA+ CycleOnlyVerified: the only permitted 'backwards' edge is
        VERIFIED → PENDING_VERIFICATION.  All other state orderings must
        be strictly forward.
        """
        state_order = {
            DocumentState.RAW:                  0,
            DocumentState.PENDING_VERIFICATION: 1,
            DocumentState.VERIFIED:             2,
            DocumentState.FLAGGED_REVIEW:       2,
            DocumentState.SOFT_DELETED:         3,
            DocumentState.ARCHIVED:             4,
        }
        for src, targets in _MEM_ALLOWED.items():
            for tgt in targets:
                is_contradiction_loop = (
                    src == DocumentState.VERIFIED
                    and tgt == DocumentState.PENDING_VERIFICATION
                )
                if is_contradiction_loop:
                    continue   # the one allowed back-edge
                assert state_order[tgt] > state_order[src], (
                    f"Unexpected back-edge in memory spec: "
                    f"{src.value!r} → {tgt.value!r}"
                )

    def test_same_state_is_noop(self) -> None:
        """Transitioning to the current state must be a no-op."""
        sm = _PureMemSM()
        sm.transition(DocumentState.RAW)
        assert sm.state == DocumentState.RAW


class TestMemoryIllegalTransitions:
    """Sequences the TLA+ model proved UNREACHABLE or INVARIANT-VIOLATING."""

    @pytest.mark.parametrize("from_state,to_state", [
        # Back-edges that are not the contradiction loop
        (DocumentState.PENDING_VERIFICATION, DocumentState.RAW),
        (DocumentState.VERIFIED,             DocumentState.RAW),
        (DocumentState.FLAGGED_REVIEW,       DocumentState.RAW),
        (DocumentState.FLAGGED_REVIEW,       DocumentState.PENDING_VERIFICATION),
        (DocumentState.FLAGGED_REVIEW,       DocumentState.VERIFIED),
        (DocumentState.SOFT_DELETED,         DocumentState.RAW),
        (DocumentState.SOFT_DELETED,         DocumentState.PENDING_VERIFICATION),
        (DocumentState.SOFT_DELETED,         DocumentState.VERIFIED),
        (DocumentState.SOFT_DELETED,         DocumentState.FLAGGED_REVIEW),
        (DocumentState.ARCHIVED,             DocumentState.RAW),
        (DocumentState.ARCHIVED,             DocumentState.SOFT_DELETED),
        (DocumentState.ARCHIVED,             DocumentState.VERIFIED),
        # State skips
        (DocumentState.RAW,                  DocumentState.VERIFIED),
        (DocumentState.RAW,                  DocumentState.ARCHIVED),
        (DocumentState.RAW,                  DocumentState.SOFT_DELETED),
        (DocumentState.PENDING_VERIFICATION, DocumentState.ARCHIVED),
    ])
    def test_illegal_mem_transition_raises(
        self, from_state: DocumentState, to_state: DocumentState
    ) -> None:
        """Illegal memory transitions must raise ValueError."""
        sm = _PureMemSM()
        _teleport_mem(sm, from_state)
        with pytest.raises(ValueError):
            sm.transition(to_state)

    def test_archived_no_outbound_transitions(self) -> None:
        """No transition from ARCHIVED is ever permitted."""
        sm = _PureMemSM()
        _teleport_mem(sm, DocumentState.ARCHIVED)
        for target in DocumentState:
            if target == DocumentState.ARCHIVED:
                continue
            with pytest.raises(ValueError):
                sm2 = _PureMemSM()
                _teleport_mem(sm2, DocumentState.ARCHIVED)
                sm2.transition(target)


# ── Memory teleport helper ──────────────────────────────────────────────────

def _teleport_mem(sm: _PureMemSM, target: DocumentState) -> None:
    """Force the memory machine to a specific state via a known-valid path."""
    _ROUTES: dict[DocumentState, list[DocumentState]] = {
        DocumentState.RAW:                  [],
        DocumentState.PENDING_VERIFICATION: [DocumentState.PENDING_VERIFICATION],
        DocumentState.VERIFIED:             [DocumentState.PENDING_VERIFICATION,
                                             DocumentState.VERIFIED],
        DocumentState.FLAGGED_REVIEW:       [DocumentState.PENDING_VERIFICATION,
                                             DocumentState.FLAGGED_REVIEW],
        DocumentState.SOFT_DELETED:         [DocumentState.PENDING_VERIFICATION,
                                             DocumentState.SOFT_DELETED],
        DocumentState.ARCHIVED:             [DocumentState.PENDING_VERIFICATION,
                                             DocumentState.SOFT_DELETED,
                                             DocumentState.ARCHIVED],
    }
    sm.apply_sequence(_ROUTES[target])


# ─────────────────────────────────────────────────────────────────────────────
# Cross-machine property: state machine graph completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionTableCompleteness:
    """Structural integrity checks on both transition tables."""

    def test_oms_transition_table_covers_all_states(self) -> None:
        """Every OMS state must appear as a key in _OMS_ALLOWED."""
        for state in OrderState:
            assert state in _OMS_ALLOWED, (
                f"OMS state {state!r} missing from allowed-transitions table"
            )

    def test_memory_transition_table_covers_all_states(self) -> None:
        """Every DocumentState must appear as a key in _MEM_ALLOWED."""
        for state in DocumentState:
            assert state in _MEM_ALLOWED, (
                f"Memory state {state!r} missing from allowed-transitions table"
            )

    def test_oms_no_self_loops_in_allowed(self) -> None:
        """The OMS spec contains no explicit self-loops (handled as no-op)."""
        for state, targets in _OMS_ALLOWED.items():
            assert state not in targets, (
                f"OMS state {state!r} has a self-loop in the spec table"
            )

    def test_memory_no_self_loops_except_noop(self) -> None:
        """The memory spec contains no explicit self-loops."""
        for state, targets in _MEM_ALLOWED.items():
            assert state not in targets, (
                f"Memory state {state!r} has a self-loop in the spec table"
            )

    def test_oms_target_states_are_valid(self) -> None:
        """All target states in the OMS transition table must be valid."""
        for src, targets in _OMS_ALLOWED.items():
            for tgt in targets:
                assert tgt in set(OrderState), (
                    f"OMS table references unknown target state {tgt!r} "
                    f"from {src!r}"
                )

    def test_memory_target_states_are_valid(self) -> None:
        """All target states in the memory transition table must be valid."""
        for src, targets in _MEM_ALLOWED.items():
            for tgt in targets:
                assert tgt in set(DocumentState), (
                    f"Memory table references unknown target state {tgt!r} "
                    f"from {src!r}"
                )
