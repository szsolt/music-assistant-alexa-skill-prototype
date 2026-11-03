"""MA's hand-offs waiting for an open page: offered, taken once, missed."""

from skill import echo as echos
from skill import handoff


def test_live_only_after_recent_event():
    assert not handoff.is_live("d", now=100)
    handoff.heard_from("d", now=100)
    assert handoff.is_live("d", now=100 + handoff.LIVE_SECONDS)
    assert not handoff.is_live("d", now=101 + handoff.LIVE_SECONDS)


def test_offer_needs_open_page_and_is_taken_once():
    assert not handoff.offer("d", "stream", "u1")
    handoff.heard_from("d")
    assert handoff.offer("d", "stream", "u1")
    assert handoff.offer("d", "stream", "u2")      # replaces the untaken one
    assert handoff.take("d") == ("stream", "u2")
    assert handoff.take("d") is None
    handoff.offer("d", "stream", "u1")
    handoff.offer("d", "pause")
    handoff.offer("d", "stream", "u2")             # also replaces an untaken pause
    assert handoff.take("d") == ("stream", "u2")
    assert handoff.take("d") is None


def test_take_of_one_command_leaves_others():
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u1")
    assert handoff.take("d", "pause") is None
    assert handoff.take("d", "stream") == ("stream", "u1")
    handoff.closed("d")


def test_missed_handoff_calls_back_and_page_counts_closed(clock):
    got = []
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u", on_missed=lambda d, c: got.append((d, c)))
    clock.advance(handoff.HANDOFF_SECONDS - 1)
    assert got == []
    clock.advance(1)
    assert got == [("d", "stream")]
    assert not handoff.is_live("d")
    assert handoff.take("d") is None


def test_pause_after_untaken_stream_waits_for_it():
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u1")
    assert handoff.offer("d", "pause")
    assert handoff.take("d") == ("stream", "u1")
    assert handoff.take("d") == ("pause", None)
    assert handoff.take("d") is None


def test_resume_after_untaken_stream_adds_nothing():
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u1")
    handoff.offer("d", "pause")
    assert handoff.offer("d", "resume")
    assert handoff.take("d") == ("stream", "u1")
    assert handoff.take("d") is None


def test_pause_behind_stream_is_not_taken_by_command_first():
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u1")
    handoff.offer("d", "pause")
    assert handoff.take("d", "pause") is None
    assert handoff.take("d") == ("stream", "u1")
    assert handoff.take("d", "pause") == ("pause", None)


def test_missed_stream_with_pause_behind_falls_back_as_pause(clock):
    got = []
    handoff.heard_from("d")
    callback = lambda d, c: got.append((d, c))
    handoff.offer("d", "stream", "u", on_missed=callback)
    handoff.offer("d", "pause", on_missed=callback)
    clock.advance(handoff.HANDOFF_SECONDS)
    assert got == [("d", "pause")]
    assert handoff.take("d") is None


def test_pause_wait_starts_when_stream_is_taken(clock):
    got = []
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u", on_missed=lambda d, c: got.append(c))
    handoff.offer("d", "pause", on_missed=lambda d, c: got.append(c))
    clock.advance(handoff.HANDOFF_SECONDS - 1)
    assert handoff.take("d") == ("stream", "u")
    clock.advance(handoff.HANDOFF_SECONDS - 1)     # past the offer's wait, not the take's
    assert handoff.take("d") == ("pause", None)
    assert got == []


def test_closed_falls_back_for_untaken_handoff_only_when_asked():
    got = []
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u", on_missed=lambda d, c: got.append((d, c)))
    handoff.closed("d")
    assert got == []
    assert handoff.take("d") is None
    assert not handoff.is_live("d")
    handoff.heard_from("d")
    handoff.offer("d", "stream", "u", on_missed=lambda d, c: got.append((d, c)))
    handoff.offer("d", "pause", on_missed=lambda d, c: got.append((d, c)))
    handoff.closed("d", fall_back=True)
    assert got == [("d", "pause")]
    assert handoff.take("d") is None


def test_a_claimed_stream_goes_only_to_the_reply():
    handoff.heard_from("d")
    handoff.claim("d")
    assert handoff.offer("d", "stream", "url")
    assert handoff.take("d") is None                         # the old page's refresh
    assert handoff.take("d", "stream", for_reply=True) == ("stream", "url")
    handoff.heard_from("d")
    assert handoff.offer("d", "stream", "url2")
    assert handoff.take("d") == ("stream", "url2")           # claim used up
    handoff.closed("d")


def test_silent_pause_is_taken_once_and_expires():
    handoff.expect_silent_pause("q", now=100)
    assert handoff.takes_silent_pause("q", now=101)
    assert not handoff.takes_silent_pause("q", now=102)
    handoff.expect_silent_pause("q", now=100)
    assert not handoff.takes_silent_pause("q", now=100 + handoff.SILENT_PAUSE_SECONDS + 1)


def test_asking_about_an_echo_makes_no_record_for_it():
    assert not handoff.is_live("d")
    assert handoff.take("d") is None
    assert not handoff.takes_silent_pause("d")
    handoff.closed("d", fall_back=True)
    assert echos.find("d") is None


def test_an_unconfirmed_page_pause_with_a_newer_stream_falls_back_once(clock):
    got = []
    page = echos.open_page("d", "html")
    handoff.heard_from("d")
    handoff.sent(page.page_id, 1, clock.monotonic(), ("pause", None), on_missed=lambda d, c: got.append(c))
    handoff.claim("d")                                       # a voice command's reply takes the stream
    handoff.offer("d", "stream", "u", on_missed=lambda d, c: got.append(c))
    clock.advance(handoff.HANDOFF_SECONDS)
    assert got == ["stream"]
