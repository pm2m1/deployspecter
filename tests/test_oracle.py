from deployspecter.models import ExecutionTrace, InvariantProposal, Requirement, SourceOp, StepRecord, WriterDecl
from deployspecter.oracle import evaluate, invariant_holds

REQ = Requirement(
    id="R", text="keep user value", kind="authoritative_write_preserved", table="t", key_column="id",
    column="v", writers=[WriterDecl(function="app:set", key_param="id", value_param="v")],
    assumptions=["the only authoritative writer is app:set"],
)
DELTA = Requirement(
    id="D", text="no lost credit", kind="committed_delta_sum", table="t", key_column="id", column="c",
    base_column="d", base_multiplier=100, writers=[WriterDecl(function="app:credit", key_param="id", value_param="a")],
)
TOMB = Requirement(
    id="T", text="stay deleted", kind="tombstone_preserved", table="t", key_column="id", column="deleted_at",
    writers=[WriterDecl(function="app:delete", key_param="id")],
)


def rec(seq, function="app:set", committed=True, rowcount=1, params=None, commit=1, status="ok"):
    return StepRecord(
        seq=seq, actor="u", step_index=seq, kind="sql", function=function, sql="UPDATE t SET v = 1",
        params=params if params is not None else {"id": 1, "v": "new"}, committed=committed,
        rowcount=rowcount, durable_commit_seq=commit, status=status,
    )


def trace(records, final, outcome="completed", initial=None):
    return ExecutionTrace(
        requested_schedule=[], issue_order=[], records=records, outcome=outcome,
        final_state={"t": final}, initial_state={"t": initial or []},
    )


def test_committed_write_lost_is_a_true_violation():
    verdict = evaluate([REQ], trace([rec(0)], [{"id": 1, "v": "old"}]))
    assert verdict.classification == "true_violation"
    v = verdict.violations[0]
    assert (v.expected, v.actual, v.key) == ("new", "old", 1)
    assert "the only authoritative writer is app:set" in verdict.assumptions


def test_rolled_back_write_is_not_a_violation():
    verdict = evaluate([REQ], trace([rec(0, committed=False)], [{"id": 1, "v": "old"}]))
    assert verdict.classification == "vacuous"
    assert not verdict.violations


def test_write_affecting_no_rows_is_not_authoritative():
    verdict = evaluate([REQ], trace([rec(0, rowcount=0)], [{"id": 1, "v": "old"}]))
    assert verdict.classification == "vacuous"


def test_deadlock_victim_is_a_conflict_not_corruption():
    verdict = evaluate(
        [REQ], trace([rec(0, committed=False, status="error")], [{"id": 1, "v": "old"}], "transaction_conflict")
    )
    assert verdict.classification == "transaction_conflict"
    assert not verdict.violations


def test_execution_error_is_distinct_from_corruption():
    assert evaluate([REQ], trace([], [], "execution_error")).classification == "execution_error"


def test_last_committed_writer_wins_by_commit_order_not_issue_order():
    records = [rec(0, params={"id": 1, "v": "later-commit"}, commit=2),
               rec(1, params={"id": 1, "v": "earlier-commit"}, commit=1)]
    verdict = evaluate([REQ], trace(records, [{"id": 1, "v": "later-commit"}]))
    assert verdict.classification == "no_violation"


def test_missing_row_after_committed_write_is_a_violation():
    assert evaluate([REQ], trace([rec(0)], [])).classification == "true_violation"


def test_delta_sum_detects_lost_credit_and_accepts_conserved_total():
    records = [rec(0, function="app:credit", params={"id": 1, "a": 500})]
    initial = [{"id": 1, "d": 10, "c": 1000}]
    assert evaluate([DELTA], trace(records, [{"id": 1, "d": 15, "c": 1000}], initial=initial)).violations
    assert not evaluate([DELTA], trace(records, [{"id": 1, "d": 15, "c": 1500}], initial=initial)).violations


