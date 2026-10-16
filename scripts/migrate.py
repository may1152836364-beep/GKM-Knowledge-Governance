"""Support entrypoint: same backed-up migrations used at application startup."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gkm.storage import Store

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    store = Store(args.data_dir)
    print("数据库架构版本：", store.one("PRAGMA user_version")["user_version"])
    print("升级前备份保存在原数据目录；未知较新版本拒绝写入。")
