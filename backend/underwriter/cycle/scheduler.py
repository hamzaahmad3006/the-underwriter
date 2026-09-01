"""The scheduler — TD-01, §13.2, OPS-020.

APScheduler in-process, one instance, three jobs. Not Celery, not a second
service: OPS-020 requires exactly one scheduler, and running it inside the API
process makes that structural rather than a deployment note. Two schedulers
would double-submit, and no amount of idempotency downstream makes that a
situation worth being in.

Cadences from §13.2 — underwrite 30 min, manage 15, reconcile 5.

Three properties matter more than the scheduling itself:

* **`max_instances=1` and `coalesce=True`.** A cycle that overruns must not
  start a second copy of itself, and a run missed while the process was down
  is folded into one catch-up rather than replayed as a backlog.
* **Every job records a `SchedulerRun`** (DB-020) including the ones that did
  nothing. `NO_ACTION` is a distinct status from `ERROR` (FR-026).
* **A job never raises.** ERR-006 alerts after three consecutive failures; one
  failure is recorded and the next tick tries again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from underwriter.cycle.underwrite import CycleStatus, new_correlation_id
from underwriter.db import session_scope
from underwriter.db.models import SchedulerRun, SystemEvent

log = logging.getLogger(__name__)

UNDERWRITE_INTERVAL_MIN = 30
MANAGE_INTERVAL_MIN = 15
RECONCILE_INTERVAL_MIN = 5
CONSECUTIVE_FAILURES_BEFORE_ALERT = 3  # ERR-006


@dataclass(frozen=True, slots=True)
class JobResult:
    """What a job wants recorded. Any cycle report reduces to this."""

    status: CycleStatus
    outcome: str
    detail: str = ""
    correlation_id: str | None = None


JobFn = Callable[[], JobResult]


class CycleScheduler:
    """Owns the three jobs and the record of what they did."""

    def __init__(
        self,
        *,
        underwrite: JobFn | None = None,
        manage: JobFn | None = None,
        reconcile: JobFn | None = None,
        underwrite_min: int = UNDERWRITE_INTERVAL_MIN,
        manage_min: int = MANAGE_INTERVAL_MIN,
        reconcile_min: int = RECONCILE_INTERVAL_MIN,
    ) -> None:
        self._jobs: dict[str, JobFn] = {}
        if underwrite is not None:
            self._jobs["underwrite"] = underwrite
        if manage is not None:
            self._jobs["manage"] = manage
        if reconcile is not None:
            self._jobs["reconcile"] = reconcile

        self._intervals = {
            "underwrite": underwrite_min,
            "manage": manage_min,
            "reconcile": reconcile_min,
        }
        self._failures: dict[str, int] = {}
        self._scheduler = BackgroundScheduler(timezone="UTC")

    # -- running one job -------------------------------------------------

    def run_job(self, name: str) -> JobResult:
        """Execute one job, record it, and never let it raise.

        The scheduler surviving a bad cycle is the whole point of this wrapper.
        A job that throws would kill its own trigger in some configurations and
        silently stop the desk.
        """
        job = self._jobs.get(name)
        if job is None:
            return JobResult(CycleStatus.ERROR, "UNKNOWN_JOB", f"no job named {name!r}")

        started = datetime.now(UTC)
        correlation_id = new_correlation_id()

        try:
            result = job()
        except Exception as exc:
            log.exception("cycle %s failed", name)
            result = JobResult(
                CycleStatus.ERROR,
                type(exc).__name__,
                str(exc),
                correlation_id=correlation_id,
            )

        self._track_failures(name, result)
        self._record(name, result, started, correlation_id)
        return result

    def _track_failures(self, name: str, result: JobResult) -> None:
        """ERR-006: alert on three consecutive failures, not on the first."""
        if result.status is CycleStatus.ERROR:
            self._failures[name] = self._failures.get(name, 0) + 1
            if self._failures[name] >= CONSECUTIVE_FAILURES_BEFORE_ALERT:
                log.error(
                    "job %s has failed %d times consecutively (ERR-006)",
                    name,
                    self._failures[name],
                )
        else:
            self._failures[name] = 0

    def _record(self, name: str, result: JobResult, started: datetime, correlation_id: str) -> None:
        """DB-020, plus a `system_event` for anything that was not routine.

        Persistence failing must not take the cycle with it — the trade either
        happened or it did not, and losing the log of it is bad but losing the
        scheduler is worse.
        """
        finished = datetime.now(UTC)
        try:
            with session_scope() as session:
                session.add(
                    SchedulerRun(
                        job_name=name,
                        correlation_id=result.correlation_id or correlation_id,
                        started_at=started,
                        finished_at=finished,
                        status=str(result.status),
                        outcome=result.outcome,
                        error_message=result.detail if result.status is CycleStatus.ERROR else None,
                        duration_ms=int((finished - started).total_seconds() * 1000),
                    )
                )

                if result.status is not CycleStatus.SUCCESS:
                    session.add(
                        SystemEvent(
                            level="ERROR" if result.status is CycleStatus.ERROR else "INFO",
                            component=name,
                            event=result.outcome,
                            detail_json={"detail": result.detail},
                            correlation_id=result.correlation_id or correlation_id,
                        )
                    )
        except Exception:
            log.exception("could not record scheduler run for %s", name)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        for name in self._jobs:
            self._scheduler.add_job(
                self.run_job,
                trigger=IntervalTrigger(minutes=self._intervals[name]),
                args=[name],
                id=name,
                name=name,
                # A cycle that overruns must not start a second copy of itself.
                max_instances=1,
                # A run missed while the process was down folds into one
                # catch-up rather than replaying as a backlog of trades.
                coalesce=True,
                misfire_grace_time=60,
            )

        self._scheduler.start()
        log.info("scheduler started with jobs: %s", ", ".join(self._jobs))

    def shutdown(self, *, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    def status(self) -> dict[str, Any]:
        """What the dashboard shows for `/scheduler/runs` and system status."""
        return {
            "running": self.running,
            "jobs": [
                {
                    "name": name,
                    "interval_min": self._intervals[name],
                    "consecutive_failures": self._failures.get(name, 0),
                    "next_run": (
                        job.next_run_time.isoformat()
                        if (job := self._scheduler.get_job(name)) is not None
                        and job.next_run_time is not None
                        else None
                    ),
                }
                for name in self._jobs
            ],
        }
