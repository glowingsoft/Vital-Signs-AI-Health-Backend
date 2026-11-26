# Tests for what the motors use at run time (app/medical_sources/library.py) and the first load
# (app/medical_sources/seed.py, scripts/load_medical_sources.py). The motors' use of it is tested in
# tests/test_ai_sources.py; the admin API in tests/test_medical_sources_api.py.
# Run with: pytest

import copy
import pytest

from app import config
from app.medical_sources import library as medical_sources
from app.medical_sources import seed
from scripts import load_medical_sources as loader


@pytest.fixture(autouse=True)
def use_seed_file_unless_a_test_configures_database(monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", "")
    monkeypatch.setattr(config, "MEDICAL_SOURCE_STATUSES", ("active", "draft", "proposed_addition"))
    medical_sources.clear_cache()
    yield
    medical_sources.clear_cache()


@pytest.mark.parametrize("country, code", [
    ("US", "US"), ("us", "US"), ("United States", "US"), ("USA", "US"),
    ("MX", "MX"), ("mx", "MX"), ("Mexico", "MX"), ("México", "MX"), (" MEXICO ", "MX"),
    ("Canada", None), ("", None), (None, None),
])
def test_normalize_country(country, code):
    assert medical_sources.normalize_country(country) == code


def test_sources_are_filtered_by_country_and_motor():
    us_visual = {source["id"] for source in medical_sources.sources_for("visual", "US")}
    assert "US-ADA-SOC-2026" in us_visual  # diabetic foot photos
    assert "US-CDC-STROKE" not in us_visual  # triage only
    assert not any(source_id.startswith("MX-") for source_id in us_visual)


def test_only_allowed_statuses_are_given_to_the_ai(monkeypatch):
    monkeypatch.setattr(config, "MEDICAL_SOURCE_STATUSES", ("active",))  # production default; the draft is not active
    assert medical_sources.sources_for("triage", "US") == []
    assert 'Trusted source list: empty. Set "source_id" to "none".' in medical_sources.prompt_block("triage", "US")
    assert medical_sources.check_source("US-AHA-CHESTPAIN-2021", "triage", "US")["source_status"] == "pending_medical_review"


def test_proposed_addition_is_left_out_unless_allowed(monkeypatch):
    monkeypatch.setattr(config, "MEDICAL_SOURCE_STATUSES", ("active", "draft"))
    ids = {source["id"] for source in medical_sources.sources_for("vitals", "MX")}
    assert "MX-SSA-PRONAM-HAS-2026" not in ids
    assert "MX-OPS-BEC" in ids


def test_prompt_lists_ids_but_no_links():
    block = medical_sources.prompt_block("vitals", "US")
    assert "- US-AHA-HBP-2025 | " in block
    assert "https://" not in block  # links are attached by the backend, never copied by the model


def test_full_citation_has_authors_title_publication_doi_and_link():
    source = next(s for s in medical_sources.load_sources() if s["id"] == "US-AHA-HBP-2025")
    citation = medical_sources.full_citation(source)
    assert citation.startswith("Jones DW (Chair)")
    assert "doi:10.1161/HYP.0000000000000249" in citation
    assert citation.endswith(" https://doi.org/10.1161/HYP.0000000000000249")  # no "." glued to the link


def test_database_is_read_when_configured_and_cached(monkeypatch):
    calls = []
    row = {**next(s for s in medical_sources.read_from_file() if s["id"] == "US-CDC-STROKE"), "status": "active"}

    def fake_read():
        calls.append(1)
        return [row]

    monkeypatch.setattr(config, "DB_NAME", "vitalsigns")
    monkeypatch.setattr(medical_sources, "read_from_database", fake_read)
    medical_sources.clear_cache()

    assert [s["id"] for s in medical_sources.sources_for("triage", "US")] == ["US-CDC-STROKE"]
    medical_sources.sources_for("triage", "US")
    assert len(calls) == 1  # kept in memory for MEDICAL_SOURCES_CACHE_SECONDS


def test_database_down_keeps_the_last_list(monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", "vitalsigns")
    monkeypatch.setattr(config, "MEDICAL_SOURCES_CACHE_SECONDS", 0)  # read again on every call
    monkeypatch.setattr(medical_sources, "read_from_database", lambda: [{"id": "X", "country": "US", "motors": ["triage"],
                                                                         "status": "active"}])
    medical_sources.clear_cache()
    assert medical_sources.load_sources()[0]["id"] == "X"

    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(medical_sources, "read_from_database", down)
    assert medical_sources.load_sources()[0]["id"] == "X"


def test_database_down_at_start_gives_no_sources_and_retries_soon(monkeypatch):
    calls = []

    def down():
        calls.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr(config, "DB_NAME", "vitalsigns")
    monkeypatch.setattr(medical_sources, "read_from_database", down)
    medical_sources.clear_cache()
    assert medical_sources.load_sources() == []  # every result is then "pending_medical_review"
    medical_sources.load_sources()
    assert len(calls) == 1  # not retried on every request (each try may wait for the connect timeout)
    assert medical_sources._cache["loaded_at"] < medical_sources.time.monotonic() - (
        config.MEDICAL_SOURCES_CACHE_SECONDS - medical_sources.RETRY_SECONDS - 1
    )  # but retried after RETRY_SECONDS, not after the full cache time


def test_missing_file_gives_no_sources(tmp_path):
    assert medical_sources.read_from_file(tmp_path / "missing.json") == []


# ---------- first load (seed) ----------

def library():
    return seed.read_seed_file()


def test_the_seed_file_passes_the_check():
    assert seed.check_library(library()) == []


@pytest.mark.parametrize("change, problem", [
    (lambda s: s.update(link="http://example.org"), "link must start with https://"),
    (lambda s: s.update(country="CA"), "country must be one of"),
    (lambda s: s.update(motors=["triage", "chat"]), "motors must be among"),
    (lambda s: s.update(topics=["unknown_topic"]), "unknown topics"),
    (lambda s: s.update(status="approved"), "status must be one of"),
    (lambda s: s.update(status="active"), "approve it through the API"),
    (lambda s: s.update(id="us-lowercase"), "id must look like"),
    (lambda s: s.pop("patient_label"), "missing patient_label"),
])
def test_seed_refuses_a_bad_source(change, problem):
    data = library()
    change(data["sources"][0])
    assert any(problem in message for message in seed.check_library(data))


def test_seed_refuses_a_repeated_id():
    data = library()
    data["sources"].append(copy.deepcopy(data["sources"][0]))
    assert any("id used twice" in message for message in seed.check_library(data))


def test_seed_never_overwrites_a_row():
    assert "ON CONFLICT (id) DO NOTHING" in seed.INSERT_IF_NEW
    assert "DO UPDATE" not in seed.INSERT_IF_NEW.upper()


def test_seed_row_maps_the_file_fields():
    source = next(s for s in library()["sources"] if s["id"] == "MX-CENETEC-UPP-2015")
    row = seed.row(source)
    assert row["motors"] == ["visual"]
    assert row["needs_client_confirmation"] is True
    assert row["client_decision"] == "approve"
    assert (row["link_status"], row["link_checked_at"]) == ("ok", "2026-11-13")


def test_seed_file_topics_match_the_code():
    assert library()["topics"] == seed.TOPICS


def test_script_check_only_does_not_connect(monkeypatch, capsys):
    monkeypatch.setattr(seed, "connect", lambda: pytest.fail("must not connect with --check"))
    assert loader.main(["--check"]) == 0
    assert "18 sources OK" in capsys.readouterr().out
