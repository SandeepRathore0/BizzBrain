"""Reconciliation Worker background process.

Periodically runs every 60s:
1. Job A: Expiry of stale checkouts (releases partial unique index).
2. Job B: Reconciliation of uncertain captures (reads from PayPal, uses complete_capture).
3. Job C: System monitoring alerts (stuck allocations, stuck reviews, dead outbox rows).

Run with: python -m app.workers.reconciliation_worker
"""
import asyncio
import signal
import sys
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.reconciliation_service import (
    ReconciliationService,
    get_reconciliation_service,
)

logger = get_logger("reconciliation_worker")


class ReconciliationWorker:
    def __init__(self, recon_service: Optional[ReconciliationService] = None):
        self.service = recon_service or get_reconciliation_service()
        self.running: bool = False
        self.shutting_down: bool = False

    def handle_signal(self, sig, frame):
        logger.info("reconciliation_worker_received_signal", signal=sig)
        self.stop()

    def stop(self):
        self.running = False
        self.shutting_down = True

    async def run_once(self) -> dict[str, any]:
        """Execute one pass of all three jobs."""
        logger.info("reconciliation_cycle_starting")

        # Job A: Expiry
        expired = await self.service.reconcile_expired_payments()

        # Job B: Uncertain captures
        recon_results = await self.service.reconcile_uncertain_captures()

        # Job C: Monitoring
        monitor_alerts = await self.service.monitor_system_health()

        summary = {
            "expired_payments": expired,
            "recon_results": recon_results,
            "monitor_alerts": monitor_alerts,
        }
        logger.info("reconciliation_cycle_completed", **summary)
        return summary

    async def run(self):
        self.running = True
        logger.info("reconciliation_worker_starting")
        settings = get_settings()

        while self.running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("reconciliation_cycle_failed", error=str(exc))

            # Sleep between cycles
            sleep_remaining = settings.RECON_INTERVAL_SECONDS
            while self.running and sleep_remaining > 0:
                await asyncio.sleep(min(1.0, sleep_remaining))
                sleep_remaining -= 1.0

        logger.info("reconciliation_worker_stopped_cleanly")


def main():
    worker = ReconciliationWorker()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, worker.handle_signal)
        except (ValueError, AttributeError):
            pass

    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
