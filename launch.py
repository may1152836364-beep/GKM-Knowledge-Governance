"""GUI entrypoint, including a fallback before Qt can load."""

if __name__ == "__main__":
    try:
        from gkm.ui import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        import os
        from pathlib import Path

        directory = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GKM"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            # Exception class only: never log credentials, content or request headers.
            (directory / "startup-diagnostic.log").write_text(
                "Startup failure: " + type(error).__name__, encoding="utf-8"
            )
        except OSError:
            pass
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "启动组件加载失败："
                + type(error).__name__
                + "。请完整解压后运行；不需要安装 Python。",
                "GKM 启动诊断",
                0x10,
            )
        raise SystemExit(1)
