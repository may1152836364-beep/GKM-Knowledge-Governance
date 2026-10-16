import pytest
from scripts.verify_clean_release import verify_empty_store, verify_package


def test_release_starts_with_empty_business_data():
    assert not any(verify_empty_store()["fresh_store"].values())


@pytest.mark.parametrize(
    "name",
    [
        "gkm.sqlite3",
        "gkm.sqlite3-wal",
        "sample.xlsx",
        "key.dpapi",
        "PRD_V4.md",
        "UI_ARTICLES.png",
    ],
)
def test_release_rejects_persisted_or_private_data(tmp_path, name):
    (tmp_path / name).write_text("synthetic test sentinel", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_package(tmp_path)


def test_release_accepts_application_and_reports(tmp_path):
    (tmp_path / "launch.py").write_text("# application source", encoding="utf-8")
    (tmp_path / "TEST_REPORT.md").write_text("only aggregate counts", encoding="utf-8")
    assert not verify_package(tmp_path)["package_has_persisted_business_data"]
