"""The event fan-out, and what it does about a client that stops
reading.

A subscriber's queue is drained by its own SSE stream. If the client
stops reading the socket, the stream blocks on the write rather than on
the queue, and `unsubscribe` only runs when the connection actually
drops - which a frozen tab with an open socket never does. Behind that,
an unbounded queue grows for as long as the run does, and a run can be
hours.
"""

import unittest

import _harness  # noqa: F401  - isolates the config/log directories

from transcribr.broker import EventBroker, _RESYNC, _sse_format


class ReplayingWhatAClientMissed(unittest.TestCase):
    def test_a_fresh_subscriber_gets_no_backlog(self):
        b = EventBroker()
        b.publish("progress", {"pct": 1})
        q, backlog = b.subscribe()
        self.assertEqual(backlog, [])

    def test_a_reconnecting_client_gets_what_it_missed(self):
        b = EventBroker()
        b.publish("progress", {"pct": 1})
        b.publish("progress", {"pct": 2})
        b.publish("progress", {"pct": 3})
        _q, backlog = b.subscribe(last_event_id=1)
        self.assertEqual([i[0] for i in backlog], [2, 3])

    def test_a_gap_wider_than_the_ring_asks_for_a_full_resync(self):
        b = EventBroker()
        for i in range(b._RING_SIZE + 50):
            b.publish("progress", {"pct": i})
        _q, backlog = b.subscribe(last_event_id=1)
        self.assertIsNone(backlog)

    def test_an_event_reaches_the_queue_or_the_backlog_but_not_both(self):
        b = EventBroker()
        b.publish("progress", {"pct": 1})
        q, backlog = b.subscribe(last_event_id=0)
        b.publish("progress", {"pct": 2})
        seen = [i[0] for i in backlog]
        while q.qsize():
            seen.append(q.get_nowait()[0])
        self.assertEqual(seen, sorted(set(seen)))
        self.assertEqual(seen, [1, 2])


class AClientThatStopsReading(unittest.TestCase):
    def setUp(self):
        self.b = EventBroker()
        self.stuck, _ = self.b.subscribe()
        self.healthy, _ = self.b.subscribe()

    def _publish(self, n):
        for i in range(n):
            self.b.publish("review_grew", {"n": i})
            while self.healthy.qsize():
                self.healthy.get_nowait()

    def test_its_queue_stops_growing(self):
        self._publish(3000)
        self.assertLessEqual(self.stuck.qsize(), self.b._RING_SIZE + 1)

    def test_it_is_dropped_rather_than_fed_for_ever(self):
        self._publish(3000)
        self.assertNotIn(self.stuck, self.b._subscribers)

    def test_it_is_told_to_start_again(self):
        self._publish(3000)
        items = []
        while self.stuck.qsize():
            items.append(self.stuck.get_nowait())
        self.assertIs(items[-1], _RESYNC)

    def test_a_client_keeping_up_is_unaffected(self):
        self._publish(3000)
        self.assertIn(self.healthy, self.b._subscribers)
        self.b.publish("progress", {"pct": 50})
        self.assertEqual(self.healthy.qsize(), 1)

    def test_the_resync_notice_is_not_formatted_as_an_ordinary_event(self):
        # The stream must recognise it and end, not emit "id: None".
        self.assertIsNone(_RESYNC[0])
        self.assertIn("id: None", _sse_format(_RESYNC))


if __name__ == "__main__":
    unittest.main()
