# Tests for the cron jobs: the runner (app/cron_jobs/main.py) and the daily link check of the medical sources
# (app/cron_jobs/source_check/check_links.py). No real database or network: the repository and the HTTP
# transport are replaced by fakes. Run with: pytest

import asyncio
import copy
import ssl
from contextlib import asynccontextmanager
from datetime import date

import httpx
import pytest

from app import config
from app.cron_jobs import main as cron_main
from app.cron_jobs.source_check import check_links

TODAY = date(2026, 11, 18)
YESTERDAY = date(2026, 11, 17)


def source(source_id, link_status=None, status="active"):
    return {
        "id": source_id, "link": f"https://example.org/{source_id}", "status": status,
        "link_status": link_status, "link_checked_at": YESTERDAY if link_status else None,
        "updated_by": "admin-1", "title": "A guideline", "patient_label": "according to X",
    }


class FakeDatabase:
    """ai.medical_sources and ai.audit_log in memory. Changes are kept only when the transaction ends without error."""

    def __init__(self, *sources):
        self.table = {row["id"]: row for row in sources}
        self.audit = []
        self.fail_on = set()  # source ids whose write raises, to test that one error does not stop the run


class FakeSession:
    def __init__(self, db):
        self.db = db
        self.table = copy.deepcopy(db.table)
        self.audit = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @asynccontextmanager
    async def begin(self):
        yield
        self.db.table, self.db.audit = self.table, self.db.audit + self.audit  # commit


class FakeRepository:
    """Same link-check methods as MedicalSourceRepository, on the fake session."""

    def __init__(self, session):
        self.session = session

    async def list_active_links(self):
        rows = sorted(self.session.table.values(), key=lambda row: row["id"])
        return [{"id": row["id"], "link": row["link"]} for row in rows if row["status"] == "active"]

    async def get_link_state(self, source_id):
        row = self.session.table.get(source_id)
        if row is None:
            return None
        return {key: row[key] for key in ("id", "status", "link_status", "link_checked_at", "updated_by")}

    async def record_link_check(self, source_id, link_status, checked_on, set_broken, updated_by):
        if source_id in self.session.db.fail_on:
            raise RuntimeError("database refused the write")
        row = self.session.table[source_id]
        if row["status"] != "active":
            return None
        row.update(link_status=link_status, link_checked_at=checked_on, updated_by=updated_by)
        if set_broken:
            row["status"] = "broken"
        return {key: row[key] for key in ("id", "status", "link_status", "link_checked_at", "updated_by")}

    async def add_audit(self, row):
        self.session.audit.append(row)


@pytest.fixture
def db(monkeypatch):
    database = FakeDatabase()

    @asynccontextmanager
    async def fake_open_system_session():
        yield lambda: FakeSession(database)

    monkeypatch.setattr(check_links, "open_system_session", fake_open_system_session)
    monkeypatch.setattr(check_links, "MedicalSourceRepository", FakeRepository)
    return database


@pytest.fixture
def web(monkeypatch):
    """links[url] = a status code, or an exception to raise. Every request is recorded."""

    class Web:
        links = {}
        requests = []

    def handler(request):
        Web.requests.append(request)
        answer = Web.links.get(str(request.url), 200)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, str):  # a redirect to this URL
            return httpx.Response(301, headers={"Location": answer})
        return httpx.Response(answer, text="<html>page text that must never be used</html>")

    monkeypatch.setattr(
        check_links, "make_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True,
            headers={"User-Agent": check_links.USER_AGENT},
        ),
    )
    return Web


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(check_links, "send_alert_email", lambda broken: sent.append(broken))
    monkeypatch.setattr(config, "LINK_ALERT_EMAILS", ())
    monkeypatch.setattr(config, "LINK_ALERT_FROM", "")
    return sent


def run_check():
    return asyncio.run(check_links.run(today=TODAY))


def link(source_id):
    return f"https://example.org/{source_id}"


# ---------- classifying the HTTP result ----------

@pytest.mark.parametrize("code, outcome", [
    (200, "ok"), (204, "ok"), (304, "ok"), (399, "ok"),
    (404, "broken"), (410, "broken"), (500, "broken"), (502, "broken"), (503, "broken"),
    (401, "blocked"), (403, "blocked"), (429, "blocked"),
])
def test_outcome_for_status(code, outcome):
    assert check_links.outcome_for_status(code) == outcome


