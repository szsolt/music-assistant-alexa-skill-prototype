"""Reads of a page's SSE stream that fail the test when no message comes, instead of hanging it."""

import json
import queue
import threading

from skill.page.html import channel

WAIT_S = 2


def next_chunk(chunks, timeout=WAIT_S):
    """The stream's next chunk, within timeout."""
    got = queue.Queue()
    threading.Thread(target=lambda: got.put(next(chunks, None)), daemon=True).start()
    try:
        return got.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError("no message came on the page's stream") from None


MARK = {"mark": True}
MARK_CHUNK = f"data: {json.dumps(MARK)}\n\n".encode()


def up_to_mark(page_id, messages):
    """What the skill sent the page so far: the messages on queue messages before a mark sent now."""
    channel.send(page_id, MARK)
    got = []
    try:
        while (message := messages.get(timeout=WAIT_S)) != MARK:
            got.append(message)
    except queue.Empty:
        raise AssertionError("no mark came on the page's stream") from None
    return got
