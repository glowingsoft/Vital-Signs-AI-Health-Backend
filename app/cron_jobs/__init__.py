# Scheduled jobs (run from the server's crontab, not by the API). Entry point: python -m app.cron_jobs.main
#   main.py                       the registry of jobs and the runner
#   source_check/check_links.py   daily check of the links of the active medical sources
