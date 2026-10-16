"""Exercise actual main(), theme, DB migration and lock across clean processes."""

import os
import subprocess
import sys
import json


def test_native_startup_restart_without_tcp_port(tmp_path):
    code = """
import json,os,socket
from pathlib import Path
from PySide6.QtCore import QTimer
import gkm.ui as ui

def no_bind(*args,**kwargs):raise AssertionError('UI must not bind TCP ports')
socket.socket.bind=no_bind
Original=ui.Window
class Smoke(Original):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.nav.setCurrentRow(1)
        assert self.stack.count()==8
        assert self.styleSheet()==''  # theme belongs to QApplication
        Path(os.environ['GKM_DATA_DIR'],'readiness.json').write_text(json.dumps({'pages':self.stack.count(),'schema':self.store.one('PRAGMA user_version')['user_version']}))
        QTimer.singleShot(100,self.close)
ui.Window=Smoke
raise SystemExit(ui.main())
"""
    env = {**os.environ, "GKM_DATA_DIR": str(tmp_path), "QT_QPA_PLATFORM": "offscreen"}
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            timeout=15,
            env=env,
        )
        assert json.loads((tmp_path / "readiness.json").read_text()) == {
            "pages": 8,
            "schema": 2,
        }
        assert not (tmp_path / "app.lock").exists()
