# Tests for the admin API of the trusted medical source library (app/medical_sources/controller.py, service.py).
# No real database: open_repository is replaced by an in-memory fake with the same methods as
# MedicalSourceRepository. The SQL itself was checked against Postgres (see README, "Trusted medical sources").
# Run with: pytest

import copy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.medical_sources import library, service
from app.medical_sources.repository import COLUMNS, SEARCHABLE
from main import app
from python_auth.roles import UserType
from python_auth.test_auth import USER_ID, make_token
from tests.test_assess import auth_header

URL = "/ai/admin/medical-sources"
ADMIN = auth_header(make_token(user_type=UserType.Admin))

NEW_SOURCE = {
    "id": "US-AHA-HF-2022",
    "country": "US",
    "language": "en",
    "organisation": "American Heart Association / American College of Cardiology / Heart Failure Society of America",
    "title": "2022 AHA/ACC/HFSA Guideline for the Management of Heart Failure",
    "year": 2022,
    "publication": "Circulation. 2022;145(18):e895-e1032",
    "doi": "10.1161/CIR.0000000000001063",
    "link": "https://doi.org/10.1161/CIR.0000000000001063",
    "topics": ["heart_failure"],
    "motors": ["triage", "vitals"],
    "patient_label": "according to American Heart Association guidelines",
}

APPROVAL = {"approved_by": "Jose (Vital Signs LLC)", "approval_note": "Email 20 Nov 2026: approved", "approved_at": "2026-11-20"}


class FakeRepository:
    """Same methods as MedicalSourceRepository, on a dict. Changes are kept only when the block ends without error,
    like the real transaction."""

    def __init__(self, table, audit):
        self.table = table
        self.audit = audit

    async def list(self, country=None, status=None, motor=None, topic=None, search=None,
                   sort="created_at", order="desc", limit=None, offset=0):
        def found(row):
            if search is None:
                return True
            needle = search.lower()
            texts = [row[column] for column in SEARCHABLE if row.get(column)] + list(row["topics"])
            return any(needle in str(value).lower() for value in texts)

        rows = [
            row for row in self.table.values()
            if (country is None or row["country"] == country) and (status is None or row["status"] == status)
            and (motor is None or motor in row["motors"]) and (topic is None or topic in row["topics"])
            and found(row)
        ]
        rows.sort(key=lambda row: row["id"], reverse=order == "desc")  # the id tie-break of the real ORDER BY
        rows.sort(key=lambda row: (row[sort] is None, row[sort]) if order == "asc" else (row[sort] is not None, row[sort]),
                  reverse=order == "desc")
        page = rows[offset:offset + limit] if limit else rows[offset:]
        return [copy.deepcopy(row) for row in page], len(rows)

    async def get(self, source_id, for_update=False):
        row = self.table.get(source_id)
        return copy.deepcopy(row) if row else None

    async def insert(self, values):
        now = datetime.now(timezone.utc)
        row = {column: None for column in COLUMNS}
        row.update(evidence_level="clinical_guideline", needs_client_confirmation=False, created_at=now, updated_at=now)
        row.update(values)
        self.table[row["id"]] = row
        return copy.deepcopy(row)

    async def update(self, source_id, changes):
        self.table[source_id].update(changes, updated_at=datetime.now(timezone.utc))
        return copy.deepcopy(self.table[source_id])

    async def add_audit(self, row):
        self.audit.append(row)


@pytest.fixture
def db(monkeypatch):
    """The fake ai.medical_sources (seeded with the 18 sources of the seed file) and the audit rows written."""

    class Db:
        table = {}
        audit = []

    loaded = datetime(2026, 11, 18, tzinfo=timezone.utc)
    for number, source in enumerate(library.read_from_file()):
        row = {column: None for column in COLUMNS}
        row.update({key: value for key, value in source.items() if key in COLUMNS}, created_by="seed",
                   updated_by="seed", created_by_name="System (seed)", updated_by_name="System (seed)")
        row["created_at"] = row["updated_at"] = loaded + timedelta(seconds=number)  # file order = load order
        Db.table[row["id"]] = row
    Db.audit = []

    @asynccontextmanager
    async def fake_open_repository():
        work = copy.deepcopy(Db.table)
        audit = []
        yield FakeRepository(work, audit)
        Db.table.clear()
        Db.table.update(work)  # committed only when no error was raised
        Db.audit.extend(audit)

    monkeypatch.setattr(service, "open_repository", fake_open_repository)
    return Db


@pytest.fixture
def client():
    return TestClient(app)


# ---------- who may call it ----------

