# First load of the trusted medical source library into ai.medical_sources (code: app/medical_sources/seed.py).
#
#   python -m scripts.load_medical_sources              add the seed file's sources that are not in the table yet
#   python -m scripts.load_medical_sources --check      only check the seed file, change nothing
#   python -m scripts.load_medical_sources --file other.json
#
# Run "alembic upgrade head" first (it creates the table). Database: DATABASE_URL if set, otherwise the DB_*
# values, the same as Alembic (both may come from .env).
# It never changes or deletes an existing row: after the first load, sources are added, changed, approved and
# deactivated only through the admin API, /ai/admin/medical-sources (see app/medical_sources/controller.py).

import argparse
import sys
from pathlib import Path

from app import config
from app.medical_sources import seed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="First load of the trusted medical source library.")
    parser.add_argument("--file", type=Path, default=seed.LIBRARY_FILE, help="default: db/medical_sources/medical_sources.json")
    parser.add_argument("--check", action="store_true", help="only check the file; do not touch the database")
    args = parser.parse_args(argv)

    library = seed.read_seed_file(args.file)
    problems = seed.check_library(library)
    if problems:
        print(f"{args.file}: not loaded, fix these first:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    sources = library["sources"]
    print(f"{args.file}: {len(sources)} sources OK")
    if args.check:
        return 0

    counts = seed.seed(library)
    print(f"ai.medical_sources: {counts['added']} added, {counts['skipped']} already there (left unchanged).")
    print("From now on, add and change sources through the admin API: /ai/admin/medical-sources "
          f"(the AI cites statuses {','.join(config.MEDICAL_SOURCE_STATUSES)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
