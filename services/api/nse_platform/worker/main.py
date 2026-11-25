"""`nse-worker`: the standalone scheduler process (ADR-0002).

Job definitions and the schedule live in `nse_platform.worker.jobs`, shared with the API's optional
in-process scheduler (`RUN_SCHEDULER_IN_API=true`). Run this process *or* that flag, not both — though
advisory locks make it safe if you do.
"""

from __future__ import annotations

import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from nse_platform.core.config import get_settings
from nse_platform.core.logging import configure_logging
from nse_platform.worker.jobs import JOB_DEFAULTS, job_backtests, job_eod, register_jobs

log = logging.getLogger("nse_platform.worker")


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    return register_jobs(BlockingScheduler(timezone=s.market_timezone, job_defaults=JOB_DEFAULTS), s)


def run() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    sched = build_scheduler()

    def _stop(*_: object) -> None:
        log.info("worker_shutdown_requested")
        sched.shutdown(wait=True)  # graceful: let running jobs finish
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker_started", extra={"jobs": [j.id for j in sched.get_jobs()]})
    if "--once" in sys.argv:
        job_eod()
        job_backtests()
        return
    sched.start()


if __name__ == "__main__":
    run()
