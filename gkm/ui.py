"""Native desktop UI. No HTTP listener, HTML execution, or credential echo."""

import difflib
import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QTimer, QLockFile, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QStackedWidget,
    QLabel,
    QPushButton,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QSplitter,
    QScrollArea,
    QTabWidget,
    QAbstractItemView,
    QInputDialog,
)
from .storage import Store, dumps
from .security import Vault
from .excel import preview, import_xlsx, export_results, set_cleaning, ALIASES
from .ai import save_profile, test_connection
from .jobs import Engine, create_job, progress, estimate, refresh_credentials
from .workflows import bulk_review, compare_prompts, evaluate_job
from .learning import (
    save_case,
    approve_case,
    activate_case,
    review,
    save_prompt,
    publish_prompt,
)

LABELS = {
    "queued": "待开始",
    "running": "运行中",
    "pausing": "暂停中",
    "paused": "已暂停",
    "cancelling": "取消中",
    "cancelled": "已取消",
    "completed": "已完成",
    "completed_with_errors": "完成（有失败）",
    "keep": "保留",
    "revise": "修改",
    "review_removal": "下架评估",
    "business_verification": "待业务核实",
    "ready": "可分析",
    "insufficient_content": "正文信息不足",
    "missing_title": "缺标题",
    "missing_body": "缺正文",
    "pending": "待审核",
    "approved": "已审核",
    "archived": "已归档",
    "confirmed_issue": "文内证实问题",
    "suspected_issue": "疑似问题",
    "no_issue_found": "暂未发现",
    "insufficient_information": "无法判断",
    "full": "全文",
    "partial": "局部",
    "unknown": "未知",
    "not_applicable": "不适用",
    "succeeded": "成功",
    "failed": "失败",
    "retry": "待重试",
    "inflight": "进行中",
}
NAV = [
    "首页总览",
    "文章数据",
    "AI 智能识别",
    "任务管理",
    "识别结果及复核",
    "案例知识库",
    "规则与提示词",
    "模型与系统设置",
]
RECS = [
    ("保留", "keep"),
    ("修改", "revise"),
    ("下架评估", "review_removal"),
    ("待业务核实", "business_verification"),
]


def label(value):
    return LABELS.get(str(value), str(value) if value is not None else "")


class Worker(QThread):
    success = Signal(object)
    failure = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.success.emit(self.fn())
        except Exception as e:
            # Avoid raw network exceptions, which can include headers/contents.
            self.failure.emit(
                str(e)
                if isinstance(e, (ValueError, RuntimeError))
                else "操作失败："
                + type(e).__name__
                + "。请检查文件权限、文件格式和数据目录。"
            )


class Table(QTableWidget):
    def __init__(self, columns):
        super().__init__()
        self.columns = columns
        self.records = []
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels([c[1] for c in columns])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setAlternatingRowColors(True)

    def fill(self, rows):
        selected = self.record()
        selected_id = selected.get("id") if selected else None
        self.records = rows
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, (key, _) in enumerate(self.columns):
                value = row.get(key, "")
                item = QTableWidgetItem(label(value))
                item.setToolTip(label(value))
                self.setItem(r, c, item)
            if row.get("id") == selected_id:
                self.selectRow(r)

    def record(self):
        i = self.currentRow()
        return self.records[i] if 0 <= i < len(self.records) else None


class Form(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 580)
        self.box = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QWidget()
        self.form = QFormLayout(contents)
        scroll.setWidget(contents)
        self.box.addWidget(scroll, 1)
        self.fields = {}

    def line(self, key, title, value="", password=False):
        edit = QLineEdit(str(value or ""))
        self.fields[key] = edit
        if password:
            edit.setEchoMode(QLineEdit.Password)
        self.form.addRow(title, edit)
        return edit

    def area(self, key, title, value=""):
        edit = QPlainTextEdit(str(value or ""))
        edit.setMinimumHeight(90)
        self.fields[key] = edit
        self.form.addRow(title, edit)
        return edit

    def combo(self, key, title, values, current=None):
        widget = QComboBox()
        for text, data in values:
            widget.addItem(text, data)
        if current is not None:
            index = widget.findData(current)
            if index >= 0:
                widget.setCurrentIndex(index)
        self.fields[key] = widget
        self.form.addRow(title, widget)
        return widget

    def number(self, key, title, value=0, minimum=0, maximum=1_000_000, decimal=False):
        widget = QDoubleSpinBox() if decimal else QSpinBox()
        widget.setRange(minimum, maximum)
        if decimal:
            widget.setDecimals(6)
        widget.setValue(value)
        self.fields[key] = widget
        self.form.addRow(title, widget)
        return widget

    def check(self, key, title, value=False):
        widget = QCheckBox(title)
        widget.setChecked(value)
        self.fields[key] = widget
        self.form.addRow(widget)
        return widget

    def buttons(self):
        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.button(QDialogButtonBox.Ok).setText("确认")
        b.button(QDialogButtonBox.Cancel).setText("取消")
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        self.box.addWidget(b)

    def values(self):
        result = {}
        for k, w in self.fields.items():
            if isinstance(w, QLineEdit):
                result[k] = w.text()
            elif isinstance(w, QPlainTextEdit):
                result[k] = w.toPlainText()
            elif isinstance(w, QComboBox):
                result[k] = w.currentData()
            elif isinstance(w, QCheckBox):
                result[k] = w.isChecked()
            else:
                result[k] = w.value()
        return result


