from skill import bell


def test_status_pattern():
    assert [bell.status_for(t) for t in range(10)] == [200] * 5 + [404, 200, 200, 410, 200]
    assert bell.status_for(15) == 404


def test_rang_measures_the_gap():
    bell._last_request = None
    assert bell.rang(1, now=100.0) == (200, None)
    status, gap = bell.rang(5, now=101.25)
    assert status == 404 and abs(gap - 1.25) < 1e-9


def test_page_counts_only_every_report_interval():
    bell._last_report = 0.0
    args = ["MetadataRefresh", 3, 1000, 7, 2]
    assert bell.page_counts(args, now=100.0)
    assert not bell.page_counts(args, now=105.0)
    assert bell.page_counts(args, now=110.0)


def test_page_counts_ignores_old_pages():
    bell._last_report = 0.0
    assert not bell.page_counts(["MetadataRefresh", 3, 1000], now=100.0)


def test_failed_tolerates_short_or_odd_arguments():
    bell.failed(["BellFail"])
    bell.failed(["BellFail", 404, "Not Found", "${bellTick}"])
    assert bell._expected("15") == 404
