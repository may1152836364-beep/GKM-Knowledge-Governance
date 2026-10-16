"""Reproducible Windows x64 portable bundle, built without executing Windows code.
Requires ziglang==0.13.0 only on the build machine. End users install nothing.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_URL = (
    "https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg"
)
# Official NuGet SHA512, observed over TLS; do not replace to bypass verification.
PYTHON_SHA512 = "u9pNz2iKlCEbYtUJaKkbOPMF0LjR7NkCafdKhvigpPzrt8oWKgdTpHaR6z3wyWQAm9PYGUxv0Zr66NX9AeHMDw=="


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--python-package", type=Path, required=True)
    args = parser.parse_args()
    package = args.python_package
    if not package.exists():
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as response:
            package.write_bytes(response.read())
    if (
        base64.b64encode(hashlib.sha512(package.read_bytes()).digest()).decode()
        != PYTHON_SHA512
    ):
        raise RuntimeError("CPython 官方包 SHA512 校验失败，停止构建")
    out = ROOT / "dist" / "GKM-Windows-x64"
    if out.exists():
        # Only remove this build-owned output; user data lives outside the package.
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    runtime = out / "runtime"
    runtime.mkdir(exist_ok=True)
    # Extract only official runtime payload, leaving source package signature intact.
    with zipfile.ZipFile(package) as z:
        for name in z.namelist():
            if name.startswith("tools/") and not name.endswith("/"):
                target = runtime / name[len("tools/") :]
                if ".." in Path(name).parts:
                    raise ValueError("unsafe archive")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    hashes = {}
    expected = json.loads((ROOT / "scripts" / "windows-wheels.sha256.json").read_text())
    for wheel in sorted(args.wheels.glob("*.whl")):
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if expected.get(wheel.name) != digest:
            raise RuntimeError("Wheel checksum mismatch: " + wheel.name)
        hashes[wheel.name] = digest
        with zipfile.ZipFile(wheel) as z:
            for name in z.namelist():
                if ".." in Path(name).parts:
                    raise ValueError("unsafe wheel")
            z.extractall(site)
    if hashes != expected:
        raise RuntimeError("缺少或多余依赖文件")
    # Locked import path avoids system Python, PYTHONPATH and registry pollution.
    (runtime / "python312._pth").write_text(
        ".\nDLLs\nLib\nLib/site-packages\n../app\nimport site\n", encoding="utf-8"
    )
    app = out / "app"
    app.mkdir(exist_ok=True)
    shutil.copytree(
        ROOT / "gkm",
        app / "gkm",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(ROOT / "launch.py", app / "launch.py")
    for name in (
        "USER_GUIDE.md",
        "ACCEPTANCE.md",
        "THIRD_PARTY.md",
        "TEST_REPORT.md",
        "PERFORMANCE.json",
        "PERFORMANCE_FINAL_SMOKE.json",
        "test-results.xml",
        "CASE_TEMPLATE.json",
        "RESULT_SCHEMA.json",
    ):
        if (ROOT / "docs" / name).exists():
            shutil.copy2(ROOT / "docs" / name, out / name)
    shutil.copytree(
        ROOT / "docs",
        out / "docs",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("PRD_V4.md", "UI_ARTICLES.png"),
    )
    (out / "使用说明.txt").write_text(
        (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8"),
        encoding="utf-8-sig",
    )
    build_env = {
        **os.environ,
        "ZIG_GLOBAL_CACHE_DIR": str(ROOT / "build" / "zig-global"),
        "ZIG_LOCAL_CACHE_DIR": str(ROOT / "build" / "zig-local"),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "ziglang",
            "cc",
            "-target",
            "x86_64-windows-gnu",
            "-municode",
            "-Wl,--subsystem,windows",
            "-O2",
            str(ROOT / "scripts" / "launcher.c"),
            "-o",
            str(out / "GKM.exe"),
            "-luser32",
        ],
        check=True,
        env=build_env,
    )
    # Static PE check; explicitly does not claim Windows execution.
    data = (out / "GKM.exe").read_bytes()
    pe = int.from_bytes(data[60:64], "little")
    assert (
        data[:2] == b"MZ"
        and data[pe : pe + 4] == b"PE\0\0"
        and int.from_bytes(data[pe + 4 : pe + 6], "little") == 0x8664
    )
    assert int.from_bytes(data[pe + 24 + 68 : pe + 24 + 70], "little") == 2, (
        "Launcher must be GUI subsystem (no console)"
    )
    from verify_clean_release import verify_package

    verify_package(out)
    manifest = {
        str(p.relative_to(out)).replace("\\", "/"): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "SHA256.json"
    }
    (out / "SHA256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = shutil.make_archive(
        str(ROOT / "dist" / "GKM-Windows-x64"), "zip", out.parent, out.name
    )
    print(
        json.dumps(
            {
                "artifact": archive,
                "files": len(manifest),
                "runtime": "CPython 3.12.10 x64 bundled",
                "executed_on_windows": False,
                "sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