@pytest.mark.parametrize("method, path", [
    ("get", URL), ("get", f"{URL}/topics"), ("get", f"{URL}/US-CDC-STROKE"), ("post", URL),
    ("patch", f"{URL}/US-CDC-STROKE"), ("post", f"{URL}/US-CDC-STROKE/approve"),
    ("post", f"{URL}/US-CDC-STROKE/deactivate"),
])
@pytest.mark.parametrize("user_type", [UserType.User, UserType.Doctor, UserType.Supervisor, UserType.Labeler,
                                       UserType.Developer, UserType.Guest])
def test_only_admin_may_call_it(client, db, method, path, user_type):
    headers = auth_header(make_token(user_type=user_type))
    response = getattr(client, method)(path, headers=headers, **({"json": {}} if method != "get" else {}))
    assert response.status_code == 403


def test_no_token_is_401(client, db):
    assert client.get(URL).status_code == 401


def test_admin_route_preflight_allows_bearer_auth(client):
    response = client.options(
        f"{URL}/topics",
        headers={
            "Origin": "http://localhost:3030",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3030"  # echoed, so credentials work
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


# ---------- reads ----------

def test_list_and_filters(client, db):
    data = client.get(URL, headers=ADMIN).json()
    assert data["count"] == 18

    mexico_visual = client.get(URL, params={"country": "MX", "motor": "visual"}, headers=ADMIN).json()
    assert {source["id"] for source in mexico_visual["sources"]} == {
        "MX-CENETEC-UPP-2015", "MX-OPS-HEARTSD-2020", "MX-OPS-BEC",
    }
    assert client.get(URL, params={"status": "active"}, headers=ADMIN).json()["count"] == 0


def test_list_is_newest_first(client, db):
    # Bug: a new source came last (ORDER BY country, id). Now the newest is on top.
    assert client.post(URL, json=NEW_SOURCE, headers=ADMIN).status_code == 201
    ids = [source["id"] for source in client.get(URL, headers=ADMIN).json()["sources"]]
    assert ids[0] == "US-AHA-HF-2022"
    oldest = client.get(URL, params={"order": "asc"}, headers=ADMIN).json()["sources"]
    assert oldest[0]["id"] == ids[-1]
    by_title = [s["title"] for s in client.get(URL, params={"sort": "title", "order": "asc"}, headers=ADMIN).json()["sources"]]
    assert by_title == sorted(by_title)
    assert client.get(URL, params={"sort": "drop table"}, headers=ADMIN).status_code == 422


def test_list_search(client, db):
    # Bug: there was no search at all.
    stroke = client.get(URL, params={"search": "stroke"}, headers=ADMIN).json()
    assert stroke["count"] >= 1
    assert all("stroke" in str(source).lower() for source in stroke["sources"])
    assert "US-CDC-STROKE" in {source["id"] for source in stroke["sources"]}
    assert client.get(URL, params={"search": "STROKE"}, headers=ADMIN).json()["count"] == stroke["count"]
    assert client.get(URL, params={"search": "no-such-source-xyz"}, headers=ADMIN).json() == {
        "count": 0, "sources": [], "page": None, "limit": None, "total_pages": None,
    }
    # Search works together with the filters.
    us_only = client.get(URL, params={"search": "stroke", "country": "MX"}, headers=ADMIN).json()
    assert all(source["country"] == "MX" for source in us_only["sources"])


def test_list_pages(client, db):
    first = client.get(URL, params={"page": 1, "limit": 5}, headers=ADMIN).json()
    second = client.get(URL, params={"page": 2, "limit": 5}, headers=ADMIN).json()
    assert (first["count"], first["limit"], first["total_pages"], len(first["sources"])) == (18, 5, 4, 5)
    assert not {s["id"] for s in first["sources"]} & {s["id"] for s in second["sources"]}


def test_detail_shows_names_not_ids(client, db):
    # Bug: "Updated by" showed a MongoDB id on GET /ai/admin/medical-sources/{id}.
    seed = client.get(f"{URL}/US-CDC-STROKE", headers=ADMIN).json()
    assert (seed["created_by"], seed["updated_by"], seed["updated_by_id"]) == ("System (seed)", "System (seed)", "seed")
    admin = auth_header(make_token(user_type=UserType.Admin, name="Jane Admin"))
    assert client.patch(f"{URL}/US-CDC-STROKE", json={"notes": "x"}, headers=admin).status_code == 200
    changed = client.get(f"{URL}/US-CDC-STROKE", headers=ADMIN).json()
    assert (changed["updated_by"], changed["updated_by_id"], changed["created_by"]) == ("Jane Admin", USER_ID, "System (seed)")
    # An old row with only an id and no name still shows something (the id).
    db.table["US-CDC-STROKE"].update(updated_by="65f0c0ffee0000000000abcd", updated_by_name=None)
    assert client.get(f"{URL}/US-CDC-STROKE", headers=ADMIN).json()["updated_by"] == "65f0c0ffee0000000000abcd"


def test_list_refuses_an_unknown_filter_value(client, db):
    assert client.get(URL, params={"country": "CA"}, headers=ADMIN).status_code == 422


def test_get_one_and_404(client, db):
    assert client.get(f"{URL}/US-CDC-STROKE", headers=ADMIN).json()["title"] == "Signs and Symptoms of Stroke"
    assert client.get(f"{URL}/US-NOPE", headers=ADMIN).status_code == 404


def test_topics(client, db):
    topics = client.get(f"{URL}/topics", headers=ADMIN).json()["topics"]
    assert topics["heart_failure"] == "Heart failure"


# ---------- create ----------

def test_create_saves_a_draft_and_audits(client, db):
    response = client.post(URL, json={**NEW_SOURCE, "reason": "US heart failure gap"}, headers=ADMIN)

    assert response.status_code == 201
    source = response.json()
    # A person's name in created_by / updated_by, the MongoDB id only in *_by_id (bug: the id was shown).
    assert (source["status"], source["created_by"], source["updated_by"]) == ("draft", "Test Patient", "Test Patient")
    assert (source["created_by_id"], source["updated_by_id"]) == (USER_ID, USER_ID)
    assert db.table["US-AHA-HF-2022"]["status"] == "draft"  # the AI cannot cite it yet
    (audit,) = db.audit
    assert (audit.action, audit.actor_role, audit.resource_type, audit.resource_id, audit.reason) == (
        "create", "admin", "medical_source", "US-AHA-HF-2022", "US heart failure gap",
    )


def test_create_cannot_set_the_status(client, db):
    response = client.post(URL, json={**NEW_SOURCE, "status": "active"}, headers=ADMIN)
    assert response.status_code == 422  # unknown field: only /approve makes a source active


def test_create_refuses_an_existing_id(client, db):
    response = client.post(URL, json={**NEW_SOURCE, "id": "US-CDC-STROKE"}, headers=ADMIN)
    assert response.status_code == 409
    assert db.audit == []


@pytest.mark.parametrize("change", [
    {"id": "MX-AHA-HF-2022"},  # id does not start with the country
    {"id": "us-aha-hf"},  # not capital letters
    {"link": "http://doi.org/10.1161/CIR.0000000000001063"},  # not https
    {"link": "doi.org/10.1161"},
    {"topics": ["heart_failure", "kidneys"]},  # unknown topic
    {"topics": []},
    {"motors": ["chat"]},
    {"motors": []},
    {"country": "CA"},
    {"language": "fr"},
    {"evidence_level": "blog_post"},
    {"patient_label": ""},
    {"year": 1800},
])
def test_create_refuses_bad_values(client, db, change):
    response = client.post(URL, json={**NEW_SOURCE, **change}, headers=ADMIN)
    assert response.status_code == 422
    assert "US-AHA-HF-2022" not in db.table


def test_create_requires_the_main_fields(client, db):
    for field in ["id", "country", "language", "organisation", "title", "link", "topics", "motors", "patient_label"]:
        body = {key: value for key, value in NEW_SOURCE.items() if key != field}
        assert client.post(URL, json=body, headers=ADMIN).status_code == 422, field


# ---------- update ----------

def test_update_a_draft(client, db):
    response = client.patch(
        f"{URL}/US-CDC-STROKE", json={"notes": "Checked again", "topics": ["stroke_symptoms"]}, headers=ADMIN,
    )

    assert response.status_code == 200
    assert response.json()["topics"] == ["stroke_symptoms"]
    (audit,) = db.audit
    assert audit.action == "update"
    assert set(audit.before) == set(audit.after) == {"notes", "topics", "updated_by", "updated_by_name"}  # only what changed
    assert audit.before["topics"] == ["stroke_symptoms", "altered_mental_status"]
    assert (audit.after["topics"], audit.after["notes"], audit.after["updated_by"]) == (["stroke_symptoms"], "Checked again", USER_ID)


def test_update_cannot_change_id_country_or_status(client, db):
    for body in ({"id": "US-X"}, {"country": "MX"}, {"status": "active"}):
        assert client.patch(f"{URL}/US-CDC-STROKE", json=body, headers=ADMIN).status_code == 422


def test_update_needs_a_field(client, db):
    assert client.patch(f"{URL}/US-CDC-STROKE", json={"reason": "nothing"}, headers=ADMIN).status_code == 400


def test_update_cannot_empty_a_required_field(client, db):
    assert client.patch(f"{URL}/US-CDC-STROKE", json={"link": None}, headers=ADMIN).status_code == 422


def test_update_404(client, db):
    assert client.patch(f"{URL}/US-NOPE", json={"notes": "x"}, headers=ADMIN).status_code == 404


def test_changing_an_active_source_needs_the_clients_approval(client, db):
    client.post(f"{URL}/US-CDC-STROKE/approve", json=APPROVAL, headers=ADMIN)
    new_link = "https://www.cdc.gov/stroke/signs-symptoms/index.html"

    refused = client.patch(f"{URL}/US-CDC-STROKE", json={"link": new_link}, headers=ADMIN)
    assert refused.status_code == 409
    assert db.table["US-CDC-STROKE"]["link"] != new_link

    notes_only = client.patch(f"{URL}/US-CDC-STROKE", json={"notes": "internal note"}, headers=ADMIN)
    assert notes_only.status_code == 200  # notes are not shown to the AI or the patient

    approval = {**APPROVAL, "approval_note": "Email 21 Nov 2026: new CDC link approved", "approved_at": "2026-11-21"}
    accepted = client.patch(f"{URL}/US-CDC-STROKE", json={"link": new_link, "approval": approval}, headers=ADMIN)
    assert accepted.status_code == 200
    assert (accepted.json()["link"], accepted.json()["approval_note"], accepted.json()["status"]) == (
        new_link, "Email 21 Nov 2026: new CDC link approved", "active",
    )


# ---------- approve and deactivate ----------

def test_approve_makes_it_active_with_the_approval(client, db):
    response = client.post(f"{URL}/MX-SMC-IC-2024/approve", json=APPROVAL, headers=ADMIN)

    assert response.status_code == 200
    source = response.json()
    assert (source["status"], source["approved_by"], source["approval_note"]) == (
        "active", "Jose (Vital Signs LLC)", "Email 20 Nov 2026: approved",
    )
    assert source["approved_at"].startswith("2026-11-20")
    assert source["needs_client_confirmation"] is False
    (audit,) = db.audit
    assert audit.reason.startswith("Approved by Jose (Vital Signs LLC)")
    assert (audit.before["status"], audit.after["status"]) == ("draft", "active")


@pytest.mark.parametrize("body", [
    {},
    {"approved_by": "Jose"},  # no message
    {"approval_note": "ok please"},  # no name
    {**APPROVAL, "approval_note": "ok"},  # too short to be the written approval
])
def test_approve_needs_the_written_approval(client, db, body):
    assert client.post(f"{URL}/MX-SMC-IC-2024/approve", json=body, headers=ADMIN).status_code == 422
    assert db.table["MX-SMC-IC-2024"]["status"] == "draft"


def test_approve_twice_is_409(client, db):
    client.post(f"{URL}/MX-SMC-IC-2024/approve", json=APPROVAL, headers=ADMIN)
    assert client.post(f"{URL}/MX-SMC-IC-2024/approve", json=APPROVAL, headers=ADMIN).status_code == 409


def test_deactivate_never_deletes(client, db):
    client.post(f"{URL}/US-CDC-SEPSIS/approve", json=APPROVAL, headers=ADMIN)

    response = client.post(f"{URL}/US-CDC-SEPSIS/deactivate", json={"reason": "Page moved"}, headers=ADMIN)

    assert response.status_code == 200
    assert db.table["US-CDC-SEPSIS"]["status"] == "inactive"  # still there, so past results keep their source
    assert db.audit[-1].reason == "Page moved"
    assert client.post(f"{URL}/US-CDC-SEPSIS/deactivate", json={"reason": "again"}, headers=ADMIN).status_code == 409


def test_deactivate_needs_a_reason(client, db):
    assert client.post(f"{URL}/US-CDC-SEPSIS/deactivate", json={}, headers=ADMIN).status_code == 422


def test_there_is_no_delete(client, db):
    assert client.delete(f"{URL}/US-CDC-SEPSIS", headers=ADMIN).status_code == 405


def test_a_change_clears_the_motor_cache(client, db, monkeypatch):
    cleared = []
    monkeypatch.setattr(library, "clear_cache", lambda: cleared.append(1))
    client.post(f"{URL}/US-CDC-SEPSIS/approve", json=APPROVAL, headers=ADMIN)
    assert cleared == [1]


def test_refused_change_writes_nothing(client, db):
    client.post(f"{URL}/US-CDC-STROKE/approve", json=APPROVAL, headers=ADMIN)
    audit_rows = len(db.audit)
    client.patch(f"{URL}/US-CDC-STROKE", json={"title": "Other"}, headers=ADMIN)  # 409: active, no approval
    assert len(db.audit) == audit_rows
    assert db.table["US-CDC-STROKE"]["title"] == "Signs and Symptoms of Stroke"
