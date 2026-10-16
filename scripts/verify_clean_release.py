"""Release gate: an empty business store and no persisted user data in packages."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gkm.storage import Store

BUSINESS_TABLES = (
    "articles",
    "article_versions",
    "cases",
    "case_history",
    "analysis_jobs",
    "job_items",
    "analysis_results",
    "reviews",
    "model_profiles",
    "import_batches",
)
FORBIDDEN_SUFFIXES = (
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".db",
    ".db-wal",
    ".db-shm",
    ".dpapi",
    ".xlsx",
    ".xls",
    ".csv",
)
PRIVATE_NAMES = {"PRD_V4.md", "UI_ARTICLES.png"}


def verify_package(directory):
    directory = Path(directory)
    forbidden = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.lower().endswith(FORBIDDEN_SUFFIXES) or name in PRIVATE_NAMES:
            forbidden.append(str(path.relative_to(directory)))
    if forbidden:
        raise ValueError(
            "Package contains persisted/private data: " + ", ".join(forbidden)
        )
    return {
        "package_has_persisted_business_data": False,
        "private_source_documents_included": False,
    }


def verify_empty_store():
    with tempfile.TemporaryDirectory(prefix="gkm-release-check-") as directory:
        store = Store(directory)
        try:
            counts = {
                table: store.one("SELECT count(*) n FROM " + table)["n"]
                for table in BUSINESS_TABLES
            }
            if any(counts.values()):
                raise ValueError("Fresh store is not empty")
            return {"fresh_store": counts}
        finally:
            store.close_connection()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh-store", action="store_true")
    parser.add_argument("--package-dir", type=Path)
    args = parser.parse_args()
    result = {}
    if args.fresh_store:
        result.update(verify_empty_store())
    if args.package_dir:
        result.update(verify_package(args.package_dir))
    if not result:
        parser.error("Select --fresh-store and/or --package-dir")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
