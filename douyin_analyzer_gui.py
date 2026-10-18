"""Desktop interface with an embedded browser for Douyin metric collection."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, QUrl, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from douyin_analyzer import (
    VideoMetric,
    normalize_profile_url,
    parse_detail_like_count,
    parse_video_cards,
    save_snapshot,
    video_collection_matches_declared_count,
)


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
DATABASE_PATH = DATA_DIR / "douyin_metrics.sqlite"


def _configure_data_directory() -> None:
    global DATA_DIR, DATABASE_PATH
    if getattr(sys, "frozen", False):
        local_data_path = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        if not local_data_path:
            raise RuntimeError("无法确定 Windows 本地应用数据目录。")
        DATA_DIR = Path(local_data_path)
    else:
        DATA_DIR = PROJECT_DIR / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH = DATA_DIR / "douyin_metrics.sqlite"
PROFILE_WORKS_SCRIPT = """
JSON.stringify((() => {
    const tab = Array.from(document.querySelectorAll('[role="tab"]'))
        .find(item =>
            item.querySelector('[data-e2e="user-tab-count"]') &&
            (item.innerText || '').includes('作品')
        );
    if (!tab) return {error: '作品总数未显示'};
    const countText = tab.querySelector('[data-e2e="user-tab-count"]')
        ?.innerText?.trim().replace(/,/g, '') || '';
    const expectedCount = /^\\d+$/.test(countText) ? Number(countText) : null;
    let worksContainer = tab;
    while (worksContainer && !worksContainer.querySelector("a[href*='/video/']")) {
        worksContainer = worksContainer.parentElement;
    }
    const firstAnchor = worksContainer?.querySelector("a[href*='/video/']");
    const worksList = firstAnchor?.closest('ul') || worksContainer;
    const anchors = Array.from(worksList?.querySelectorAll("a[href*='/video/']") || []);
    const videos = anchors.map(anchor => {
        let node = anchor;
        let cardText = anchor.innerText || '';
        for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
            const text = (node.innerText || '').trim();
            if (text && text.length <= 1200) {
                cardText = text;
                if (/(播放|观看|点赞|获赞|like|view)/i.test(text)) break;
            }
        }
        let playCount = '';
        for (const value of anchor.querySelectorAll('span')) {
            const count = (value.innerText || '').trim();
            const icon = value.previousElementSibling;
            const path = icon?.querySelector('svg path')?.getAttribute('d') || '';
            if (/^\\d+(?:\\.\\d+)?(?:万|亿|千|[kKmM])?$/.test(count) &&
                icon?.getAttribute('role') === 'img' &&
                /^M[^L]*L/.test(path)) {
                playCount = count;
                break;
            }
        }
        return {
            href: anchor.href,
            title: anchor.querySelector('p')?.innerText ||
                anchor.getAttribute('title') ||
                anchor.getAttribute('aria-label') || anchor.innerText || '',
            text: cardText,
            play_count: playCount
        };
    });
    let scroller = worksList;
    while (scroller && scroller !== document.body &&
        (scroller.clientHeight <= 0 ||
         scroller.scrollHeight <= scroller.clientHeight + 20)) {
        scroller = scroller.parentElement;
    }
    if (!scroller || scroller === document.body) {
        scroller = document.scrollingElement;
    }
    return {
        expected_count: expectedCount,
        videos,
        scroll_top: scroller?.scrollTop || 0,
        scroll_height: scroller?.scrollHeight || 0,
        client_height: scroller?.clientHeight || 0
    };
})())
"""
SCROLL_PROFILE_WORKS_SCRIPT = """
(() => {
    const tab = Array.from(document.querySelectorAll('[role="tab"]'))
        .find(item =>
            item.querySelector('[data-e2e="user-tab-count"]') &&
            (item.innerText || '').includes('作品')
        );
    let worksContainer = tab;
    while (worksContainer && !worksContainer.querySelector("a[href*='/video/']")) {
        worksContainer = worksContainer.parentElement;
    }
    const firstAnchor = worksContainer?.querySelector("a[href*='/video/']");
    const worksList = firstAnchor?.closest('ul') || worksContainer;
    let scroller = worksList;
    while (scroller && scroller !== document.body &&
        (scroller.clientHeight <= 0 ||
         scroller.scrollHeight <= scroller.clientHeight + 20)) {
        scroller = scroller.parentElement;
    }
    if (!scroller || scroller === document.body) {
        scroller = document.scrollingElement;
    }
    if (!scroller) return false;
    const step = Math.max(300, Math.floor(scroller.clientHeight * 0.75));
    scroller.scrollTop = Math.min(
        scroller.scrollTop + step,
        scroller.scrollHeight
    );
    return true;
})()
"""
VIDEO_LIKE_SCRIPT = """
document.querySelector('[data-e2e="video-player-digg"]')?.innerText?.trim() || ''
"""


class AnalyzerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("抖音视频数据分析")
        self.resize(1520, 940)
        self.setMinimumSize(1120, 720)
        self._is_collecting = False
        self._capture_in_progress = False
        self._capture_attempts = 0
        self._max_capture_attempts = 200
        self._profile_expected_count: int | None = None
        self._profile_videos: dict[str, VideoMetric] = {}
        self._profile_previous_count = 0
        self._profile_no_progress_attempts = 0
        self._max_profile_no_progress_attempts = 8
        self._like_scan_active = False
        self._returning_to_profile = False
        self._profile_return_attempts = 0
        self._max_profile_return_attempts = 2
        self._restoring_profile = False
        self._like_scan_profile_url = ""
        self._like_scan_videos: list[VideoMetric] = []
        self._like_scan_results: dict[str, int | None] = {}
        self._like_scan_index = 0
        self._like_scan_attempts = 0
        self._like_scan_delay_ms = 1_500
        self._displayed_videos: list[VideoMetric] = []
        self.settings = QSettings()

        self.browser = QWebEngineView()
        self.web_profile = QWebEngineProfile("DouyinAnalyzerProfile", self)
        profile_root = DATA_DIR / "browser-profile"
        (profile_root / "storage").mkdir(parents=True, exist_ok=True)
        (profile_root / "cache").mkdir(parents=True, exist_ok=True)
        self.web_profile.setPersistentStoragePath(str(profile_root / "storage"))
        self.web_profile.setCachePath(str(profile_root / "cache"))
        self.web_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.browser.setPage(QWebEnginePage(self.web_profile, self.browser))
        self.browser.urlChanged.connect(self._sync_address)
        self.browser.loadFinished.connect(self._page_loaded)

        self.address = QLineEdit("https://www.douyin.com/")
        self.address.setPlaceholderText("输入抖音个人主页网址")
        self.address.returnPressed.connect(self._navigate)
        saved_username = self.settings.value("login/username", "")
        self.username = QLineEdit()
        self.username.setPlaceholderText("可选：输入登录用户名（不会读取或保存密码）")
        self.username.setText(saved_username if isinstance(saved_username, str) else "")
        self.username.editingFinished.connect(self._save_remembered_username)
        self.remember_username = QCheckBox("记住用户名")
        self.remember_username.setChecked(bool(self.username.text()))
        self.remember_username.toggled.connect(self._remember_username_changed)
        self.clear_login_button = QPushButton("清除记忆登录")
        self.clear_login_button.clicked.connect(self._clear_saved_login)
        self.clock = QLabel()
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock.setStyleSheet("font-size: 18px; font-weight: 600;")

        self.back_button = QPushButton("←")
        self.back_button.setObjectName("navigationButton")
        self.back_button.setToolTip("后退")
        self.back_button.clicked.connect(self.browser.back)
        self.forward_button = QPushButton("→")
        self.forward_button.setObjectName("navigationButton")
        self.forward_button.setToolTip("前进")
        self.forward_button.clicked.connect(self.browser.forward)
        self.reload_button = QPushButton("刷新")
        self.reload_button.setObjectName("navigationButton")
        self.reload_button.clicked.connect(self.browser.reload)
        self.go_button = QPushButton("打开主页")
        self.go_button.setObjectName("secondaryButton")
        self.go_button.clicked.connect(self._navigate)

        self.interval = QSpinBox()
        self.interval.setRange(30, 86_400)
        self.interval.setValue(300)
        self.interval.setSuffix(" 秒")
        self.start_button = QPushButton("开始采集")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._start_collection)
        self.stop_button = QPushButton("停止采集")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_collection)

        self.status = QLabel("输入抖音个人主页网址，打开主页后即可开始采集。")
        self.status.setObjectName("statusLabel")
        self.metrics_summary = QLabel("尚无采集数据")
        self.metrics_summary.setObjectName("summaryLabel")
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.scan_progress.setFormat("%v / %m")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["视频标题", "视频 ID", "播放量", "点赞量"])
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 4):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setObjectName("secondaryButton")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export_results)

        self._collection_timer = QTimer(self)
        self._collection_timer.timeout.connect(self._collect_from_page)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1_000)
        self._update_clock()

        self._build_layout()

    def _build_layout(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 12)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title = QLabel("抖音视频数据采集")
        title.setObjectName("appTitle")
        heading.addWidget(title)
        subtitle = QLabel("主页作品 · 播放量 · 点赞量")
        subtitle.setObjectName("mutedLabel")
        heading.addWidget(subtitle)
        heading.addStretch(1)
        heading.addWidget(QLabel("本地时间"))
        heading.addWidget(self.clock)
        layout.addLayout(heading)

        profile_panel = QGroupBox("采集设置")
        profile_layout = QVBoxLayout(profile_panel)
        profile_layout.setSpacing(10)
        navigation = QHBoxLayout()
        navigation.setSpacing(6)
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.forward_button)
        navigation.addWidget(self.reload_button)
        navigation.addWidget(self.address, 1)
        navigation.addWidget(self.go_button)
        profile_layout.addLayout(navigation)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("采集间隔"))
        controls.addWidget(self.interval)
        controls.addSpacing(12)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        controls.addWidget(QLabel("登录用户名"))
        controls.addWidget(self.username)
        controls.addWidget(self.remember_username)
        controls.addWidget(self.clear_login_button)
        profile_layout.addLayout(controls)
        layout.addWidget(profile_panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(self.browser)

        results = QFrame()
        results.setObjectName("dataPanel")
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(16, 14, 16, 14)
        results_layout.setSpacing(10)
        list_heading = QHBoxLayout()
        list_title = QLabel("作品数据列表")
        list_title.setObjectName("sectionTitle")
        list_heading.addWidget(list_title)
        list_heading.addStretch(1)
        list_heading.addWidget(self.export_button)
        results_layout.addLayout(list_heading)
        results_layout.addWidget(self.metrics_summary)
        results_layout.addWidget(self.scan_progress)
        results_layout.addWidget(self.table, 1)
        splitter.addWidget(results)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([760, 650])
        layout.addWidget(splitter, 1)

        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f3f6fb;
                color: #1e293b;
                font-family: "Segoe UI", "Microsoft YaHei UI";
                font-size: 10pt;
            }
            QGroupBox, #dataPanel {
                background: #ffffff;
                border: 1px solid #dce4ef;
                border-radius: 12px;
            }
            QGroupBox {
                margin-top: 10px;
                padding: 12px 12px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 5px;
                color: #475569;
            }
            #appTitle {
                color: #12223b;
                font-size: 20px;
                font-weight: 700;
            }
            #sectionTitle {
                font-size: 15px;
                font-weight: 700;
                color: #12223b;
            }
            #mutedLabel, #summaryLabel {
                color: #64748b;
            }
            #statusLabel {
                padding: 7px 11px;
                color: #475569;
                background: #eaf0f8;
                border-radius: 7px;
            }
            QLineEdit, QSpinBox {
                min-height: 34px;
                padding: 0 9px;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                selection-background-color: #2563eb;
            }
            QLineEdit:focus, QSpinBox:focus {
                border: 1px solid #3b82f6;
            }
            QPushButton {
                min-height: 34px;
                padding: 0 12px;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                background: #ffffff;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f1f5f9;
            }
            QPushButton:disabled {
                color: #94a3b8;
                background: #e2e8f0;
            }
            #navigationButton {
                min-width: 38px;
                padding: 0;
            }
            #primaryButton {
                color: #ffffff;
                background: #2563eb;
                border-color: #2563eb;
            }
            #primaryButton:hover {
                background: #1d4ed8;
            }
            #dangerButton {
                color: #b42318;
                border-color: #fecaca;
                background: #fff7f7;
            }
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                gridline-color: #edf1f6;
                selection-background-color: #dbeafe;
                selection-color: #172554;
            }
            QHeaderView::section {
                min-height: 34px;
                padding: 0 8px;
                color: #475569;
                background: #f1f5f9;
                border: none;
                border-bottom: 1px solid #dce4ef;
                font-weight: 700;
            }
            QProgressBar {
                max-height: 14px;
                color: #475569;
                background: #e2e8f0;
                border: none;
                border-radius: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #3b82f6;
                border-radius: 6px;
            }
            QSplitter::handle {
                background: transparent;
            }
            """
        )
        self.browser.setUrl(QUrl("https://www.douyin.com/"))

    def _update_clock(self) -> None:
        from datetime import datetime

        self.clock.setText(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"))

    def _navigate(self) -> None:
        text = self.address.text().strip()
        if not text:
            QMessageBox.warning(self, "网址为空", "请输入抖音个人主页网址。")
            return
        if "://" not in text:
            text = f"https://{text}"
        try:
            profile_url = normalize_profile_url(text)
        except ValueError as error:
            QMessageBox.warning(self, "网址无效", str(error))
            return
        self.address.setText(profile_url)
        self.browser.setUrl(QUrl(profile_url))
        self.status.setText("正在打开主页……")

    def _sync_address(self, url: QUrl) -> None:
        self.address.setText(url.toString())

    def _remember_username_changed(self, checked: bool) -> None:
        if checked:
            self._save_remembered_username()
        else:
            self.settings.remove("login/username")

    def _save_remembered_username(self) -> None:
        if not self.remember_username.isChecked():
            return
        username = self.username.text().strip()
        if username:
            self.settings.setValue("login/username", username)
        else:
            self.settings.remove("login/username")

    def _clear_saved_login(self) -> None:
        answer = QMessageBox.question(
            self,
            "清除记忆登录",
            "这将清除已记住的用户名，并删除内置浏览器的登录 Cookie 和当前网页站点存储。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._stop_collection()
        self.settings.remove("login/username")
        self.username.clear()
        self.remember_username.setChecked(False)
        self.web_profile.cookieStore().deleteAllCookies()
        self.web_profile.clearHttpCache()
        self.browser.page().runJavaScript(
            """(() => {
                try {
                    localStorage.clear();
                    sessionStorage.clear();
                    return true;
                } catch (error) {
                    return false;
                }
            })()""",
            self._finish_clear_login,
        )

    def _finish_clear_login(self, storage_cleared: object) -> None:
        if storage_cleared is True:
            self.status.setText(
                "已清除记住的用户名、浏览器 Cookie、缓存和当前站点存储。"
            )
        else:
            self.status.setText(
                "已清除记住的用户名、浏览器 Cookie 和缓存；"
                "当前页面站点存储未能清除，请打开抖音页面后重试。"
            )

    def _page_loaded(self, success: bool) -> None:
        if self._restoring_profile:
            self._restoring_profile = False
            return
        if self._returning_to_profile:
            if not self._is_on_profile_url(self._like_scan_profile_url):
                return
            if success:
                self._returning_to_profile = False
                self._profile_return_attempts = 0
                QTimer.singleShot(
                    self._like_scan_delay_ms,
                    self._open_next_video_for_like_scan,
                )
                return
            self._profile_return_attempts += 1
            if self._profile_return_attempts <= self._max_profile_return_attempts:
                self.status.setText(
                    "返回主页失败，正在重试后再继续采集……"
                )
                QTimer.singleShot(
                    1_000,
                    lambda: self.browser.setUrl(
                        QUrl(self._like_scan_profile_url)
                    ),
                )
                return
            self._returning_to_profile = False
            self.status.setText(
                "多次尝试仍无法返回主页，已停止后续视频采集。"
            )
            self._finish_video_like_scan_batch()
            return
        if self._like_scan_active:
            video = self._current_video_for_like_scan()
            if video is None:
                return
            if not success:
                self._finish_video_like_scan(video.video_id, None)
                return
            QTimer.singleShot(
                1_200,
                lambda: self._read_video_like_count(video.video_id),
            )
            return
        if not success:
            self.status.setText("网页加载失败。请检查网络，或在内置浏览器中重新打开主页。")
            return
        if self._is_collecting:
            QTimer.singleShot(700, self._collect_from_page)

    def _start_collection(self) -> None:
        try:
            profile_url = normalize_profile_url(self.address.text().strip())
        except ValueError as error:
            QMessageBox.warning(self, "网址无效", str(error))
            return

        self._is_collecting = True
        self._capture_attempts = 0
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._collection_timer.start(self.interval.value() * 1_000)
        if self.browser.url().toString() != profile_url:
            self.browser.setUrl(QUrl(profile_url))
        else:
            self._collect_from_page()
        self.status.setText("采集已启动；首次采集将读取当前主页。")

    def _stop_collection(self) -> None:
        was_scanning_likes = self._like_scan_active
        profile_url = self._like_scan_profile_url
        if was_scanning_likes:
            self.scan_progress.setRange(
                0, max(len(self._like_scan_videos), 1)
            )
            self.scan_progress.setValue(self._like_scan_index)
        self._like_scan_active = False
        self._returning_to_profile = False
        self._like_scan_videos = []
        self._like_scan_results = {}
        self._like_scan_index = 0
        self._like_scan_profile_url = ""
        self._is_collecting = False
        self._capture_in_progress = False
        self._collection_timer.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if was_scanning_likes and profile_url:
            self._restoring_profile = True
            self.browser.setUrl(QUrl(profile_url))
        self.status.setText("采集已停止。已保存的历史采集记录仍保留。")

    def _collect_from_page(self) -> None:
        if not self._is_collecting or self._capture_in_progress:
            return
        try:
            profile_url = normalize_profile_url(self.browser.url().toString())
        except ValueError as error:
            self.status.setText(f"{error} 请在上方地址栏输入个人主页链接。")
            return

        self._capture_in_progress = True
        self._capture_attempts = 0
        self._profile_expected_count = None
        self._profile_videos = {}
        self._profile_previous_count = 0
        self._profile_no_progress_attempts = 0
        self.status.setText("正在读取主页作品总数并遍历作品列表……")
        self._read_profile_works(profile_url)

    def _schedule_profile_read(self, profile_url: str, scroll_succeeded: object) -> None:
        if not self._is_collecting or self.browser.url().toString() != profile_url:
            self._capture_in_progress = False
            return
        if scroll_succeeded is not True:
            self._capture_in_progress = False
            self.status.setText("无法滚动主页作品列表；请刷新主页后重试。")
            return
        QTimer.singleShot(900, lambda: self._read_profile_works(profile_url))

    def _read_profile_works(self, profile_url: str) -> None:
        if not self._is_collecting or self.browser.url().toString() != profile_url:
            self._capture_in_progress = False
            return
        self._capture_attempts += 1
        self.status.setText("正在逐屏收集主页作品……")
        self.browser.page().runJavaScript(
            PROFILE_WORKS_SCRIPT,
            lambda cards: self._finish_collection(profile_url, cards),
        )

    def _finish_collection(self, profile_url: str, result: object) -> None:
        if not self._is_collecting:
            return
        if self.browser.url().toString() != profile_url:
            self._capture_in_progress = False
            self.status.setText("页面已切换；请在目标抖音主页重新开始采集。")
            return
        if not isinstance(result, str):
            self._capture_in_progress = False
            result_type = type(result).__name__
            self.status.setText(
                f"网页脚本未返回有效文本（{result_type}）。"
                "请刷新主页后重试；如持续出现，请提供此提示。"
            )
            return
        try:
            cards = json.loads(result)
        except json.JSONDecodeError as error:
            self._capture_in_progress = False
            self.status.setText(f"网页脚本返回的数据格式无效：{error.msg}。请刷新后重试。")
            return
        if not isinstance(cards, dict):
            self._capture_in_progress = False
            self.status.setText("网页脚本返回的数据格式不正确。请刷新主页后重试。")
            return
        expected_count = cards.get("expected_count")
        card_items = cards.get("videos")
        if not isinstance(expected_count, int) or expected_count < 0:
            self._capture_in_progress = False
            self.status.setText(
                "无法读取主页显示的作品总数；为避免数据不完整，本次未开始采集。"
            )
            return
        if not isinstance(card_items, list):
            self._capture_in_progress = False
            self.status.setText("主页作品列表格式不正确，请刷新主页后重试。")
            return
        if self._profile_expected_count is None:
            self._profile_expected_count = expected_count
        elif self._profile_expected_count != expected_count:
            self._capture_in_progress = False
            self.status.setText(
                f"采集期间主页作品总数从 {self._profile_expected_count} 变为 "
                f"{expected_count}；为确保数量一致，已取消本次采集。"
            )
            return
        valid_cards = [
            card
            for card in card_items
            if isinstance(card, dict)
            and isinstance(card.get("href"), str)
            and isinstance(card.get("title", ""), str)
            and isinstance(card.get("text", ""), str)
            and isinstance(card.get("play_count", ""), str)
        ]
        for video in parse_video_cards(valid_cards):
            self._profile_videos[video.video_id] = video

        collected_count = len(self._profile_videos)
        if collected_count > expected_count:
            self._capture_in_progress = False
            self.status.setText(
                f"读取到 {collected_count} 条不同视频，超过主页显示的 "
                f"{expected_count} 条作品；可能混入其他列表，已取消本次采集。"
            )
            return
        if video_collection_matches_declared_count(
            expected_count, list(self._profile_videos.values())
        ):
            self.status.setText(f"作品数量核对通过：{collected_count}/{expected_count}。")
            self._start_video_like_scan(
                profile_url, list(self._profile_videos.values())
            )
            return
        if collected_count == self._profile_previous_count:
            self._profile_no_progress_attempts += 1
        else:
            self._profile_no_progress_attempts = 0
        self._profile_previous_count = collected_count
        if (
            self._capture_attempts >= self._max_capture_attempts
            or self._profile_no_progress_attempts
            >= self._max_profile_no_progress_attempts
        ):
            self._capture_in_progress = False
            self.status.setText(
                f"主页显示 {expected_count} 条作品，但目前只遍历到 "
                f"{collected_count} 条不同视频。为避免少采或重复，已取消本次采集；"
                "请确认作品列表滚动到底且所有作品可见后重试。"
            )
            return
        self.status.setText(
            f"正在遍历作品列表：{collected_count}/{expected_count}；继续向下加载……"
        )
        self.browser.page().runJavaScript(
            SCROLL_PROFILE_WORKS_SCRIPT,
            lambda scrolled: self._schedule_profile_read(profile_url, scrolled),
        )

    def _start_video_like_scan(
        self, profile_url: str, videos: list[VideoMetric]
    ) -> None:
        self._like_scan_active = True
        self._like_scan_profile_url = profile_url
        self._like_scan_videos = videos
        self._like_scan_results = {}
        self._like_scan_index = 0
        self._like_scan_attempts = 0
        self._displayed_videos = list(videos)
        self._update_results(videos)
        self.scan_progress.setRange(0, max(len(videos), 1))
        self.scan_progress.setValue(0)
        self.export_button.setEnabled(bool(videos))
        self.status.setText(
            f"主页已识别 {len(videos)} 条视频；正在逐条打开视频读取点赞量……"
        )
        self._open_next_video_for_like_scan()

    def _current_video_for_like_scan(self) -> VideoMetric | None:
        if not self._like_scan_active:
            return None
        if self._like_scan_index >= len(self._like_scan_videos):
            return None
        return self._like_scan_videos[self._like_scan_index]

    def _open_next_video_for_like_scan(self) -> None:
        if not self._like_scan_active:
            return
        if self._like_scan_index >= len(self._like_scan_videos):
            self._finish_video_like_scan_batch()
            return
        video = self._like_scan_videos[self._like_scan_index]
        self._like_scan_attempts = 0
        self.status.setText(
            f"正在读取点赞量：{self._like_scan_index + 1}/"
            f"{len(self._like_scan_videos)}"
        )
        video_url = f"https://www.douyin.com/video/{video.video_id}"
        self.browser.setUrl(QUrl(video_url))
        QTimer.singleShot(
            30_000,
            lambda video_id=video.video_id: self._finish_video_like_scan(
                video_id, None
            ),
        )

    def _read_video_like_count(self, video_id: str) -> None:
        video = self._current_video_for_like_scan()
        if video is None or video.video_id != video_id:
            return
        if self.browser.url().path().rstrip("/").endswith(f"/{video_id}") is False:
            self._finish_video_like_scan(video_id, None)
            return
        self._like_scan_attempts += 1
        self.browser.page().runJavaScript(
            VIDEO_LIKE_SCRIPT,
            lambda value: self._handle_video_like_result(video_id, value),
        )

    def _handle_video_like_result(self, video_id: str, value: object) -> None:
        video = self._current_video_for_like_scan()
        if video is None or video.video_id != video_id:
            return
        like_count = (
            parse_detail_like_count(value)
            if isinstance(value, str)
            else None
        )
        if like_count is None and self._like_scan_attempts < 3:
            QTimer.singleShot(
                1_000,
                lambda: self._read_video_like_count(video_id),
            )
            return
        self._finish_video_like_scan(video_id, like_count)

    def _finish_video_like_scan(
        self, video_id: str, like_count: int | None
    ) -> None:
        video = self._current_video_for_like_scan()
        if video is None or video.video_id != video_id:
            return
        self._like_scan_results[video_id] = like_count
        self._update_video_row(video_id, like_count)
        self._like_scan_index += 1
        self._update_scan_progress()
        if self._like_scan_index >= len(self._like_scan_videos):
            self._finish_video_like_scan_batch()
            return
        self._returning_to_profile = True
        self._profile_return_attempts = 0
        self.status.setText(
            f"已读取第 {self._like_scan_index}/{len(self._like_scan_videos)} 条视频点赞量；"
            "正在返回主页，再继续下一条。"
        )
        profile_url = self._like_scan_profile_url
        if self._is_on_profile_url(profile_url):
            self._returning_to_profile = False
            QTimer.singleShot(
                self._like_scan_delay_ms,
                self._open_next_video_for_like_scan,
            )
        else:
            self.browser.setUrl(QUrl(profile_url))

    def _is_on_profile_url(self, profile_url: str) -> bool:
        target = QUrl(profile_url)
        current = self.browser.url()
        return (
            current.host() == target.host()
            and current.path().rstrip("/") == target.path().rstrip("/")
        )

    def _finish_video_like_scan_batch(self) -> None:
        if not self._like_scan_active:
            return
        profile_url = self._like_scan_profile_url
        videos = [
            VideoMetric(
                video_id=video.video_id,
                title=video.title,
                play_count=video.play_count,
                like_count=self._like_scan_results.get(video.video_id),
            )
            for video in self._like_scan_videos
        ]
        self._like_scan_active = False
        try:
            captured_at = save_snapshot(DATABASE_PATH, profile_url, videos)
        except (OSError, RuntimeError, sqlite3.Error) as error:
            self._capture_in_progress = False
            self.status.setText(f"保存采集结果失败：{error}")
            self._restore_profile_after_like_scan(profile_url)
            return
        self._update_results(videos)
        self.export_button.setEnabled(bool(videos))
        known_likes = sum(video.like_count is not None for video in videos)
        self.status.setText(
            f"最近采集时间：{captured_at}；读取 {len(videos)} 条视频，"
            f"其中 {known_likes} 条获取到点赞量。"
        )
        self._capture_in_progress = False
        self._restore_profile_after_like_scan(profile_url)

    def _restore_profile_after_like_scan(self, profile_url: str) -> None:
        self._like_scan_videos = []
        self._like_scan_results = {}
        self._like_scan_index = 0
        self._like_scan_profile_url = ""
        if self.browser.url().toString() != profile_url:
            self._restoring_profile = True
            self.browser.setUrl(QUrl(profile_url))

    def _update_results(self, videos: list[VideoMetric]) -> None:
        self._displayed_videos = list(videos)
        known_plays = sum(video.play_count is not None for video in videos)
        self.metrics_summary.setText(
            f"作品 {len(videos):,} 条    ·    已识别播放量 {known_plays:,} 条"
            f"    ·    已读取点赞量 "
            f"{sum(video.like_count is not None for video in videos):,} 条"
        )
        self.table.setRowCount(len(videos))
        for row, video in enumerate(videos):
            values = (
                video.title or "（无标题）",
                video.video_id,
                f"{video.play_count:,}" if video.play_count is not None else "未公开",
                f"{video.like_count:,}" if video.like_count is not None else "未公开",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column > 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)

    def _update_video_row(self, video_id: str, like_count: int | None) -> None:
        for row, video in enumerate(self._like_scan_videos):
            if video.video_id == video_id:
                updated_video = VideoMetric(
                    video_id=video.video_id,
                    title=video.title,
                    play_count=video.play_count,
                    like_count=like_count,
                )
                self._displayed_videos[row] = updated_video
                item = QTableWidgetItem(
                    f"{like_count:,}" if like_count is not None else "未获取"
                )
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.table.setItem(row, 3, item)
                break
        known_likes = sum(
            value is not None for value in self._like_scan_results.values()
        )
        self.metrics_summary.setText(
            f"作品 {len(self._like_scan_videos):,} 条    ·    "
            f"已识别播放量 "
            f"{sum(video.play_count is not None for video in self._like_scan_videos):,} 条"
            f"    ·    "
            f"已读取点赞量 {known_likes:,} 条"
        )

    def _update_scan_progress(self) -> None:
        total = len(self._like_scan_videos)
        self.scan_progress.setRange(0, max(total, 1))
        self.scan_progress.setValue(min(self._like_scan_index, total))

    def _export_results(self) -> None:
        if not self._displayed_videos:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出视频数据",
            "douyin_video_metrics.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        try:
            with Path(path).open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["视频标题", "视频 ID", "播放量", "点赞量"])
                writer.writerows(
                    [
                        [
                            video.title,
                            video.video_id,
                            video.play_count if video.play_count is not None else "",
                            video.like_count if video.like_count is not None else "",
                        ]
                        for video in self._displayed_videos
                    ]
                )
        except OSError as error:
            QMessageBox.critical(self, "导出失败", f"无法写入 CSV 文件：{error}")
            return
        self.status.setText(f"数据列表已导出：{path}")

def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("DouyinDataAnalyzer")
    app.setApplicationName("DouyinDataAnalyzer")
    _configure_data_directory()
    window = AnalyzerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
