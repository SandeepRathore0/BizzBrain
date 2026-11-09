"""Unit tests for OutboxWorker and ReconciliationWorker lifecycles."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.workers.outbox_worker import OutboxWorker, main as outbox_main
from app.workers.reconciliation_worker import ReconciliationWorker, main as recon_main


@pytest.mark.asyncio
async def test_reconciliation_worker_run_once():
    mock_service = MagicMock()
    mock_service.reconcile_expired_payments = AsyncMock(return_value=3)
    mock_service.reconcile_uncertain_captures = AsyncMock(return_value={"completed": 1, "unchanged": 2})
    mock_service.monitor_system_health = AsyncMock(return_value={"allocation_stuck": 0, "review_stuck": 0, "dead_outbox": 0})

    worker = ReconciliationWorker(recon_service=mock_service)
    summary = await worker.run_once()

    assert summary["expired_payments"] == 3
    assert summary["recon_results"]["completed"] == 1
    assert summary["monitor_alerts"]["dead_outbox"] == 0
    mock_service.reconcile_expired_payments.assert_awaited_once()
    mock_service.reconcile_uncertain_captures.assert_awaited_once()
    mock_service.monitor_system_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconciliation_worker_lifecycle_and_signal():
    mock_service = MagicMock()
    mock_service.reconcile_expired_payments = AsyncMock(return_value=0)
    mock_service.reconcile_uncertain_captures = AsyncMock(return_value={})
    mock_service.monitor_system_health = AsyncMock(return_value={})

    worker = ReconciliationWorker(recon_service=mock_service)

    # Test signal handler
    worker.handle_signal(15, None)
    assert worker.running is False
    assert worker.shutting_down is True

    # Run loop and stop after one iteration
    async def stop_after_one(*args, **kwargs):
        worker.stop()

    with patch.object(worker, "run_once", side_effect=stop_after_one) as mock_run_once:
        await worker.run()
        mock_run_once.assert_awaited_once()
    assert worker.running is False


@pytest.mark.asyncio
async def test_reconciliation_worker_loop_exception():
    mock_service = MagicMock()
    worker = ReconciliationWorker(recon_service=mock_service)

    async def fail_once(*args, **kwargs):
        worker.stop()
        raise RuntimeError("Transient error in cycle")

    with patch.object(worker, "run_once", side_effect=fail_once):
        await worker.run()
    assert worker.running is False


@pytest.mark.asyncio
async def test_outbox_worker_lifecycle_and_signal():
    mock_service = MagicMock()
    worker = OutboxWorker(outbox_service=mock_service)
    worker.handle_signal(15, None)
    assert worker.running is False
    assert worker.shutting_down is True


@pytest.mark.asyncio
async def test_outbox_worker_processes_batch_and_stops():
    mock_service = MagicMock()
    mock_service.is_circuit_open.return_value = False
    mock_service.get_queue_stats = AsyncMock(return_value={"pending": 1, "in_flight": 0})
    event = {"event_id": "test-1"}

    mock_service.claim_batch = AsyncMock(return_value=[event])

    async def fake_process(ev):
        worker.stop()

    mock_service.process_event = AsyncMock(side_effect=fake_process)

    worker = OutboxWorker(outbox_service=mock_service)
    await worker.run()

    assert mock_service.process_event.await_count == 1
    assert worker.running is False


@pytest.mark.asyncio
async def test_outbox_worker_empty_batch_sleep():
    mock_service = MagicMock()
    mock_service.is_circuit_open.return_value = False
    mock_service.get_queue_stats = AsyncMock(return_value={"pending": 0, "in_flight": 0})
    mock_service.claim_batch = AsyncMock(return_value=[])

    worker = OutboxWorker(outbox_service=mock_service)

    async def fake_sleep(duration):
        worker.stop()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await worker.run()

    assert worker.running is False


@pytest.mark.asyncio
async def test_outbox_worker_handles_circuit_open():
    mock_service = MagicMock()
    mock_service.is_circuit_open.return_value = True
    mock_service.get_queue_stats = AsyncMock(return_value={"pending": 1, "in_flight": 0})

    worker = OutboxWorker(outbox_service=mock_service)

    async def fake_sleep(duration):
        worker.stop()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await worker.run()

    assert worker.running is False


@pytest.mark.asyncio
async def test_outbox_worker_loop_exception():
    mock_service = MagicMock()
    mock_service.is_circuit_open.return_value = False
    mock_service.get_queue_stats = AsyncMock(return_value={"pending": 0})

    async def raise_claim(*args, **kwargs):
        worker.stop()
        raise RuntimeError("DB connection dropped")

    mock_service.claim_batch = AsyncMock(side_effect=raise_claim)
    worker = OutboxWorker(outbox_service=mock_service)

    with patch("asyncio.sleep", new=AsyncMock()):
        await worker.run()

    assert worker.running is False


def test_main_entrypoints():
    with patch("asyncio.run") as mock_asyncio_run, patch("signal.signal"):
        mock_asyncio_run.side_effect = lambda coro: coro.close()
        outbox_main()
        assert mock_asyncio_run.call_count == 1

    with patch("asyncio.run") as mock_asyncio_run, patch("signal.signal"):
        mock_asyncio_run.side_effect = lambda coro: coro.close()
        recon_main()
        assert mock_asyncio_run.call_count == 1
