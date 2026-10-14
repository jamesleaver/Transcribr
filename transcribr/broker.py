"""Server-sent-event fan-out to connected clients."""

import queue
import threading


class EventBroker:
    """Fans server-side events out to any number of SSE subscribers.

    Events carry monotonically increasing ids and are kept in a bounded
    ring so a reconnecting client (Last-Event-ID) can replay what it
    missed; when the gap is wider than the ring the subscriber is told
    to resync via GET /api/state instead."""

    _RING_SIZE = 500

    def __init__(self):
        from collections import deque
        self._lock = threading.Lock()
        self._seq = 0
        self._subscribers = set()
        self._ring = deque(maxlen=self._RING_SIZE)

    def publish(self, event, data):
        import json
        with self._lock:
            self._seq += 1
            item = (self._seq, event, json.dumps(data))
            self._ring.append(item)
            targets = list(self._subscribers)
        for q in targets:
            # A subscriber that has stopped reading - a tab the browser
            # has frozen, or a window whose socket is still open and
            # going nowhere - blocks its own stream, not this thread,
            # and unsubscribe only runs when the connection actually
            # drops. An unbounded queue behind that grows for as long as
            # the run does, and a run can be hours.
            #
            # Past the ring's length there is nothing left to catch it
            # up with anyway, which is the same position a client with
            # too old a Last-Event-ID is in - so it gets the same
            # answer: resync, and the stream ends.
            if q.qsize() >= self._RING_SIZE:
                self._drop_behind(q)
                continue
            q.put(item)

    def _drop_behind(self, q):
        """Stop feeding a subscriber that cannot keep up, and leave it a
        note saying to start again."""
        self.unsubscribe(q)
        try:
            q.put_nowait(_RESYNC)
        except queue.Full:         # pragma: no cover - room is reserved
            pass

    def subscribe(self, last_event_id=None):
        """Register a subscriber. Returns (queue, backlog): backlog is
        the list of missed events, or None when the gap can't be
        bridged and the client must do a full resync."""
        # One slot beyond the ring, so there is always room for the
        # resync notice _drop_behind leaves.
        q = queue.Queue(maxsize=self._RING_SIZE + 1)
        with self._lock:
            self._subscribers.add(q)
            if last_event_id is None:
                return q, []
            ring = list(self._ring)
        if not ring or last_event_id < ring[0][0] - 1:
            return q, None
        return q, [item for item in ring if item[0] > last_event_id]

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)


# Delivered in place of an event to a subscriber that has fallen
# further behind than the ring can replay. The stream turns it into the
# same "resync" event a client with too old a Last-Event-ID gets, and
# then ends; EventSource reconnects on its own.
_RESYNC = (None, "resync", "{}")


def _sse_format(item):
    seq, event, payload = item
    return f"id: {seq}\nevent: {event}\ndata: {payload}\n\n"
