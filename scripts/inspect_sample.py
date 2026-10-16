"""Private sample diagnostics. Outputs counts/headers only, never article text."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gkm.storage import Store
from gkm.excel import preview, import_xlsx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("xlsx", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    pre = preview(args.xlsx)
    with TemporaryDirectory(prefix="gkm-private-sample-") as directory:
        db = Store(directory)
        report = import_xlsx(db, args.xlsx, pre["mapping"])
        lengths = [
            r["n"]
            for r in db.rows(
                "SELECT length(raw_content) n FROM article_versions ORDER BY n"
            )
        ]
        result = {
            "file_name": args.xlsx.name,
            "sheets": pre["sheets"],
            "column_count": len(pre["headers"]),
            "headers": pre["headers"],
            "mapping": pre["mapping"],
            "import": report,
            "content_length": {
                "count": len(lengths),
                "median": lengths[len(lengths) // 2] if lengths else None,
                "mean": sum(lengths) / len(lengths) if lengths else None,
                "max": max(lengths) if lengths else None,
            },
            "parsing": db.rows(
                "SELECT parse_status,count(*) count FROM article_versions GROUP BY parse_status"
            ),
        }
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Windows cannot remove a live sqlite handle.
        db.close_connection()
    print("已保存不含正文的样本核对报告：" + str(args.output))


if __name__ == "__main__":
    main()
