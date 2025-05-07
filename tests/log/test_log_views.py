import json
import pytest
from flask import url_for
from ad2web.log.models import EventLogEntry
from ad2web.services.log_service import LogService


@pytest.fixture
def seed_events(db_session):
    """Seed 5 events for paging tests."""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    entries = [
        EventLogEntry(type=i, timestamp=now + timedelta(seconds=i), message=f"msg{i}")
        for i in range(5)
    ]
    db_session.add_all(entries)
    db_session.commit()
    return entries


def test_events_page_requires_login(client):
    """GET /log/ should redirect to login when unauthenticated."""
    resp = client.get("/log/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_events_page_accessible(normal_client):
    """Logged-in user can view event history."""
    resp = normal_client.get("/log/")
    assert resp.status_code == 200
    assert b"Event Log" in resp.data  # page_title set in template


@pytest.mark.parametrize("url", ["/log/live", "/log/alarmdecoder"])
def test_admin_only_pages(url, normal_client):
    """Non-admin users get 403 on live and alarmdecoder pages."""
    resp = normal_client.get(url)
    assert resp.status_code == 403


@pytest.mark.parametrize("url,title_snippet", [
    ("/log/live", b"Live Log"),
    ("/log/alarmdecoder", b"AlarmDecoder")
])
def test_admin_only_pages_accessible(admin_client, url, title_snippet):
    """Admin users can access live and alarmdecoder pages."""
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert title_snippet in resp.data


def test_delete_clears_events(admin_client, db_session, seed_events):
    """GET /log/delete removes all EventLogEntry rows and redirects."""
    # Confirm seed present
    assert db_session.query(EventLogEntry).count() == 5
    resp = admin_client.get("/log/delete")
    # Redirects back to /log/
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/log/")
    # All events cleared
    assert db_session.query(EventLogEntry).count() == 0


def test_alarmdecoder_data_json(monkeypatch, admin_client):
    """GET /log/alarmdecoder/get_data/<n> returns JSON list via LogService."""
    sample = ["one", "two", "three"]
    monkeypatch.setattr(LogService, "tail_alarmdecoder_log", staticmethod(lambda n: sample))
    resp = admin_client.get("/log/alarmdecoder/get_data/3")
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json() == sample


def test_retrieve_events_paging_data(admin_client, seed_events):
    """GET /log/retrieve_events_paging_data returns proper DataTables JSON."""
    # Request second page: start=1, length=2, and echo=42
    params = {"iDisplayStart": "1", "iDisplayLength": "2", "sEcho": "42"}
    resp = admin_client.get("/log/retrieve_events_paging_data", query_string=params)
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    # Must include sEcho, record counts, and aaData rows
    assert data["sEcho"] == "42"
    assert data["iTotalRecords"] == 5
    assert data["iTotalDisplayRecords"] == 5
    assert isinstance(data["aaData"], list)
    # Should return exactly 2 rows (entries 1 and 2 of the sorted list)
    assert len(data["aaData"]) == 2
    # Check that the messages correspond to seed_events[1] and seed_events[2]
    returned_msgs = [row[2] for row in data["aaData"]]
    assert returned_msgs == ["msg1", "msg2"]


def test_retrieve_events_paging_data_invalid(monkeypatch, admin_client):
    """If DataTablesServer raises TypeError, we still return an empty JSON structure."""
    # Monkeypatch the DataTablesServer to throw on initialization
    import ad2web.log.views as views
    class Broken:
        def __init__(self, req): raise TypeError("bad input")
    monkeypatch.setattr(views, "DataTablesServer", Broken)
    resp = admin_client.get("/log/retrieve_events_paging_data", query_string={
        "iDisplayStart": "0", "iDisplayLength": "10", "sEcho": "1"
    })
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    # On error, code does `return json.dumps(results)` where results default is {}
    # So data == {}
    assert data == {}
