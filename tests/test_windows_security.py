import os
import pytest
from gkm.security import Vault
from gkm.storage import uid


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows DPAPI")
def test_dpapi_persist_restart(tmp_path):
    ref = uid()
    Vault(tmp_path).save(ref, "test-key-not-production")
    assert Vault(tmp_path).get(ref) == "test-key-not-production"
    assert (
        b"test-key-not-production" not in next(tmp_path.rglob("*.dpapi")).read_bytes()
    )


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows 子进程验证 DPAPI")
def test_dpapi_new_process(tmp_path):
    import subprocess
    import sys

    ref = uid()
    Vault(tmp_path).save(ref, "test-only-key")
    code = 'from gkm.security import Vault; import sys; assert Vault(sys.argv[1]).get(sys.argv[2]) == "test-only-key"'
    subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), ref],
        check=True,
        capture_output=True,
    )
