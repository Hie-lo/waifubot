"""Unit tests for BrowserSlotPool, MemoryGuard, and ChallengeGate (R28, R35)."""

import asyncio
import os
import shutil
import tempfile
import unittest

from waifubot.adapters.browser.pool import BrowserSlot, BrowserSlotPool
from waifubot.services.resources import ChallengeGate, ChallengeTimeoutError, MemoryGuard


class TestSlotPoolAndResources(unittest.IsolatedAsyncioTestCase):
    """Test suite covering browser slots, soft memory guard, and serialized challenge gate."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_slot_test_")

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_browser_slot_pool_isolation_and_capacity(self) -> None:
        """BrowserSlotPool yields isolated slots up to max_contexts and recycles them."""
        pool = BrowserSlotPool(
            max_contexts=2,
            storage_state_dir=self.temp_dir,
            mock_mode=True,
        )
        try:
            async with pool.acquire() as slot1:
                self.assertIsInstance(slot1, BrowserSlot)
                self.assertEqual(slot1.slot_id, "browser-slot-1")

                async with pool.acquire() as slot2:
                    self.assertEqual(slot2.slot_id, "browser-slot-2")

            # Slots are recycled and reusable
            async with pool.acquire() as slot_recycled:
                self.assertIn(slot_recycled.slot_id, ["browser-slot-1", "browser-slot-2"])
        finally:
            await pool.close()

    def test_memory_guard_soft_gate(self) -> None:
        """MemoryGuard blocks acquisition when simulated RAM is below threshold (R35)."""
        simulated_ram = 1024.0
        alerts: list[tuple[float, float]] = []

        guard = MemoryGuard(
            min_free_ram_mb=512.0,
            memory_reader=lambda: simulated_ram,
            on_low_memory_alert=lambda free, min_req: alerts.append((free, min_req)),
        )

        self.assertTrue(guard.can_acquire())
        self.assertEqual(len(alerts), 0)

        # Simulate RAM drops below threshold
        simulated_ram = 256.0
        self.assertFalse(guard.can_acquire())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0], (256.0, 512.0))

        # Repeated check while still low does NOT fire redundant alerts (R35)
        self.assertFalse(guard.can_acquire())
        self.assertEqual(len(alerts), 1)

        # Memory recovers
        simulated_ram = 800.0
        self.assertTrue(guard.can_acquire())

    async def test_challenge_gate_serialization(self) -> None:
        """ChallengeGate serializes challenge window access and enforces wait timeout (R28)."""
        gate = ChallengeGate()
        order: list[str] = []

        async def worker_1() -> None:
            async with gate.acquire_challenge(wait_timeout_seconds=5.0):
                order.append("w1_acquired")
                await asyncio.sleep(0.1)
                order.append("w1_done")

        async def worker_2() -> None:
            await asyncio.sleep(0.02)
            async with gate.acquire_challenge(wait_timeout_seconds=5.0):
                order.append("w2_acquired")
                order.append("w2_done")

        await asyncio.gather(worker_1(), worker_2())
        self.assertEqual(order, ["w1_acquired", "w1_done", "w2_acquired", "w2_done"])

    async def test_challenge_gate_timeout_raises(self) -> None:
        """Waiting longer than challenge_wait_seconds raises ChallengeTimeoutError (R28)."""
        gate = ChallengeGate()

        async with gate.acquire_challenge(wait_timeout_seconds=2.0):
            # Second acquisition with 0.05s timeout must time out
            with self.assertRaises(ChallengeTimeoutError):
                async with gate.acquire_challenge(wait_timeout_seconds=0.05):
                    pass


if __name__ == "__main__":
    unittest.main()