@pytest.mark.parametrize("error, outcome, detail", [
    (httpx.ConnectTimeout("timed out"), "broken", "timeout after 15 s"),
    (httpx.ReadTimeout("timed out"), "broken", "timeout after 15 s"),
    (httpx.ConnectError("[Errno -2] Name or service not known"), "broken", "connection error"),
    (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "broken", "SSL error"),
])
def test_network_errors_are_broken(db, web, emails, error, outcome, detail):
    db.table["US-A"] = source("US-A")
    web.links = {link("US-A"): error}

    run_check()

    assert db.table["US-A"]["link_status"] == outcome
    assert detail in db.audit[0].reason


def test_ssl_and_dns_causes_are_named():
    ssl_error = httpx.ConnectError("failed")
    ssl_error.__cause__ = ssl.SSLCertVerificationError("certificate has expired")
    assert check_links.describe_error(ssl_error).startswith("SSL error")

    import socket
    dns_error = httpx.ConnectError("failed")
    dns_error.__cause__ = socket.gaierror(-2, "Name or service not known")
    assert check_links.describe_error(dns_error) == "DNS error: host not found"


def test_request_uses_a_browser_user_agent_and_follows_redirects(db, web, emails):
    db.table["US-A"] = source("US-A")
    web.links = {link("US-A"): "https://example.org/moved", "https://example.org/moved": 200}

    summary = run_check()

    assert summary["ok"] == 1
    assert [str(request.url) for request in web.requests] == [link("US-A"), "https://example.org/moved"]
    assert web.requests[0].headers["User-Agent"].startswith("Mozilla/5.0")
    assert web.requests[0].method == "GET"


def test_real_client_settings():
    client = check_links.make_http_client()
    assert client.follow_redirects is True
    assert client.timeout.read == 15
    assert client.headers["User-Agent"] == check_links.USER_AGENT


