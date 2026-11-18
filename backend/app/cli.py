"""Operational CLI.

    python -m app.cli seed            # reference data
    python -m app.cli worker          # standalone worker process (no API)
    python -m app.cli scheduler-tick  # run one scheduler pass
    python -m app.cli drain           # process all runnable jobs now
    python -m app.cli recover         # recover stale jobs
"""
from __future__ import annotations

import argparse
import signal
import threading

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope


def main() -> None:
    parser = argparse.ArgumentParser(prog="gbp")
    parser.add_argument("command", choices=["seed", "worker", "scheduler-tick", "drain", "recover"])
    args = parser.parse_args()
    configure_logging()
    get_settings().validate_for_startup()
    if args.command == "seed":
        from app.services.seed import seed_demo_institutions, seed_reference_data
        with session_scope() as db:
            seed_reference_data(db)
            if get_settings().demo_allowed:
                seed_demo_institutions(db)
        print("Reference data seeded")
    elif args.command == "worker":
        from app.scheduler.scheduler import AutomationScheduler
        from app.workers.worker import Worker
        stop = threading.Event()
        w, sch = Worker(), AutomationScheduler()
        w.start()
        sch.start()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        print("Worker + scheduler running. Ctrl+C to stop.")
        stop.wait()
        sch.stop()
        w.stop()
    elif args.command == "scheduler-tick":
        from app.scheduler.scheduler import tick
        print(f"Runs started: {tick()}")
    elif args.command == "drain":
        from app.workers.worker import run_pending_once
        print(f"Jobs processed: {run_pending_once(1000)}")
    elif args.command == "recover":
        from app.automation.queue import recover_stale
        with session_scope() as db:
            print(f"Recovered: {recover_stale(db)}")


if __name__ == "__main__":
    main()
