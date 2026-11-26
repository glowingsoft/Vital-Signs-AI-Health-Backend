# Trusted medical source library

`medical_sources.json` is the **seed file**: the first list of medical sources the AI may cite, owned by Vital Signs LLC.

**It is used once**, to fill the table `ai.medical_sources` on a new database (`python -m scripts.load_medical_sources`, which never changes a row already there). After that, sources are added, changed, approved and deactivated **only through the admin API** (`/ai/admin/medical-sources`, Admin token only; code in `app/medical_sources/`). Editing this file later changes nothing in the database.

## How it is used

The motors read the table `ai.medical_sources`. Code: `app/medical_sources/`; prompt: `app/shared/prompts.py`.

1. The AI is given only the sources for the patient's country (`country`: `US` or `MX`) and its motor.
2. The AI picks a source `id` from that list, or `none`.
3. The backend checks the `id` is in the list and attaches the exact `title` and `link` from the library. Anything not in the list is ignored.
4. If no source fits, the result shows no reference and is marked **"pending medical review"**. A link is never invented.

Display:
- Patient: the short `patient_label` (e.g. "según las Guías de Práctica Clínica de la Secretaría de Salud"), with the full link one tap away.
- Doctor: the full citation (`authors`, `title`, `publication`, `year`, `doi`, `link`).
- Critical alerts: SOS and Call 911 come first; the source loads afterwards in the background.

## Fields

| Field | Meaning |
|---|---|
| `id` | Stable ID the AI returns, e.g. `US-AHA-HBP-2025` |
| `country`, `language` | `US`/`en` or `MX`/`es` |
| `organisation`, `authors`, `title`, `year`, `publication`, `doi` | Full citation for doctors |
| `link` | Verified link (see `link_check`) |
| `topics` | Keys from the `topics` list at the top of the file |
| `motors` | Which AI service may use it: `triage`, `visual`, `vitals` |
| `patient_label` | Short line shown to the patient |
| `evidence_level` | `clinical_guideline`, `expert_consensus`, `peer_reviewed_study` or `public_health_guidance` |
| `client_decision` | The client's decision in the review sheet (`approve`, `approve_with_changes`, `reject`) |
| `status` | `draft`, `proposed_addition`, `active`, `inactive` or `broken`. Only statuses in `MEDICAL_SOURCE_STATUSES` (default `active`) are used by the AI |
| `current` | Whether the guideline is the latest version |
| `link_check` | Result and date of the last link check |
| `needs_client_confirmation` | `true` when the client must confirm something before approval |

## Rules

- Only an Admin can add or change sources (admin API). A new source starts as `draft`.
- Nothing becomes `active` without the client's written approval: `POST /ai/admin/medical-sources/{id}/approve` with who approved it, the message and the date. Changing an active source needs the approval of that change too.
- Never delete a source: deactivate it (`inactive`), so past results keep their reference. The API has no delete.
- Every change is written to `ai.audit_log`.
- The full list is reviewed every six months.
- Every link is checked automatically; a broken link is flagged and an alert is sent (to be built).

## Current draft (18 Nov 2026)

- 18 sources: 9 US (AHA, ADA, WHO, CDC) and 9 Mexico (SMC, SMNyCT, CENETEC, OPS, plus the proposed PRONAM).
- Client review sheet (read 18 Nov 2026): all 18 marked "Approve", no comments; the "Open questions" tab is not answered yet. `client_decision` records this. The sources stay `draft` until each one is approved through the API with the client's written approval.
- Left out: `MX-CENETEC-HAS-2021` and `MX-CENETEC-PIEDIABETICO-2020` (official links could not be opened).
- Known gaps (results on these topics will show "pending medical review"): Mexico has no source for stroke signs or sepsis; the US list has no heart failure source.