def test_at_most_three_links_at_a_time():
    running = {"now": 0, "most": 0}

    async def slow_handler(request):
        running["now"] += 1
        running["most"] = max(running["most"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return httpx.Response(200)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as client:
            return await check_links.check_links(client, [{"id": f"US-{n}", "link": link(f"US-{n}")} for n in range(10)])

    results = asyncio.run(go())

    assert [result.source_id for result in results] == [f"US-{n}" for n in range(10)]  # order kept
    assert running["most"] == check_links.MAX_PARALLEL == 3


# ---------- what is written ----------

def test_ok_link_first_check_is_recorded_with_an_audit_row(db, web, emails):
    db.table["US-A"] = source("US-A")

    summary = run_check()

    row = db.table["US-A"]
    assert (row["status"], row["link_status"], row["link_checked_at"], row["updated_by"]) == (
        "active", "ok", TODAY, "system:source_check",
    )
    (audit,) = db.audit
    assert (audit.actor_id, audit.actor_role, audit.action, audit.resource_type, audit.resource_id) == (
        "system:source_check", "system", "update", "medical_source", "US-A",
    )
    assert audit.before == {"link_status": None, "link_checked_at": None, "updated_by": "admin-1"}
    assert audit.after == {"link_status": "ok", "link_checked_at": "2026-11-18", "updated_by": "system:source_check"}
    assert summary == {"checked": 1, "ok": 1, "failed_once": 0, "set_broken": 0, "blocked": 0}


def test_ok_link_still_ok_updates_the_date_but_writes_no_audit_row(db, web, emails):
    db.table["US-A"] = source("US-A", link_status="ok")

    run_check()

    assert db.table["US-A"]["link_checked_at"] == TODAY
    assert db.audit == []


def test_first_failure_marks_the_link_broken_but_keeps_the_source_active(db, web, emails):
    db.table["US-A"] = source("US-A", link_status="ok")
    web.links = {link("US-A"): 404}

    summary = run_check()

    assert (db.table["US-A"]["status"], db.table["US-A"]["link_status"]) == ("active", "broken")
    (audit,) = db.audit
    assert audit.reason == "Link check: 404 Not Found (1st failure)"
    assert audit.before == {"link_status": "ok", "link_checked_at": "2026-11-17", "updated_by": "admin-1"}
    assert "status" not in audit.after  # only the fields that changed
    assert summary["failed_once"] == 1 and summary["set_broken"] == 0
    assert emails == []


def test_second_failure_in_a_row_sets_the_source_to_broken(db, web, emails, caplog):
    db.table["US-A"] = source("US-A", link_status="broken")
    web.links = {link("US-A"): 410}

    summary = run_check()

    assert (db.table["US-A"]["status"], db.table["US-A"]["link_status"]) == ("broken", "broken")
    (audit,) = db.audit
    assert audit.reason == "Link check failed twice: source set to broken (410 Gone)"
    assert audit.before["status"] == "active" and audit.after["status"] == "broken"
    assert "link_status" not in audit.after  # was already broken
    assert summary["set_broken"] == 1
    warning = [record for record in caplog.records if record.levelname == "WARNING" and "set to broken" in record.message]
    assert warning and "US-A" in warning[0].message and link("US-A") in warning[0].message


def test_broken_link_that_works_again_is_ok_but_never_reactivates(db, web, emails):
    db.table["US-A"] = source("US-A", link_status="broken")

    run_check()

    assert (db.table["US-A"]["status"], db.table["US-A"]["link_status"]) == ("active", "ok")
    assert db.audit[0].reason == "Link check: link works again (200 OK)"


def test_only_active_sources_are_checked(db, web, emails):
    db.table["US-A"] = source("US-A")
    db.table["US-B"] = source("US-B", link_status="broken", status="broken")
    db.table["US-C"] = source("US-C", status="draft")
    db.table["US-D"] = source("US-D", status="inactive")

    summary = run_check()

    assert [str(request.url) for request in web.requests] == [link("US-A")]
    assert summary["checked"] == 1
    assert db.table["US-B"]["status"] == "broken"  # never set back to active
    assert db.table["US-C"]["link_status"] is None and db.table["US-D"]["link_status"] is None


@pytest.mark.parametrize("code", [401, 403, 429])
def test_blocked_link_leaves_the_source_unchanged(db, web, emails, code):
    db.table["US-A"] = source("US-A", link_status="broken")
    before = copy.deepcopy(db.table["US-A"])
    web.links = {link("US-A"): code}

    summary = run_check()

    assert db.table["US-A"] == before
    assert db.audit == []
    assert summary["blocked"] == 1 and summary["checked"] == 1


def test_source_changed_by_an_admin_during_the_check_is_left_alone(db, web, emails, monkeypatch):
    db.table["US-A"] = source("US-A", link_status="broken")
    web.links = {link("US-A"): 404}
    real_check_links = check_links.check_links

    async def admin_deactivates_meanwhile(client, sources):
        results = await real_check_links(client, sources)
        db.table["US-A"]["status"] = "inactive"
        return results

    monkeypatch.setattr(check_links, "check_links", admin_deactivates_meanwhile)

    run_check()

    assert db.table["US-A"]["status"] == "inactive"
    assert db.audit == []


def test_only_the_check_fields_change(db, web, emails):
    db.table["US-A"] = source("US-A", link_status="broken")
    before = copy.deepcopy(db.table["US-A"])
    web.links = {link("US-A"): 500}

    run_check()

    changed = {key for key in before if before[key] != db.table["US-A"][key]}
    assert changed <= {"status", "link_status", "link_checked_at", "updated_by"}
    assert "page text" not in repr(db.table) + repr(db.audit)  # nothing from the page is stored


def test_one_failed_write_does_not_stop_the_others_and_fails_the_job(db, web, emails):
    db.table["US-A"] = source("US-A")
    db.table["US-B"] = source("US-B")
    db.fail_on = {"US-A"}

    with pytest.raises(check_links.LinkCheckFailed, match="US-A"):
        run_check()

    assert db.table["US-A"]["link_status"] is None  # rolled back
    assert db.table["US-B"]["link_status"] == "ok"
    assert [row.resource_id for row in db.audit] == ["US-B"]


def test_cache_is_cleared_after_the_run(db, web, emails, monkeypatch):
    cleared = []
    monkeypatch.setattr(check_links.library, "clear_cache", lambda: cleared.append(True))

    run_check()

    assert cleared == [True]


def test_summary_is_printed(db, web, emails, capsys):
    db.table["US-A"] = source("US-A")
    db.table["US-B"] = source("US-B", link_status="ok")
    db.table["US-C"] = source("US-C", link_status="broken")
    db.table["US-D"] = source("US-D")
    web.links = {link("US-B"): 404, link("US-C"): 503, link("US-D"): 403}

    summary = run_check()

    assert summary == {"checked": 4, "ok": 1, "failed_once": 1, "set_broken": 1, "blocked": 1}
    assert "4 checked, 1 ok, 1 failed once, 1 set to broken, 1 blocked" in capsys.readouterr().out


# ---------- the alert email ----------

def test_no_email_when_no_address_is_set(db, web, emails):
    db.table["US-A"] = source("US-A", link_status="broken")
    web.links = {link("US-A"): 404}

    run_check()

    assert emails == []


def test_one_email_per_run_listing_every_source_set_to_broken(db, web, emails, monkeypatch):
    monkeypatch.setattr(config, "LINK_ALERT_EMAILS", ("ops@example.org", "doc@example.org"))
    monkeypatch.setattr(config, "LINK_ALERT_FROM", "alerts@example.org")
    db.table["US-A"] = source("US-A", link_status="broken")
    db.table["US-B"] = source("US-B", link_status="broken")
    db.table["US-C"] = source("US-C", link_status="ok")
    web.links = {link("US-A"): 404, link("US-B"): httpx.ConnectTimeout("t"), link("US-C"): 404}

    run_check()

    (broken,) = emails
    assert [result.source_id for result in broken] == ["US-A", "US-B"]
    text = check_links.alert_text(broken)
    assert link("US-A") in text and "404 Not Found" in text and "timeout" in text
    assert "status=broken" in text


def test_no_email_when_nothing_was_set_to_broken(db, web, emails, monkeypatch):
    monkeypatch.setattr(config, "LINK_ALERT_EMAILS", ("ops@example.org",))
    monkeypatch.setattr(config, "LINK_ALERT_FROM", "alerts@example.org")
    db.table["US-A"] = source("US-A")
    web.links = {link("US-A"): 404}

    run_check()

    assert emails == []


def test_failed_email_fails_the_job_after_saving(db, web, emails, monkeypatch):
    monkeypatch.setattr(config, "LINK_ALERT_EMAILS", ("ops@example.org",))
    monkeypatch.setattr(config, "LINK_ALERT_FROM", "alerts@example.org")

    def ses_down(broken):
        raise RuntimeError("SES unavailable")

    monkeypatch.setattr(check_links, "send_alert_email", ses_down)
    db.table["US-A"] = source("US-A", link_status="broken")
    web.links = {link("US-A"): 404}

    with pytest.raises(check_links.LinkCheckFailed, match="SES unavailable"):
        run_check()

    assert db.table["US-A"]["status"] == "broken"  # saved before the email


def test_ses_email_shape(monkeypatch):
    calls = []

    class FakeSes:
        def send_email(self, **kwargs):
            calls.append(kwargs)

    import boto3
    monkeypatch.setattr(boto3, "client", lambda service, region_name: FakeSes() if service == "ses" else None)
    monkeypatch.setattr(config, "LINK_ALERT_EMAILS", ("ops@example.org",))
    monkeypatch.setattr(config, "LINK_ALERT_FROM", "alerts@example.org")

    check_links.send_alert_email([check_links.LinkResult("US-A", link("US-A"), "broken", "404 Not Found")])

    (call,) = calls
    assert call["Source"] == "alerts@example.org"
    assert call["Destination"] == {"ToAddresses": ["ops@example.org"]}
    assert "US-A" in call["Message"]["Body"]["Text"]["Data"]


# ---------- the runner ----------

def test_registry_has_the_source_check():
    assert cron_main.JOBS["source_check"] is check_links.run


def test_runner_runs_every_job_and_one_failure_does_not_stop_the_others(monkeypatch):
    ran = []

    def failing():
        ran.append("failing")
        raise RuntimeError("boom")

    async def working():
        ran.append("working")
        return {"done": 1}

    monkeypatch.setattr(cron_main, "JOBS", {"failing": failing, "working": working})

    assert cron_main.main([]) == 1
    assert ran == ["failing", "working"]


def test_runner_exit_code_0_when_every_job_ran(monkeypatch):
    monkeypatch.setattr(cron_main, "JOBS", {"a": lambda: {"x": 1}, "b": lambda: {"y": 2}})
    assert cron_main.main([]) == 0


def test_runner_runs_one_job(monkeypatch):
    ran = []
    monkeypatch.setattr(cron_main, "JOBS", {"a": lambda: ran.append("a"), "b": lambda: ran.append("b")})

    assert cron_main.main(["--job", "b"]) == 0
    assert ran == ["b"]


def test_runner_refuses_an_unknown_job():
    with pytest.raises(SystemExit):
        cron_main.main(["--job", "nope"])
