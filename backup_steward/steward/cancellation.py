"""Cooperative stops at hardware boundaries; interruptible local I/O is awaited."""

import asyncio


class RunCancelled(Exception):
    pass


class RunControl:
    def __init__(self):
        self.accepting = True
        self.requested = False
        self.active = None

    def request(self):
        if not self.accepting:
            raise ValueError("Safe shutdown is already in progress; cancellation is unavailable")
        self.requested = True
        if self.active is not None:
            self.active.cancel()

    def checkpoint(self):
        if self.requested:
            raise RunCancelled()

    async def interruptible(self, operation):
        if self.requested:
            operation.close()
            self.checkpoint()
        task = asyncio.create_task(operation)
        self.active = task
        try:
            return await task
        except asyncio.CancelledError:
            # An app stop must remain an interruption, even if a user also asked to cancel.
            if self.requested and not asyncio.current_task().cancelling():
                raise RunCancelled() from None
            raise
        finally:
            self.active = None
