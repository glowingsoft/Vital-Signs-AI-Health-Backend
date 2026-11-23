# Runs the cron jobs, one after another. One failing job never stops the others.
#
#   python -m app.cron_jobs.main                     run every job
#   python -m app.cron_jobs.main --job source_check  run one job
#
# Exit code 0 when every job ran, 1 when any job failed, so cron and monitoring can see it.
# A job is a function (plain or async) that returns a summary dict and raises when it failed.
# To add a job: write its run() function and add one line to JOBS.

import argparse
import asyncio
import inspect
import logging
import sys
import time
from typing import Callable, Dict, List, Optional

from app.cron_jobs.source_check import check_links

logger = logging.getLogger("app.cron_jobs")

JOBS: Dict[str, Callable] = {
    "source_check": check_links.run,
}


def run_job(name: str, job: Callable) -> bool:
    """Run one job and log its start, end, duration and summary. True when it finished without error."""
    logger.info("Cron job %s: started", name)
    started = time.monotonic()
    try:
        result = job()
        if inspect.isawaitable(result):
            result = asyncio.run(result)
    except Exception:
        logger.exception("Cron job %s: FAILED after %.1f s", name, time.monotonic() - started)
        return False
    logger.info("Cron job %s: finished in %.1f s: %s", name, time.monotonic() - started, result)
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scheduled jobs of the AI backend.")
    parser.add_argument("--job", choices=sorted(JOBS), help="run only this job (default: every job)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    names = [args.job] if args.job else list(JOBS)
    failed = [name for name in names if not run_job(name, JOBS[name])]
    if failed:
        logger.error("Cron jobs failed: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
