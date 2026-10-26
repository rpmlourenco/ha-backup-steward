import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.cancellation import RunCancelled, RunControl


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_interruptible_worker_finally_is_awaited(self):
        control = RunControl()
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def work():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                stopped.set()

        task = asyncio.create_task(control.interruptible(work()))
        await entered.wait()
        control.request()
        with self.assertRaises(RunCancelled):
            await task
        self.assertTrue(stopped.is_set())
        self.assertIsNone(control.active)

    async def test_app_stop_remains_cancelled_error_even_after_user_request(self):
        control = RunControl()
        entered = asyncio.Event()

        async def work():
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(control.interruptible(work()))
        await entered.wait()
        control.request()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_pre_requested_work_never_starts(self):
        control = RunControl()
        control.request()
        executed = False

        async def work():
            nonlocal executed
            executed = True

        with self.assertRaises(RunCancelled):
            await control.interruptible(work())
        self.assertFalse(executed)
