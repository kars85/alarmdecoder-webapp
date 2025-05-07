import os
import pytest
from datetime import datetime, timedelta

import ad2web.services.log_service as ls_module
from ad2web.services.log_service import LogService
from ad2web.log.models import EventLogEntry
from ad2web.log.constants import EVENT_TYPES
from ad2web.logwatch import LogWatcher


@pytest.fixture(autouse=True)
def no_real_logs(tmp_path, monkeypatch):
    """Redirect the INSTANCE_FOLDER_PATH/logs/info.log to a temp file and patch LogWatcher.tail."""
    # Create a fake log file under INSTANCE_FOLDER_PATH/logs/info.log
    temp_dir = tmp_path / "instance" / "logs"
    temp_dir.mkdir(parents=True)
    fake_log = temp_dir / "info.log"
    fake_log.write_text("line1\nline2\nline3\n")
    monkeypatch.setattr(ls_module, "INSTANCE_FOLDER_PATH", str(tmp_path / "instance"))
    yield


def test_tail_alarmdecoder_log_negative():
    """Requesting <=0 lines returns empty list."""
    assert LogService.tail_alarmdecoder_log(0) == []
    assert LogService.tail_alarmdecoder_log(-5) == []


def test_tail_alarmdecoder_log_success(monkeypatch):
    """LogWatcher.tail returns mixed bytes/str and is normalized to str."""
    sample = [b"bytes1", "str2"]
    monkeypatch.setattr(LogWatcher, "tail", staticmethod(lambda path, n: sample))
    lines = LogService.tail_alarmdecoder_log(2)
    assert lines == ["bytes1", "str2"]


def test_tail_alarmdecoder_log_oserror(monkeypatch):
    """If reading log raises OSError, we return the exception message."""
    def bad_tail(path, n):
        raise OSError("fail-read")
    monkeypatch.setattr(LogWatcher, "tail", staticmethod(bad_tail))
    result = LogService.tail_alarmdecoder_log(3)
    assert result == ["fail-read"]


def test_get_event_log_page_basic(db_session):
    """Basic pagination, ordering, and counts work as expected."""
    # Create three entries with distinct timestamps
    now = datetime.utcnow()
    e1 = EventLogEntry(type=0, timestamp=now, message="First")
    e2 = EventLogEntry(type=1, timestamp=now + timedelta(seconds=1), message="Second")
    e3 = EventLogEntry(type=2, timestamp=now + timedelta(seconds=2), message="Third")
    db_session.add_all([e1, e2, e3])
    db_session.commit()

    # Page size 2 => only two most recent entries
    results, total, filtered = LogService.get_event_log_page(start=0, length=2)
    assert total == 3
    assert filtered == 3
    assert len(results) == 2

    # Verify ordering: newest first
    timestamps = [datetime.fromisoformat(r[0]) for r in results]
    assert timestamps[0] > timestamps[1]
    # Verify type strings map correctly
    assert results[0][1] == EVENT_TYPES[e3.type]
    assert results[1][1] == EVENT_TYPES[e2.type]


def test_get_event_log_page_paging_and_defaults(db_session):
    """Negative start resets to 0; invalid length resets to default 50."""
    now = datetime.utcnow()
    # Insert one entry
    entry = EventLogEntry(type=0, timestamp=now, message="X")
    db_session.add(entry)
    db_session.commit()

    # Negative start
    res1, t1, f1 = LogService.get_event_log_page(start=-10, length=1)
    assert t1 == 1 and f1 == 1 and len(res1) == 1

    # Zero length => default length 50, so still returns one entry
    res2, t2, f2 = LogService.get_event_log_page(start=0, length=0)
    assert t2 == 1 and f2 == 1 and len(res2) == 1


def test_get_event_log_page_search_filter(db_session):
    """Search filters messages case-insensitively."""
    now = datetime.utcnow()
    a = EventLogEntry(type=0, timestamp=now, message="Apple pie")
    b = EventLogEntry(type=1, timestamp=now + timedelta(seconds=1), message="Banana split")
    db_session.add_all([a, b])
    db_session.commit()

    # Search for "apple" (lowercase) should match "Apple pie"
    res, total, filtered = LogService.get_event_log_page(start=0, length=10, search="apple")
    assert total == 2
    assert filtered == 1
    assert res[0][2] == "Apple pie"
