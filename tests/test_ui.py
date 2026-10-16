import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from gkm.storage import Store
from gkm.ui import Window, Form
from .helpers import MemoryVault, seed


def test_native_navigation_and_plain_text(qtbot, tmp_path):
    db = Store(tmp_path)
    seed(db, 2)
    db.execute(
        "UPDATE article_versions SET title='<script>alert(1)</script>' WHERE id=1"
    )
    w = Window(db, MemoryVault())
    qtbot.addWidget(w)
    w.show()
    assert w.stack.count() == 8
    for i in range(8):
        w.nav.setCurrentRow(i)
        assert w.stack.currentIndex() == i
    w.nav.setCurrentRow(1)
    assert w.article_table.rowCount() == 2
    assert w.article_table.item(0, 1).text() == "<script>alert(1)</script>"
    w.search.setText("not-found")
    w.refresh_articles()
    assert w.article_table.rowCount() == 0
    w.close()


def test_password_no_echo(qtbot):
    from PySide6.QtWidgets import QLineEdit

    d = Form("test")
    qtbot.addWidget(d)
    edit = d.line("key", "Key", "", True)
    assert edit.echoMode() == QLineEdit.Password


def test_result_filters_and_model_no_plaintext(qtbot, tmp_path):
    from .test_jobs import prepare, wait
    from .helpers import transport
    from gkm.jobs import Engine, create_job

    db, vault, pid = prepare(tmp_path, 3)
    e = Engine(db, vault, transport())
    job = create_job(db, pid, 10, consent=True)
    e.start(job)
    wait(e, job)
    w = Window(db, vault, e)
    qtbot.addWidget(w)
    w.show()
    w.nav.setCurrentRow(4)
    assert w.result_table.rowCount() == 3
    w.result_language.setText("en")
    w.refresh_results()
    assert w.result_table.rowCount() == 0
    w.result_language.clear()
    w.evidence_filter.setCurrentIndex(1)
    w.refresh_results()
    assert w.result_table.rowCount() == 3
    w.rec_filter.setCurrentIndex(w.rec_filter.findData("review_removal"))
    w.refresh_results()
    assert w.result_table.rowCount() == 0
    w.nav.setCurrentRow(7)
    text = " ".join(
        w.model_table.item(r, c).text()
        for r in range(w.model_table.rowCount())
        for c in range(w.model_table.columnCount())
    )
    assert "secret" not in text and "是" in text
    w.close()
