# Trusted medical source library (Addendum A, medical-source field). Everything about it lives in this folder:
#   controller.py  admin API: /ai/admin/medical-sources (Admin token only)
#   service.py     the rules: validation, status changes, client approval, audit log
#   repository.py  the SQL on ai.medical_sources and ai.audit_log
#   schemas.py     request and response bodies
#   constants.py   allowed countries, statuses, topics and evidence levels
#   library.py     what the motors use at run time: the source list in the prompt and the check after the answer
#   seed.py        first load from db/medical_sources/medical_sources.json (scripts/load_medical_sources.py)
# The daily link check of the active sources is a cron job: app/cron_jobs/source_check/check_links.py.
# After the first load, sources are added and changed only through the admin API.
