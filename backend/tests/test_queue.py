"""
Unit tests for Redis task queue.
Tests enqueue, dequeue, FIFO ordering, size inspection, and clear operations.
"""

import unittest
import uuid

from backend.app.workers.queue import TaskQueue, get_task_queue


class TestTaskQueue(unittest.TestCase):
    """Test suite for Redis-backed TaskQueue."""

    def setUp(self):
        # Use a dedicated test queue to avoid interfering with any other queue
        self.queue_name = f"test_queue_{uuid.uuid4().hex[:8]}"
        self.queue = TaskQueue(queue_name=self.queue_name)
        if not self.queue.ping():
            self.skipTest("Redis is not running on localhost:6379")

    def tearDown(self):
        self.queue.clear()

    def test_redis_connection_ping(self):
        """Redis server must respond to ping."""
        self.assertTrue(self.queue.ping())

    def test_enqueue_and_dequeue_fifo(self):
        """Messages enqueued in FIFO order are dequeued in the same order."""
        task_id_1 = uuid.uuid4()
        task_id_2 = uuid.uuid4()
        biz_id = uuid.uuid4()

        # Enqueue two tasks
        self.queue.enqueue(
            task_id=task_id_1,
            task_type="parse_invoice",
            business_id=biz_id,
            payload={"doc_num": 1},
        )
        self.queue.enqueue(
            task_id=task_id_2,
            task_type="parse_invoice",
            business_id=biz_id,
            payload={"doc_num": 2},
        )

        self.assertEqual(self.queue.size(), 2)

        # Dequeue first task
        msg1 = self.queue.dequeue(timeout=1)
        self.assertIsNotNone(msg1)
        self.assertEqual(msg1["task_id"], str(task_id_1))
        self.assertEqual(msg1["payload"]["doc_num"], 1)

        # Dequeue second task
        msg2 = self.queue.dequeue(timeout=1)
        self.assertIsNotNone(msg2)
        self.assertEqual(msg2["task_id"], str(task_id_2))
        self.assertEqual(msg2["payload"]["doc_num"], 2)

        # Dequeue empty queue returns None
        empty_msg = self.queue.dequeue(timeout=1)
        self.assertIsNone(empty_msg)

    def test_clear_queue(self):
        """Clearing the queue resets size to zero."""
        self.queue.enqueue(
            task_id=uuid.uuid4(),
            task_type="test",
            business_id=uuid.uuid4(),
        )
        self.assertEqual(self.queue.size(), 1)
        self.queue.clear()
        self.assertEqual(self.queue.size(), 0)


if __name__ == "__main__":
    unittest.main()
