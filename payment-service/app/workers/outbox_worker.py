"""Outbox Worker background process.

Polls outbox_events with SKIP LOCKED, validates against JSON Schema,
delivers events to the Invoice Service with at-least-once semantics,
handles retries with full jitter exponential backoff, and tracks circuit breaker status.

Run with: python -m app.workers.outbox_worker
"""
import asyncio
import signal
import sys
import time
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.outbox_service import OutboxService, get_outbox_service

logger = get_logger("outbox_worker")


class OutboxWorker:
    def __init__(self, outbox_service: Optional[OutboxService] = None):
        self.service = outbox_service or get_outbox_service()
        self.running: bool = False
        self.shutting_down: bool = False
        self.active_tasks: set[asyncio.Task] = set()
        self.last_heartbeat: float = 0.0

    def handle_signal(self, sig, frame):
        logger.info("outbox_worker_received_signal", signal=sig)
        self.stop()

    def stop(self):
        self.running = False
        self.shutting_down = True

    async def run(self):
        self.running = True
        logger.info("outbox_worker_starting")

        settings = get_settings()

        while self.running:
            try:
                # 1. Heartbeat check every 30 seconds
                now_mono = time.monotonic()
                if now_mono - self.last_heartbeat >= 30.0:
                    try:
                        stats = await self.service.get_queue_stats()
                        logger.info("outbox_worker_heartbeat", **stats)
                    except Exception as h_exc:
                        logger.warn("outbox_heartbeat_failed", error=str(h_exc))
                    self.last_heartbeat = now_mono

                # 2. Circuit Breaker Check
                if self.service.is_circuit_open():
                    logger.warn("outbox_worker_circuit_open_waiting")
                    await asyncio.sleep(min(5.0, settings.OUTBOX_CIRCUIT_BREAKER_COOLDOWN_SECONDS))
                    continue

                # 3. Claim Batch of rows
                claimed = await self.service.claim_batch(
                    batch_size=settings.OUTBOX_BATCH_SIZE,
                    visibility_timeout_seconds=settings.OUTBOX_VISIBILITY_TIMEOUT_SECONDS,
                )

                if not claimed:
                    # Idle sleep
                    await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_IDLE)
                    continue

                logger.info("outbox_worker_claimed_batch", count=len(claimed))

                # 4. Process claimed events
                for event_data in claimed:
                    if self.shutting_down:
                        break
                    task = asyncio.create_task(self.service.process_event(event_data))
                    self.active_tasks.add(task)
                    task.add_done_callback(self.active_tasks.discard)

                # Await in-flight tasks in batch before next poll
                if self.active_tasks:
                    await asyncio.gather(*list(self.active_tasks), return_exceptions=True)

                # If work was found, small interval before next poll
                await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_WORK)

            except asyncio.CancelledError:
                break
            except Exception as loop_exc:
                logger.error("outbox_worker_loop_error", error=str(loop_exc))
                await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_IDLE)

        # Graceful shutdown: allow in-flight calls up to 15s to finish
        logger.info("outbox_worker_shutting_down", pending_tasks=len(self.active_tasks))
        if self.active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*list(self.active_tasks), return_exceptions=True),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.warn("outbox_worker_shutdown_timeout_reached")

        logger.info("outbox_worker_stopped_cleanly")


def main():
    worker = OutboxWorker()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, worker.handle_signal)
        except (ValueError, AttributeError):
            pass

    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