class Window(QMainWindow):
    def __init__(self, store=None, vault=None, engine=None):
        super().__init__()
        self.store = store or Store()
        self.vault = vault or Vault(self.store.directory)
        self.engine = engine or Engine(self.store, self.vault)
        self.workers = []
        self.article_page = 0
        self.result_page = 0
        self.setWindowTitle("GKM · 知识有效性智能治理助手")
        self.resize(1400, 900)
        self.setMinimumSize(1050, 700)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        self.nav = QListWidget()
        self.nav.addItems(NAV)
        self.nav.setFixedWidth(200)
        for item_index in range(self.nav.count()):
            self.nav.item(item_index).setSizeHint(QSize(180, 54))
        self.nav.setUniformItemSizes(True)
        outer.addWidget(self.nav)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.pages = []
        for title in NAV:
            page = QWidget()
            layout = QVBoxLayout(page)
            h = QLabel(title)
            h.setObjectName("title")
            layout.addWidget(h)
            self.stack.addWidget(page)
            self.pages.append(layout)
        self.nav.currentRowChanged.connect(self.page_changed)
        self.build_dashboard()
        self.build_articles()
        self.build_ai()
        self.build_jobs()
        self.build_results()
        self.build_cases()
        self.build_prompts()
        self.build_settings()
        self.statusBar().showMessage(
            "本地资料仅保存在本机；AI 仅建议，不执行上下架。数据目录："
            + str(self.store.directory)
        )
        self.nav.setCurrentRow(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1500)
        self.setAcceptDrops(True)

    def run(self, fn, done=None):
        worker = Worker(fn)
        self.workers.append(worker)
        worker.success.connect(
            lambda r: self.protect(
                lambda: (done(r) if done else self.show_text("操作结果", dumps(r)))
            )
        )
        worker.failure.connect(self.error)

        def cleanup():
            self.workers.remove(worker)
            worker.deleteLater()

        worker.finished.connect(cleanup)
        worker.start()
        self.statusBar().showMessage("后台处理中，界面可继续操作…")

    def error(self, message):
        QMessageBox.warning(self, "操作未完成", message)

    def protect(self, fn):
        try:
            return fn()
        except Exception as e:
            self.error(
                str(e)
                if isinstance(e, (ValueError, RuntimeError))
                else "操作失败：" + type(e).__name__
            )

    def button(self, layout, text, fn):
        b = QPushButton(text)
        b.clicked.connect(lambda: self.protect(fn))
        layout.addWidget(b)
        return b

    def bar(self, page):
        row = QHBoxLayout()
        self.pages[page].addLayout(row)
        return row

    def show_text(self, title, text):
        d = QDialog(self)
        d.setWindowTitle(title)
        d.resize(950, 720)
        layout = QVBoxLayout(d)
        area = QPlainTextEdit()
        area.setReadOnly(True)
        area.setPlainText(text)
        layout.addWidget(area)
        b = QPushButton("关闭")
        b.clicked.connect(d.accept)
        layout.addWidget(b)
        d.exec()

    def required(self, table):
        row = table.record()
        if not row:
            raise ValueError("请先选择一行")
        return row

    def page_changed(self, index):
        self.stack.setCurrentIndex(index)
        self.refresh(index)

    def tick(self):
        index = self.stack.currentIndex()
        if index in (0, 3):
            self.refresh(index)

    def refresh(self, index=None):
        index = self.stack.currentIndex() if index is None else index
        self.protect(
            {
                0: self.refresh_dashboard,
                1: self.refresh_articles,
                2: self.refresh_profiles,
                3: self.refresh_jobs,
                4: self.refresh_results,
                5: self.refresh_cases,
                6: self.refresh_prompts,
                7: self.refresh_models,
            }[index]
        )

    def build_dashboard(self):
        layout = self.pages[0]
        intro = QLabel("让每一条治理建议有原文依据，让每一次人工复核可追溯。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        self.stats.setObjectName("stats")
        layout.addWidget(self.stats)
        self.dashboard_table = Table([("name", "统计维度"), ("value", "数量")])
        layout.addWidget(self.dashboard_table)
        self.button(layout, "刷新总览", self.refresh_dashboard)

    def refresh_dashboard(self):
        count = self.store.one("SELECT count(*) n FROM articles")["n"]
        ready = self.store.one(
            "SELECT count(*) n FROM article_versions v JOIN articles a ON a.current_version=v.id WHERE v.parse_status='ready'"
        )["n"]
        done = self.store.one("SELECT count(*) n FROM analysis_results")["n"]
        reviews = self.store.one("SELECT count(DISTINCT result_fk) n FROM reviews")["n"]
        self.stats.setText(
            f"导入文章  {count:,}       可分析文章  {ready:,}\n累计有效结构结果  {done:,}       已复核结果  {reviews:,}       待复核  {max(0, done - reviews):,}"
        )
        rows = [
            {"name": "语言 / " + r["language"], "value": r["n"]}
            for r in self.store.rows(
                "SELECT language,count(*) n FROM articles GROUP BY language"
            )
        ]
        rows += [
            {"name": "任务项 / " + label(r["state"]), "value": r["n"]}
            for r in self.store.rows(
                "SELECT state,count(*) n FROM job_items GROUP BY state"
            )
        ]
        rows += [
            {"name": "建议 / " + label(r["rec"]), "value": r["n"]}
            for r in self.store.rows(
                "SELECT json_extract(result,'$.recommendation') rec,count(*) n FROM analysis_results GROUP BY rec"
            )
        ]
        self.dashboard_table.fill(rows)

    def build_articles(self):
        row = self.bar(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("按文章 ID 或标题搜索")
        row.addWidget(self.search)
        self.language_filter = QLineEdit()
        self.language_filter.setPlaceholderText("语言（空为全部）")
        row.addWidget(self.language_filter)
        self.status_filter = QLineEdit()
        self.status_filter.setPlaceholderText("发布状态（空为全部）")
        row.addWidget(self.status_filter)
        self.button(row, "查询", lambda: self.change_article_page(0, True))
        self.search.returnPressed.connect(lambda: self.change_article_page(0, True))
        row = self.bar(1)
        self.button(row, "导入 Excel（多文件）", self.choose_import)
        self.button(row, "原文 / 清洗 / 版本详情", self.article_detail)
        self.button(row, "导入报告", self.import_reports)
        self.button(row, "清洗策略 / 撤销", self.cleaning_dialog)
        self.article_table = Table(
            [
                ("article_id", "文章 ID"),
                ("title", "标题"),
                ("language", "语言"),
                ("category", "分类"),
                ("release_status", "发布状态"),
                ("content_length", "正文字数"),
                ("parse_status", "解析状态"),
                ("source_updated_at", "源更新时间"),
            ]
        )
        self.article_table.cellDoubleClicked.connect(
            lambda *_: self.protect(self.article_detail)
        )
        self.pages[1].addWidget(self.article_table)
        row = self.bar(1)
        self.button(row, "上一页", lambda: self.change_article_page(-1))
        self.article_page_label = QLabel()
        row.addWidget(self.article_page_label)
        self.button(row, "下一页", lambda: self.change_article_page(1))

    def change_article_page(self, delta, reset=False):
        self.article_page = 0 if reset else max(0, self.article_page + delta)
        self.refresh_articles()

    def refresh_articles(self):
        rows = self.store.articles(
            self.search.text(),
            self.language_filter.text(),
            self.status_filter.text(),
            self.article_page,
        )
        self.article_table.fill(rows)
        self.article_page_label.setText(
            f"第 {self.article_page + 1} 页 · 每页 50 条 · 当前 {len(rows)} 条"
        )

    def choose_import(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 Excel 文件", "", "Excel 工作簿 (*.xlsx)"
        )
        if files:
            self.import_next(list(files))

    def import_next(self, files):
        if not files:
            return
        path = files.pop(0)
        self.run(lambda: preview(path), lambda p: self.map_import(path, p, files))

    def map_import(self, path, p, remaining):
        d = Form("导入预览 · " + Path(path).name, self)
        sheet = d.combo("sheet", "工作表", [(s, s) for s in p["sheets"]], p["sheet"])
        d.line("scope", "来源范围（跨文件相同业务使用相同值）", "default")
        for key in ALIASES:
            d.combo(
                key,
                key,
                [("不映射", "")] + [(h, h) for h in p["headers"]],
                p["mapping"].get(key),
            )
        sample = QPlainTextEdit()
        sample.setReadOnly(True)
        sample.setPlainText(
            "预计行数："
            + str(p["row_estimate"])
            + "\n"
            + "\t".join(p["headers"])
            + "\n"
            + "\n".join("\t".join(r) for r in p["rows"])
        )
        d.box.addWidget(sample)

        def changed(name):
            if name != p["sheet"]:
                d.reject()
                self.run(
                    lambda: preview(path, name),
                    lambda new: self.map_import(path, new, remaining),
                )

        sheet.currentTextChanged.connect(changed)
        d.buttons()
        if d.exec() != QDialog.Accepted:
            return
        values = d.values()
        mapping = {k: values[k] for k in ALIASES}
        if not values["scope"].strip():
            raise ValueError("来源范围不能为空")

        def finished(report):
            self.show_text("导入报告", dumps(report))
            self.refresh_articles()
            self.import_next(remaining)

        self.run(
            lambda: import_xlsx(
                self.store, path, mapping, values["sheet"], values["scope"]
            ),
            finished,
        )

    def import_reports(self):
        rows = self.store.rows(
            "SELECT * FROM import_batches ORDER BY created_at DESC LIMIT 20"
        )
        self.show_text("最近 20 次导入报告", dumps(rows))
        path, _ = QFileDialog.getSaveFileName(
            self, "可选：保存报告（不含正文）", "", "JSON (*.json)"
        )
        if path:
            Path(path).write_text(dumps(rows), encoding="utf-8")

    def cleaning_dialog(self):
        row = self.required(self.article_table)
        a = self.store.article_version(row["current_version"])
        d = Form("可审阅、可撤销的清洗策略", self)
        d.box.addWidget(
            QLabel(
                "当前策略："
                + a["clean_version"]
                + "。每次切换创建新文章版本，原文和历史 AI 结果不变。"
            )
        )
        d.combo(
            "mode",
            "分析正文策略",
            [
                ("统一换行；不删除任何业务词 / HTML / 链接", "preserve-v1"),
                ("撤销清洗，使用完整原文", "raw-v1"),
                (
                    "可读 HTML：保留段落/表格/链接/图片说明（可撤销）",
                    "html-readable-v1",
                ),
            ],
            a["clean_version"],
        )
        d.buttons()
        if d.exec() == QDialog.Accepted:
            set_cleaning(self.store, row["id"], d.values()["mode"])
            self.refresh_articles()

    def article_detail(self):
        row = self.required(self.article_table)
        a = self.store.article_version(row["current_version"])
        versions = self.store.rows(
            "SELECT id,title,created_at,content_hash,parse_status FROM article_versions WHERE article_fk=? ORDER BY id DESC",
            (row["id"],),
        )
        d = QDialog(self)
        d.setWindowTitle(a["article_id"] + " · " + a["title"])
        d.resize(1100, 750)
        layout = QVBoxLayout(d)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        for title, text in [
            ("原始正文", a["raw_content"]),
            ("清洗正文", a["clean_content"]),
            (
                "清洗差异（策略有版本，可撤销）",
                "\n".join(
                    difflib.unified_diff(
                        a["raw_content"].splitlines(keepends=True),
                        a["clean_content"].splitlines(keepends=True),
                    )
                ),
            ),
            ("元数据", a["metadata_json"]),
            ("历史版本", dumps(versions)),
        ]:
            area = QPlainTextEdit()
            area.setReadOnly(True)
            area.setPlainText(text)
            tabs.addTab(area, title)
        selector = QComboBox()
        for v in versions:
            selector.addItem(f"版本 {v['id']} · {v['title']}", v["id"])
        layout.addWidget(selector)

        def show_version():
            v = self.store.article_version(selector.currentData())
            self.show_text(
                "历史原文 / 清洗正文",
                v["raw_content"] + "\n\n——清洗正文——\n" + v["clean_content"],
            )

        self.button(layout, "查看所选历史版本", show_version)
        d.exec()

    def build_ai(self):
        layout = self.pages[2]
        info = QLabel(
            "默认全量：所有正文充分的当前文章版本均进入 AI；不按低风险、发布状态或语言跳过。\n费用使用配置价格，估算与实际 Token 费用分别记录。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.profile_select = QComboBox()
        layout.addWidget(self.profile_select)
        self.button(layout, "刷新模型列表", self.refresh_profiles)
        self.button(layout, "预估全量规模与费用", self.show_estimate)
        self.button(layout, "创建分析任务", self.new_job)
        self.button(layout, "前往模型设置", lambda: self.nav.setCurrentRow(7))
        layout.addStretch()

    def refresh_profiles(self):
        selected = self.profile_select.currentData()
        self.profile_select.clear()
        for r in self.store.rows("SELECT * FROM model_profiles"):
            self.profile_select.addItem(
                r["name"]
                + " · "
                + r["model"]
                + (" · 已通过在线测试" if r["tested_hash"] else " · 未通过测试"),
                r["id"],
            )
        i = self.profile_select.findData(selected)
        if i >= 0:
            self.profile_select.setCurrentIndex(i)

    def show_estimate(self):
        pid = self.profile_select.currentData()
        if not pid:
            raise ValueError("请先保存并测试模型")
        self.run(lambda: estimate(self.store, pid))

    def new_job(self):
        pid = self.profile_select.currentData()
        if not pid:
            raise ValueError("请先配置模型")
        profile = self.store.one("SELECT * FROM model_profiles WHERE id=?", (pid,))
        d = Form("创建全量 AI 深度分析任务", self)
        d.box.addWidget(
            QLabel("在线测试结果：" + str(profile["test_report"] or "尚未测试"))
        )
        d.combo(
            "scope",
            "范围",
            [
                ("全部可分析文章", "all"),
                ("指定导入批次", "batch"),
                ("新增/修改且尚无结果", "changed"),
                ("手工文章 ID", "ids"),
            ],
        )
        prompts = self.store.rows(
            "SELECT id,note,published FROM prompt_versions ORDER BY id DESC"
        )
        d.combo(
            "prompt_id",
            "提示词 / 规则版本",
            [("使用当前发布版本", None)]
            + [
                (
                    str(p["id"])
                    + " "
                    + p["note"]
                    + ("（已发布）" if p["published"] else "（仅回归验证）"),
                    p["id"],
                )
                for p in prompts
            ],
        )
        d.line("batch", "导入批次 ID（指定批次时填写）")
        d.line("candidate_case_ids", "仅回归验证：候选案例 ID（可选，逗号分隔）")
        d.area("ids", "文章 ID（手工范围，逗号分隔）")
        d.number(
            "budget", "总费用预算（与模型价格同币种）", 10, 0.000001, 1000000, True
        )
        d.number("max_call_cost", "单次调用费用上限", 1, 0.000001, 1000000, True)
        d.number("concurrency", "并发", 3, 1, 10)
        d.number("rpm", "每分钟请求上限", 60, 1)
        d.number("tpm", "每分钟 Token 上限", 200000, 1, 100000000)
        d.number("retries", "失败自动重试次数", 3, 0, 10)
        d.check(
            "consent",
            "我确认此为企业批准的 API，授权本任务发送标题、正文、元数据和审核案例",
        )
        d.buttons()
        if d.exec() != QDialog.Accepted:
            return
        v = d.values()
        v["candidate_case_ids"] = [
            x.strip() for x in v["candidate_case_ids"].split(",") if x.strip()
        ]
        scope = v.pop("scope")
        batch = v.pop("batch").strip()
        ids = [s.strip() for s in v.pop("ids").split(",") if s.strip()]
        if scope == "batch" and not batch:
            raise ValueError("指定批次不能为空")
        if scope == "ids" and not ids:
            raise ValueError("手工范围必须填写文章 ID")

        def finished(job):
            self.nav.setCurrentRow(3)
            self.refresh_jobs()
            self.show_text("任务已创建", job + "\n已冻结全部配置；选择任务后点击开始。")

        self.run(
            lambda: create_job(
                self.store,
                pid,
                batch=batch if scope == "batch" else None,
                ids=ids if scope == "ids" else None,
                changed=scope == "changed",
                **v,
            ),
            finished,
        )

    def build_jobs(self):
        row = self.bar(3)
        for text, fn in [
            ("开始 / 继续", self.engine.start),
            ("暂停", self.engine.pause),
            ("仅重试失败", self.engine.retry_failed),
        ]:
            self.button(
                row, text, lambda fn=fn: fn(self.required(self.job_table)["id"])
            )
        self.button(row, "取消任务", self.cancel_job)
        self.button(row, "调整剩余预算", self.job_budget)
        self.button(
            row,
            "更新任务密钥",
            lambda: refresh_credentials(
                self.store, self.vault, self.required(self.job_table)["id"]
            ),
        )
        self.button(row, "刷新", self.refresh_jobs)
        self.job_table = Table(
            [
                ("id", "任务编号"),
                ("state", "状态"),
                ("purpose", "用途"),
                ("total", "总量"),
                ("succeeded", "成功"),
                ("failed", "永久失败"),
                ("retry", "待重试"),
                ("pending", "等待"),
                ("inflight", "在途"),
                ("spent", "累计费用"),
                ("requests", "调用次数"),
            ]
        )
        self.pages[3].addWidget(self.job_table)
        self.button(self.pages[3], "查看错误诊断、调用与配置快照", self.job_detail)

    def refresh_jobs(self):
        rows = self.store.rows(
            "SELECT * FROM analysis_jobs ORDER BY created_at DESC LIMIT 100"
        )
        for row in rows:
            row.update(progress(self.store, row["id"]))
            row["spent"] = round(row["spent"], 6)
            row["purpose"] = (
                "回归验证"
                if json.loads(row["snapshot"]).get("evaluation_only")
                else "生产分析"
            )
        self.job_table.fill(rows)

    def cancel_job(self):
        job = self.required(self.job_table)["id"]
        if (
            QMessageBox.question(
                self, "取消任务", "停止新派发并取消未开始项？已完成结果保留。"
            )
            == QMessageBox.Yes
        ):
            self.engine.cancel(job)

    def job_budget(self):
        row = self.required(self.job_table)
        if row["state"] not in ("paused", "queued"):
            raise ValueError("请先暂停并等待在途完成")
        value, ok = QInputDialog.getDouble(
            self,
            "修改总预算",
            "总预算（不能小于已发生费用）",
            max(1, row["budget"]),
            row["spent"],
            1000000,
            6,
        )
        if ok:
            self.store.execute(
                "UPDATE analysis_jobs SET budget=? WHERE id=?", (value, row["id"])
            )

    def job_detail(self):
        row = self.required(self.job_table)
        errors = self.store.rows(
            "SELECT id,state,attempts,error,retry_at FROM job_items WHERE job_fk=? AND error IS NOT NULL LIMIT 100",
            (row["id"],),
        )
        calls = self.store.rows(
            "SELECT * FROM call_logs WHERE job_fk=? ORDER BY id DESC LIMIT 100",
            (row["id"],),
        )
        self.show_text(
            "任务诊断（最多 100 条）",
            dumps({"job": row, "errors": errors, "calls": calls}),
        )

    def build_results(self):
        row = self.bar(4)
        self.result_query = QLineEdit()
        self.result_query.setPlaceholderText("文章 ID / 标题")
        row.addWidget(self.result_query)
        self.rec_filter = QComboBox()
        self.rec_filter.addItem("全部建议", "")
        for t, v in RECS:
            self.rec_filter.addItem(t, v)
        row.addWidget(self.rec_filter)
        self.review_filter = QComboBox()
        self.review_filter.addItems(["全部复核状态", "待复核", "已复核"])
        row.addWidget(self.review_filter)
        self.validity_filter = QComboBox()
        self.validity_filter.addItem("全部有效性", "")
        for key in (
            "confirmed_issue",
            "suspected_issue",
            "no_issue_found",
            "insufficient_information",
        ):
            self.validity_filter.addItem(label(key), key)
        row.addWidget(self.validity_filter)
        self.button(row, "筛选", lambda: self.change_result_page(0, True))
        self.button(row, "导出 Excel", self.export)
        self.button(row, "本页条件批量复核", self.bulk_review_dialog)
        row2 = self.bar(4)
        self.result_language = QLineEdit()
        self.result_language.setPlaceholderText("语言")
        row2.addWidget(self.result_language)
        self.result_category = QLineEdit()
        self.result_category.setPlaceholderText("分类")
        row2.addWidget(self.result_category)
        self.result_job = QLineEdit()
        self.result_job.setPlaceholderText("任务 ID")
        row2.addWidget(self.result_job)
        self.scope_filter = QComboBox()
        self.scope_filter.addItem("全部范围", "")
        for key in ("full", "partial", "not_applicable", "unknown"):
            self.scope_filter.addItem(label(key), key)
        row2.addWidget(self.scope_filter)
        self.evidence_filter = QComboBox()
        self.evidence_filter.addItem("全部证据等级", "")
        for key in ("A", "B", "C"):
            self.evidence_filter.addItem(key, key)
        row2.addWidget(self.evidence_filter)
        self.result_table = Table(
            [
                ("id", "结果编号"),
                ("article_id", "文章 ID"),
                ("title", "标题"),
                ("language", "语言"),
                ("validity", "有效性"),
                ("scope", "范围"),
                ("rec", "建议"),
                ("review_count", "复核次数"),
                ("job_fk", "任务"),
            ]
        )
        self.pages[4].addWidget(self.result_table)
        self.result_table.cellDoubleClicked.connect(
            lambda *_: self.protect(self.result_detail)
        )
        row = self.bar(4)
        self.button(row, "证据定位与人工复核", self.result_detail)
        self.button(row, "上一页", lambda: self.change_result_page(-1))
        self.result_label = QLabel()
        row.addWidget(self.result_label)
        self.button(row, "下一页", lambda: self.change_result_page(1))

    def change_result_page(self, delta, reset=False):
        self.result_page = 0 if reset else max(0, self.result_page + delta)
        self.refresh_results()

    def refresh_results(self):
        query = """SELECT r.*,a.article_id,a.language,v.title,v.id version,i.job_fk,json_extract(r.result,'$.validity_status') validity,json_extract(r.result,'$.affected_scope') scope,json_extract(r.result,'$.recommendation') rec,(SELECT count(*) FROM reviews rv WHERE rv.result_fk=r.id) review_count FROM analysis_results r JOIN job_items i ON i.id=r.job_item_fk JOIN article_versions v ON v.id=i.article_version_fk JOIN articles a ON a.id=v.article_fk WHERE (a.article_id LIKE ? OR v.title LIKE ?) AND (?='' OR json_extract(r.result,'$.recommendation')=?)"""
        q = "%" + self.result_query.text() + "%"
        rec = self.rec_filter.currentData()
        params = [q, q, rec, rec]
        if self.review_filter.currentIndex() == 1:
            query += " AND NOT EXISTS(SELECT 1 FROM reviews rv WHERE rv.result_fk=r.id)"
        if self.review_filter.currentIndex() == 2:
            query += " AND EXISTS(SELECT 1 FROM reviews rv WHERE rv.result_fk=r.id)"
        for expression, value in [
            (
                "json_extract(r.result,'$.validity_status')",
                self.validity_filter.currentData(),
            ),
            (
                "json_extract(r.result,'$.affected_scope')",
                self.scope_filter.currentData(),
            ),
            ("a.language", self.result_language.text()),
            ("a.category", self.result_category.text()),
            ("i.job_fk", self.result_job.text()),
        ]:
            if value:
                query += " AND " + expression + "=?"
                params.append(value)
        if self.evidence_filter.currentData():
            query += " AND EXISTS(SELECT 1 FROM json_each(json_extract(r.result,'$.evidence')) ev WHERE json_extract(ev.value,'$.evidence_level')=?)"
            params.append(self.evidence_filter.currentData())
        query += " ORDER BY r.id DESC LIMIT 50 OFFSET ?"
        params.append(self.result_page * 50)
        self.result_table.fill(self.store.rows(query, params))
        self.result_label.setText(f"第 {self.result_page + 1} 页")

    def result_detail(self):
        row = self.required(self.result_table)
        a = self.store.article_version(row["version"])
        result = json.loads(row["result"])
        d = QDialog(self)
        d.setWindowTitle("证据核对与人工复核 · " + a["article_id"])
        d.resize(1200, 800)
        layout = QVBoxLayout(d)
        splitter = QSplitter()
        layout.addWidget(splitter)
        original = QPlainTextEdit()
        original.setReadOnly(True)
        original.setPlainText(a["title"] + "\n" + a["clean_content"])
        splitter.addWidget(original)
        analysis = QPlainTextEdit()
        analysis.setReadOnly(True)
        analysis.setPlainText(
            dumps(result)
            + "\n\n人工复核历史\n"
            + dumps(
                self.store.rows(
                    "SELECT * FROM reviews WHERE result_fk=? ORDER BY id DESC",
                    (row["id"],),
                )
            )
        )
        splitter.addWidget(analysis)
        evidence = QComboBox()
        for e in result["evidence"]:
            evidence.addItem(e["paragraph_id"] + " · " + e["quote"][:100], e)
        layout.addWidget(evidence)

        def locate():
            e = evidence.currentData()
            if e:
                # Qt positions count UTF-16 code units; convert Python offsets.
                text = original.toPlainText()
                start = len(text[: e["start"]].encode("utf-16-le")) // 2
                end = len(text[: e["end"]].encode("utf-16-le")) // 2
                from PySide6.QtGui import QTextCursor

                cursor = original.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                original.setTextCursor(cursor)
                original.setFocus()

        self.button(layout, "定位所选有效证据（无效引文不作为原文展示）", locate)
        form = QFormLayout()
        layout.addLayout(form)
        rec = QComboBox()
        for t, v in RECS:
            rec.addItem(t, v)
        index = rec.findData(result["recommendation"])
        rec.setCurrentIndex(index)
        reason = QPlainTextEdit()
        reason.setMaximumHeight(80)
        person = QLineEdit()
        form.addRow("人工结论", rec)
        form.addRow("复核理由（必填）", reason)
        form.addRow("复核人（必填）", person)
        make = QCheckBox("同时生成待审核案例（不会自动批准）")
        form.addRow(make)

        def save():
            review(
                self.store,
                row["id"],
                rec.currentData(),
                reason.toPlainText(),
                person.text(),
                make.isChecked(),
            )
            d.accept()
            self.refresh_results()

        self.button(layout, "保存人工复核", save)
        d.exec()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出全部治理结果", "", "Excel (*.xlsx)"
        )
        if path:
            self.run(
                lambda: export_results(self.store, path),
                lambda _: self.show_text("导出完成", path),
            )

    def build_cases(self):
        row = self.bar(5)
        self.case_search = QLineEdit()
        self.case_search.setPlaceholderText("案例文本搜索")
        row.addWidget(self.case_search)
        self.button(row, "搜索", self.refresh_cases)
        self.button(row, "手动录入", self.edit_case)
        self.button(
            row, "编辑所选", lambda: self.edit_case(self.required(self.case_table))
        )
        self.button(row, "批量导入 JSON", self.import_cases)
        self.case_table = Table(
            [
                ("id", "案例 ID"),
                ("version", "版本"),
                ("language", "语言"),
                ("scenario", "场景"),
                ("correct_recommendation", "正确结论"),
                ("status", "审核状态"),
                ("enabled", "已启用"),
                ("reviewer", "审核人"),
                ("usage_count", "参考次数"),
                ("reviewed_at", "最后核实时间"),
            ]
        )
        self.pages[5].addWidget(self.case_table)
        row = self.bar(5)
        self.button(row, "人工审核通过", self.approve)
        self.button(row, "启用 / 停用", self.toggle_case)
        self.button(row, "归档", self.archive_case)
        self.button(row, "版本与来源详情", self.case_detail)

    def refresh_cases(self):
        rows = self.store.rows(
            "SELECT cases.*,(SELECT count(*) FROM case_usage u WHERE u.case_id=cases.id) usage_count FROM cases WHERE content LIKE ? ORDER BY created_at DESC LIMIT 100",
            ("%" + self.case_search.text() + "%",),
        )
        for row in rows:
            content = json.loads(row["content"])
            row.update(
                {
                    key: content.get(key, "")
                    for key in (
                        "language",
                        "scenario",
                        "correct_recommendation",
                        "reviewer",
                        "reviewed_at",
                        "excerpt",
                        "reason",
                        "boundaries",
                        "author",
                    )
                }
            )
        self.case_table.fill(rows)

    def edit_case(self, row=None):
        d = Form("案例编辑（保存后重新待审核）", self)
        for key, title in [
            ("language", "语言"),
            ("scenario", "场景"),
            ("author", "录入人"),
        ]:
            d.line(key, title, (row or {}).get(key, ""))
        d.combo(
            "correct_recommendation",
            "正确人工结论",
            RECS,
            (row or {}).get("correct_recommendation"),
        )
        for key, title in [
            ("excerpt", "原文 / 必要片段"),
            ("reason", "判断依据"),
            ("boundaries", "适用与不适用边界"),
        ]:
            d.area(key, title, (row or {}).get(key, ""))
        d.buttons()
        if d.exec() == QDialog.Accepted:
            content = {**(json.loads(row["content"]) if row else {}), **d.values()}
            duplicates = self.store.one(
                "SELECT id FROM cases WHERE json_extract(content,'$.excerpt')=? AND id<>?",
                (content["excerpt"], row["id"] if row else ""),
            )
            if duplicates:
                raise ValueError(
                    "已有相同片段案例："
                    + duplicates["id"]
                    + "；请编辑已有案例，避免重复学习"
                )
            save_case(self.store, content, row["id"] if row else None)
            self.refresh_cases()

    def approve(self):
        row = self.required(self.case_table)
        person, ok = QInputDialog.getText(
            self, "人工审核", "确认案例结论和适用边界，填写审核人"
        )
        if ok:
            approve_case(self.store, row["id"], person)
            self.refresh_cases()

    def toggle_case(self):
        row = self.required(self.case_table)
        if row["status"] != "approved":
            raise ValueError("案例须先审核通过")
        if not row["enabled"]:
            path, _ = QFileDialog.getOpenFileName(
                self, "包含当前案例版本及基线的固定集回归报告", "", "JSON (*.json)"
            )
            if not path:
                return
            activate_case(
                self.store,
                row["id"],
                json.loads(Path(path).read_text(encoding="utf-8")),
            )
        else:
            self.store.execute("UPDATE cases SET enabled=0 WHERE id=?", (row["id"],))
        self.store.audit("case_toggle", row["id"], "案例启停")
        self.refresh_cases()

    def archive_case(self):
        row = self.required(self.case_table)
        self.store.execute(
            "UPDATE cases SET enabled=0,status='archived' WHERE id=?", (row["id"],)
        )
        self.store.audit("case_archive", row["id"], "归档保留版本")
        self.refresh_cases()

    def case_detail(self):
        row = self.required(self.case_table)
        self.show_text(
            "案例详情与版本",
            dumps(row)
            + "\n"
            + dumps(
                self.store.rows(
                    "SELECT * FROM case_history WHERE case_id=? ORDER BY version DESC",
                    (row["id"],),
                )
            ),
        )

    def import_cases(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入案例 JSON 数组", "", "JSON (*.json)"
        )
        if not path:
            return

        def action():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("应为案例对象数组")
            imported = []
            errors = []
            for number, row in enumerate(data, 1):
                try:
                    imported.append(save_case(self.store, row))
                except ValueError as error:
                    errors.append({"index": number, "reason": str(error)})
            return {"total": len(data), "pending_case_ids": imported, "errors": errors}

        self.run(
            action,
            lambda r: (self.refresh_cases(), self.show_text("待审核案例", dumps(r))),
        )

    def build_prompts(self):
        layout = self.pages[6]
        layout.addWidget(
            QLabel("在途任务使用冻结版本。编辑保存为草稿；发布需固定集回归与人工批准。")
        )
        self.prompt_table = Table(
            [
                ("id", "版本"),
                ("published", "当前发布"),
                ("note", "变更说明"),
                ("created_at", "创建时间"),
            ]
        )
        layout.addWidget(self.prompt_table)
        row = self.bar(6)
        self.button(row, "新版本 / 回滚为草稿", self.edit_prompt)
        self.button(row, "查看规则与提示词", self.prompt_detail)
        self.button(row, "导入回归报告并发布", self.publish)
        self.button(row, "选定文章 A/B 对比", self.ab_dialog)
        self.button(row, "用金标准集评估任务", self.evaluation_dialog)

    def refresh_prompts(self):
        self.prompt_table.fill(
            self.store.rows("SELECT * FROM prompt_versions ORDER BY id DESC LIMIT 100")
        )

    def prompt_detail(self):
        self.show_text("规则与提示词详情", dumps(self.required(self.prompt_table)))

    def edit_prompt(self):
        row = self.required(self.prompt_table)
        d = Form("保存新版本（也可复制旧版本回滚）", self)
        for key, title in [
            ("system", "系统提示词"),
            ("business", "业务提示词"),
            ("case_prompt", "案例提示词"),
            ("output", "输出提示词"),
            ("rules", "失效规则 JSON 数组"),
        ]:
            d.area(key, title, row[key])
        d.line("note", "变更说明")
        d.buttons()
        if d.exec() == QDialog.Accepted:
            v = d.values()
            v["rules"] = json.loads(v["rules"])
            save_prompt(self.store, **v)
            self.refresh_prompts()

    def publish(self):
        row = self.required(self.prompt_table)
        path, _ = QFileDialog.getOpenFileName(
            self, "固定标注集回归报告（JSON）", "", "JSON (*.json)"
        )
        if path:
            publish_prompt(
                self.store,
                row["id"],
                json.loads(Path(path).read_text(encoding="utf-8")),
            )
            self.refresh_prompts()

    def bulk_review_dialog(self):
        rows = self.result_table.records
        if not rows:
            raise ValueError("当前页无结果")
        d = Form("批量复核当前筛选页（仅本页）", self)
        d.box.addWidget(
            QLabel(
                f"筛选条件：标题/ID={self.result_query.text()}；建议={self.rec_filter.currentText()}；复核状态={self.review_filter.currentText()}；共 {len(rows)} 条。"
            )
        )
        d.combo(
            "recommendation",
            "人工结论",
            [(t, v) for t, v in RECS if v != "review_removal"],
        )
        d.area("reason", "统一理由（必须适用全部所选记录）")
        d.line("reviewer", "复核人")
        d.check(
            "conditions_confirmed", "我已逐项核对本页记录，确认筛选条件及统一理由适用"
        )
        d.buttons()
        if d.exec() == QDialog.Accepted:
            bulk_review(self.store, [r["id"] for r in rows], **d.values())
            self.refresh_results()

    def ab_dialog(self):
        profiles = self.store.rows(
            "SELECT * FROM model_profiles WHERE tested_hash IS NOT NULL"
        )
        prompts = self.store.rows(
            "SELECT id,note FROM prompt_versions ORDER BY id DESC"
        )
        if not profiles:
            raise ValueError("请先保存并在线测试模型")
        d = Form("选定文章 A/B 提示词对照（真实调用可能计费）", self)
        d.combo("profile_id", "模型", [(p["name"], p["id"]) for p in profiles])
        for key, title in [("prompt_a", "提示词 A"), ("prompt_b", "提示词 B")]:
            d.combo(
                key, title, [(str(p["id"]) + " " + p["note"], p["id"]) for p in prompts]
            )
        d.line("article_ids", "文章 ID（逗号分隔，最多 10 篇）")
        d.line("candidate_case_ids", "仅 B 组候选案例 ID（可选，逗号分隔）")
        d.number("budget", "每组预算", 1, 0.000001, 1000000, True)
        d.check("consent", "授权向所选企业批准的模型发送这些文章及审核案例")
        d.buttons()
        if d.exec() == QDialog.Accepted:
            values = d.values()
            values["candidate_case_ids"] = [
                x.strip() for x in values["candidate_case_ids"].split(",") if x.strip()
            ]
            values["article_ids"] = [
                x.strip() for x in values["article_ids"].split(",") if x.strip()
            ]
            self.run(
                lambda: compare_prompts(self.store, self.engine, **values),
                lambda results: self.show_text(
                    "A/B 结构化结果与证据校验（A 在前，B 在后）", dumps(results)
                ),
            )

    def evaluation_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "固定金标准 JSON：article_id / validity_status", "", "JSON (*.json)"
        )
        if not path:
            return
        jobs = self.store.rows(
            "SELECT id FROM analysis_jobs ORDER BY created_at DESC LIMIT 100"
        )
        if not jobs:
            raise ValueError("先对固定标注集创建任务并完成分析")
        d = Form("计算真实已完成结果的回归指标", self)
        d.combo("job", "分析任务", [(j["id"], j["id"]) for j in jobs])
        d.combo(
            "baseline_job",
            "对照基线任务",
            [("未选择", None)] + [(j["id"], j["id"]) for j in jobs],
        )
        d.line("reviewer", "批准人员（确认阈值与标注质量）")
        d.number("threshold", "F1 最低阈值", 0.9, 0, 1, True)
        d.buttons()
        if d.exec() != QDialog.Accepted:
            return
        gold = json.loads(Path(path).read_text(encoding="utf-8"))
        report = evaluate_job(self.store, gold=gold, **d.values())
        self.show_text("回归评估报告（缺失与虚构证据不放行）", dumps(report))
        destination, _ = QFileDialog.getSaveFileName(
            self, "保存可追踪的回归报告", "", "JSON (*.json)"
        )
        if destination:
            Path(destination).write_text(dumps(report), encoding="utf-8")

    def build_settings(self):
        layout = self.pages[7]
        layout.addWidget(
            QLabel(
                "模型地址填写 base_url（如 https://公司批准地址/v1）或完整 chat/completions endpoint。\n密钥由 Windows DPAPI 加密，界面不回显；在线测试会发出一次最小请求，不发送文章。"
            )
        )
        self.model_table = Table(
            [
                ("id", "配置 ID"),
                ("name", "名称"),
                ("base_url", "API 地址"),
                ("model", "模型"),
                ("key_saved", "密钥已保存"),
                ("test_state", "在线测试"),
            ]
        )
        layout.addWidget(self.model_table)
        row = self.bar(7)
        self.button(row, "新增模型", self.edit_model)
        self.button(
            row, "编辑模型", lambda: self.edit_model(self.required(self.model_table))
        )
        self.button(row, "真实在线连接测试", self.test_model)
        self.button(
            row,
            "测试详情",
            lambda: self.show_text(
                "模型测试报告",
                self.required(self.model_table)["test_report"] or "未测试",
            ),
        )
        row = self.bar(7)
        self.button(row, "备份数据库", self.backup)
        self.button(row, "恢复数据库", self.restore)
        self.button(row, "导出脱敏日志", self.export_logs)
        self.button(row, "运行环境诊断", self.diagnostics)
        layout.addWidget(
            QLabel(
                "隐私：不联网检索、不含遥测；每次任务要求确认外发授权。备份含敏感文章，请保存在授权目录。\n数据库备份不含 DPAPI 密钥文件；跨 Windows 用户迁移后需重新输入密钥。"
            )
        )

    def refresh_models(self):
        rows = self.store.rows("SELECT * FROM model_profiles")
        for row in rows:
            row["key_saved"] = "是" if self.vault.has(row["key_ref"]) else "否"
            row["test_state"] = "已通过" if row["tested_hash"] else "未通过 / 待测试"
        self.model_table.fill(rows)

    def edit_model(self, row=None):
        opts = json.loads(row["options"]) if row else {}
        d = Form("模型配置 · 安全保存", self)
        for key, title in [
            ("name", "配置名称"),
            ("base_url", "API Base URL 或完整 endpoint"),
            ("model", "模型 ID"),
        ]:
            d.line(key, title, (row or {}).get(key, ""))
        d.line("key", "API Key（编辑时留空保留；不回显）", "", True)
        d.number("timeout", "请求超时（秒）", opts.get("timeout", 60), 1, 300)
        d.number(
            "context_chars",
            "可用输入上下文字符预算（保守配置）",
            opts.get("context_chars", 24000),
            4000,
            1000000,
        )
        d.number(
            "max_output_tokens",
            "最大输出 Token",
            opts.get("max_output_tokens", 2048),
            256,
            32000,
        )
        d.number(
            "input_price",
            "每百万输入 Token 价格",
            opts.get("input_price", 0),
            0,
            1000000,
            True,
        )
        d.number(
            "output_price",
            "每百万输出 Token 价格",
            opts.get("output_price", 0),
            0,
            1000000,
            True,
        )
        d.check(
            "pricing_confirmed",
            "我已核实价格与计价币种；免费模型可填 0",
            opts.get("pricing_confirmed", False),
        )
        d.buttons()
        if d.exec() != QDialog.Accepted:
            return
        values = d.values()
        base = {k: values.pop(k) for k in ("name", "base_url", "model", "key")}
        save_profile(
            self.store,
            self.vault,
            **base,
            options=values,
            profile_id=row["id"] if row else None,
        )
        self.refresh_models()
        self.refresh_profiles()

    def test_model(self):
        row = self.required(self.model_table)
        self.run(
            lambda: test_connection(self.store, self.vault, row["id"]),
            lambda report: (
                self.refresh_models(),
                self.show_text("真实在线测试", dumps(report)),
            ),
        )

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "数据库备份（包含文章，请妥善保存）", "", "SQLite (*.sqlite3)"
        )
        if path:
            self.run(lambda: str(self.store.backup(path)))

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据库备份", "", "SQLite (*.sqlite3)"
        )
        if (
            path
            and QMessageBox.question(
                self, "恢复确认", "将替换业务数据库，当前数据库会先自动备份。确认恢复？"
            )
            == QMessageBox.Yes
        ):
            self.run(
                lambda: self.store.restore(path),
                lambda _: (
                    self.refresh(),
                    self.show_text("恢复完成", "已恢复；请核对模型密钥和数据。"),
                ),
            )

    def export_logs(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出脱敏操作日志", "", "JSON (*.json)"
        )
        if path:
            Path(path).write_text(
                dumps(
                    self.store.rows(
                        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT 5000"
                    )
                ),
                encoding="utf-8",
            )

    def diagnostics(self):
        check = self.store.one("PRAGMA quick_check")
        self.show_text(
            "运行环境诊断",
            dumps(
                {
                    "version": "0.1.0",
                    "platform": sys.platform,
                    "frozen_runtime": bool(getattr(sys, "frozen", False)),
                    "database": str(self.store.path),
                    "integrity": check,
                    "local_tcp_listener": False,
                    "key_storage": "Windows DPAPI CurrentUser"
                    if os.name == "nt"
                    else "仅测试平台，禁止保存生产密钥",
                    "privacy": "无遥测；不自动调用模型或执行上下架",
                }
            ),
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(
            u.toLocalFile().lower().endswith(".xlsx") for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.nav.setCurrentRow(1)
        self.import_next([u.toLocalFile() for u in event.mimeData().urls()])
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self.workers:
            self.error("文件或网络操作仍在处理中，请等待完成后关闭")
            event.ignore()
            return
        live = any(t.is_alive() for t in self.engine.threads.values())
        if live:
            for job, thread in list(self.engine.threads.items()):
                if thread.is_alive():
                    self.engine.pause(job)
            QMessageBox.information(
                self,
                "正在安全暂停",
                "已停止新派发，请等待在途请求完成、任务显示已暂停后再次关闭。",
            )
            event.ignore()
            return
        self.engine.close()
        self.store.close_connection()
        event.accept()


STYLE = """QWidget {background:#f5f7fb;color:#233247;} QMainWindow,QDialog {background:#f5f7fb;} QLabel#title {font-size:25px;font-weight:600;padding:12px 0;} QLabel#stats {background:#e6eefb;padding:24px;font-size:18px;line-height:1.8;} QPushButton {background:#2456a6;color:white;border:0;border-radius:5px;padding:9px 14px;} QPushButton:hover {background:#346bbb;} QLineEdit,QPlainTextEdit,QComboBox,QSpinBox,QDoubleSpinBox {background:white;border:1px solid #ccd5e4;border-radius:4px;padding:7px;} QListWidget {background:#172b4d;color:white;border:0;padding:8px;} QListWidget::item {padding:14px 6px;} QListWidget::item:selected {background:#2b5590;border-radius:5px;} QTableWidget {background:white;alternate-background-color:#eef3f9;gridline-color:#e2e7ee;} QHeaderView::section {background:#e5ebf4;padding:9px;border:0;font-weight:bold;}"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GKM")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)
    try:
        data_directory = Path(
            os.environ.get("GKM_DATA_DIR")
            or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / "GKM"
        )
        data_directory.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data_directory / "app.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            raise RuntimeError("此数据目录已由另一 GKM 窗口使用，请切回已打开的窗口")
        store = Store(data_directory)
        vault = Vault(store.directory)
        engine = Engine(store, vault)
        engine.recover()
        window = Window(store, vault, engine)
        window.show()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "GKM 启动诊断",
            f"启动失败：{type(exc).__name__}\n"
            + str(exc)
            + "\n请检查数据目录权限，勿删除已有数据库。",
        )
        return 1
