"""EXPERIMENTAL: the deterministic (developer-written) invariant candidates, and two controls per domain.

Written from each application's stated promises: the reservations module's own docstring (R1-R3) and its handlers'
documented return statuses; Chancy's public job states and the cancel API's documented effect ("cancel a job",
leaving it failed). They are a nonblind baseline next to Muse's hypotheses, not independent oracles. The controls
check the validator: one tautology (must be classified vacuous) and one false statement (must be invalid).
"""

from __future__ import annotations

from typing import Any

from ..invariants.language import agg, agg_hyp, col, cond, const, row_hyp, term

R, C = "reservations", "chancy_jobs"


def reservations() -> list[dict[str, Any]]:
    seats = term("child_sum", table="reservations", fk="event_id", col="qty")
    return [
        row_hyp("D-R1", "an event's reserved counter equals the seats in its reservations", R, "events", [],
                [cond(col("reserved"), "eq", seats)]),
        row_hyp("D-R2", "an event's reservations never hold more seats than its capacity", R, "events", [],
                [cond(seats, "le", col("capacity"))]),
        row_hyp("D-R3", "an event's reserved counter never exceeds its capacity", R, "events", [],
                [cond(col("reserved"), "le", col("capacity"))]),
        row_hyp("D-R4", "every reservation holds at least one seat", R, "reservations", [],
                [cond(col("qty"), "ge", const(1))]),
        agg_hyp("D-R5", "reservations after = before + callers told reserved - callers told released", R,
                [agg("count", "reservations")], "eq",
                [agg("count", "reservations", state="initial"),
                 agg("count", "calls", filter_col="status", filter_str="reserved"),
                 agg("count", "calls", sign=-1, filter_col="status", filter_str="released")]),
        agg_hyp("D-R6", "capacities never change", R, [agg("sum", "events", "capacity")], "eq",
                [agg("sum", "events", "capacity", state="initial")]),
        agg_hyp("D-R7", "each caller told rejected left exactly one new rejection row", R,
                [agg("count", "rejections"), agg("count", "rejections", state="initial", sign=-1)], "eq",
                [agg("count", "calls", filter_col="status", filter_str="rejected")]),
        agg_hyp("X-R-taut", "CONTROL (tautology): the number of events is never negative", R,
                [agg("count", "events")], "ge", [agg("int", value=0)]),
        row_hyp("X-R-false", "CONTROL (false): every event has no seats reserved", R, "events", [],
                [cond(col("reserved"), "eq", const(0))]),
    ]


def chancy() -> list[dict[str, Any]]:
    return [
        row_hyp("D-C1", "a job whose cancellation updated its row ends failed", C, "jobs",
                [cond(col("cancelled"), "eq", const(True))], [cond(col("state"), "eq", const("failed"))]),
        row_hyp("D-C2", "a cancelled job keeps no claim", C, "jobs",
                [cond(col("cancelled"), "eq", const(True)), cond(col("has_claim_id"), "ne", const(None))],
                [cond(col("has_claim_id"), "eq", const(False))]),
        row_hyp("D-C3", "a succeeded job is completed", C, "jobs",
                [cond(col("state"), "eq", const("succeeded"))], [cond(col("completed"), "eq", const(True))]),
        row_hyp("D-C4", "a job never records more attempts than it allows", C, "jobs", [],
                [cond(col("attempts"), "le", col("max_attempts"))]),
        row_hyp("D-C5", "a cancelled job's handler runs at most once", C, "jobs",
                [cond(col("cancelled"), "eq", const(True))], [cond(col("executions"), "le", const(1))]),
        row_hyp("D-C6", "a succeeded job records at least one attempt", C, "jobs",
                [cond(col("state"), "eq", const("succeeded"))], [cond(col("attempts"), "ge", const(1))]),
        row_hyp("D-C7", "a job's handler never succeeds more often than it runs", C, "jobs", [],
                [cond(col("successes"), "le", col("executions"))]),
        agg_hyp("X-C-taut", "CONTROL (tautology): the number of jobs is never negative", C,
                [agg("count", "jobs")], "ge", [agg("int", value=0)]),
        row_hyp("X-C-false", "CONTROL (false): every job ends succeeded", C, "jobs", [],
                [cond(col("state"), "eq", const("succeeded"))]),
    ]


INVARIANT_INSTRUCTIONS = ("You state invariant hypotheses: properties a correct version of the described application "
                          "should keep in every database state it reaches, written in the given restricted JSON "
                          "grammar over the given tables. Each hypothesis is checked by a deterministic validator "
                          "against real runs of the application; it is data, never "
                          "executed as code. Hypotheses that are false for the correct version, or that can never "
                          "be false, are rejected. Return JSON only.")

APPLICATION = {
    R: "a small Python reservations service: reserve_seats(event_id, holder, qty) reserves qty seats for holder if "
       "the event has that many free, else records a rejection and returns status 'rejected'; on success it adds a "
       "reservation and returns 'reserved'. release_seats(holder) releases one reservation the holder has and "
       "returns 'released', or returns 'nothing_held'. Tables: events, reservations, rejections, and `calls`: one "
       "row per handler call in the run with the status it returned.",
    C: "a PostgreSQL-backed job queue library. A producer pushes jobs (each with max_attempts) and may cancel them "
       "through the public cancel_job; workers claim jobs, run their handler and record the outcome. The `jobs` "
       "table is one row per job read back after the run, plus: cancelled (the producer's cancel_job updated the "
       "row), executions (times its handler ran) and successes (times its handler returned normally). Job states "
       "are pending, running, retrying, succeeded, failed.",
}