def test_delta_sum_says_nothing_about_unpopulated_target():
    records = [rec(0, function="app:credit", params={"id": 1, "a": 500})]
    initial = [{"id": 1, "d": 10, "c": None}]
    verdict = evaluate([DELTA], trace(records, [{"id": 1, "d": 15, "c": None}], initial=initial))
    assert verdict.classification == "vacuous"


def test_delta_sum_does_not_judge_a_seed_that_contradicts_its_model():
    records = [rec(0, function="app:credit", params={"id": 1, "a": 500})]
    initial = [{"id": 1, "d": 10, "c": 999}]
    verdict = evaluate([DELTA], trace(records, [{"id": 1, "d": 15, "c": 1499}], initial=initial))
    assert verdict.classification == "vacuous"


def test_tombstone_detects_resurrection_only():
    records = [rec(0, function="app:delete", params={"id": 1})]
    assert evaluate([TOMB], trace(records, [{"id": 1, "deleted_at": None}])).violations
    assert not evaluate([TOMB], trace(records, [{"id": 1, "deleted_at": "<timestamp>"}])).violations
    assert not evaluate([TOMB], trace(records, [])).violations


def test_candidate_invariant_kinds():
    state = {"t": [{"id": 1, "v": "x", "w": "x", "n": None}]}
    base = dict(id="i", description="", table="t", key_column="id", key_value=1)
    assert invariant_holds(InvariantProposal(**base, kind="column_equals", column="v", expected=["x"]), state)
    assert not invariant_holds(InvariantProposal(**base, kind="column_in", column="v", expected=["y"]), state)
    assert invariant_holds(InvariantProposal(**base, kind="column_is_null", column="n"), state)
    assert invariant_holds(InvariantProposal(**base, kind="row_exists"), state)
    assert not invariant_holds(InvariantProposal(**base, kind="row_absent"), state)
    assert invariant_holds(InvariantProposal(**base, kind="column_equals_column", column="v", other_column="w"), state)


REPLICA = Requirement(
    id="P", text="pair stays", kind="replica_pair_retained", table="d", key_column="u", column="a",
    source_table="s", key_columns=["u", "p"], source_ops=[
        SourceOp(function="f:del", op="delete", params={"u": "u", "p": "p", "access_level": "a"}),
        SourceOp(function="f:ins", op="insert", params={"u": "u", "p": "p", "access_level": "a"}),
    ],
)


def replica_trace(records, initial, final):
    return ExecutionTrace(
        requested_schedule=[], issue_order=[], records=records, outcome="completed",
        initial_state=initial, final_state=final,
    )


def op(function, a, commit=1, committed=True, rowcount=1):
    return StepRecord(
        seq=commit, actor="x", step_index=0, kind="sql", function=function, sql="-", committed=committed,
        rowcount=rowcount, durable_commit_seq=commit, params={"u": 1, "p": 1, "a": a},
    )


TWO = {"s": [{"u": 1, "p": 1, "a": 20}, {"u": 1, "p": 1, "a": 30}], "d": [{"u": 1, "p": 1, "a": 30}]}


def test_replica_retained_flags_a_lost_pair_and_nothing_else():
    lost = {"s": [{"u": 1, "p": 1, "a": 20}], "d": []}
    kept = {"s": [{"u": 1, "p": 1, "a": 20}], "d": [{"u": 1, "p": 1, "a": 20}]}
    assert evaluate([REPLICA], replica_trace([op("f:del", 30)], TWO, lost)).violations
    assert not evaluate([REPLICA], replica_trace([op("f:del", 30)], TWO, kept)).violations


def test_replica_rolled_back_or_empty_operations_are_ignored():
    for record in (op("f:del", 30, committed=False), op("f:del", 99, rowcount=0)):
        verdict = evaluate([REPLICA], replica_trace([record], TWO, TWO))
        assert verdict.classification == "vacuous"


def test_replica_pair_absent_before_any_sync_is_not_judged():
    unsynced = {"s": [{"u": 1, "p": 1, "a": 20}], "d": []}
    assert not evaluate([REPLICA], replica_trace([], unsynced, unsynced)).violations
