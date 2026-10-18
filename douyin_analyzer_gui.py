"""Desktop interface with an embedded browser for Douyin metric collection."""

from __future__ import annotations

import json
import sys
import sqlite3
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, QUrl, Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
    generate_chart,
    normalize_profile_url,
    parse_detail_like_count,
    parse_video_cards,
    save_snapshot,
)


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_DIR / "data" / "douyin_metrics.sqlite"
CHART_PATH = PROJECT_DIR / "data" / "metrics_trend.png"
VIDEO_CARDS_SCRIPT = """
JSON.stringify(Array.from(document.querySelectorAll("a[href*='/video/']")).map(anchor => {
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
}))
"""
VIDEO_LIKE_SCRIPT = """
document.querySelector('[data-e2e="video-player-digg"]')?.innerText?.trim() || ''
"""


class AnalyzerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("抖音视频数据分析")
        self.resize(1440, 900)
        self._is_collecting = False
        self._capture_in_progress = False
        self._capture_attempts = 0
        self._max_capture_attempts = 8
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
        self.settings = QSettings()

        self.browser = QWebEngineView()
        self.web_profile = QWebEngineProfile("DouyinAnalyzerProfile", self)
        profile_root = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
        ) / "browser-profile"
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
        self.back_button.setToolTip("后退")
        self.back_button.clicked.connect(self.browser.back)
        self.forward_button = QPushButton("→")
        self.forward_button.setToolTip("前进")
        self.forward_button.clicked.connect(self.browser.forward)
        self.reload_button = QPushButton("刷新")
        self.reload_button.clicked.connect(self.browser.reload)
        self.go_button = QPushButton("打开主页")
        self.go_button.clicked.connect(self._navigate)

        self.interval = QSpinBox()
        self.interval.setRange(30, 86_400)
        self.interval.setValue(300)
        self.interval.setSuffix(" 秒")
        self.start_button = QPushButton("开始采集")
        self.start_button.clicked.connect(self._start_collection)
        self.stop_button = QPushButton("停止采集")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_collection)

        self.status = QLabel("请填写公开的抖音个人主页网址，然后开始采集。")
        self.metrics_summary = QLabel("播放量合计：—    点赞量合计：—")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["视频标题", "视频 ID", "播放量", "点赞量"])
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Stretch)

        self.chart = QLabel("采集后将在此显示趋势图")
        self.chart.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chart.setMinimumHeight(260)
        self.chart.setStyleSheet("background: #f4f6f8; border: 1px solid #d9dee5;")

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

        heading = QHBoxLayout()
        title = QLabel("抖音视频数据分析")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(QLabel("本地时间"))
        heading.addWidget(self.clock)
        layout.addLayout(heading)

        navigation = QHBoxLayout()
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.forward_button)
        navigation.addWidget(self.reload_button)
        navigation.addWidget(self.address, 1)
        navigation.addWidget(self.go_button)
        layout.addLayout(navigation)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("采集间隔："))
        controls.addWidget(self.interval)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        login_memory = QHBoxLayout()
        login_memory.addWidget(QLabel("登录用户名："))
        login_memory.addWidget(self.username, 1)
        login_memory.addWidget(self.remember_username)
        login_memory.addWidget(self.clear_login_button)
        layout.addLayout(login_memory)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.addWidget(QLabel("本次主页视频数据"))
        results_layout.addWidget(self.metrics_summary)
        results_layout.addWidget(self.table, 1)
        results_layout.addWidget(QLabel("历史总播放量与总点赞量趋势"))
        results_layout.addWidget(self.chart)
        splitter.addWidget(results)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setCentralWidget(root)
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
        self.status.setText("采集已停止。已保存的历史记录和趋势图仍保留。")

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
        self.status.setText("正在等待主页视频加载……")
        self.browser.page().runJavaScript(
            "window.scrollTo(0, document.body.scrollHeight); true",
            lambda scrolled: self._schedule_video_read(profile_url, scrolled),
        )

    def _schedule_video_read(self, profile_url: str, scroll_succeeded: object) -> None:
        if not self._is_collecting or self.browser.url().toString() != profile_url:
            self._capture_in_progress = False
            return
        if scroll_succeeded is not True:
            self._capture_in_progress = False
            self.status.setText("无法读取主页页面状态；请刷新主页后重试。")
            return
        QTimer.singleShot(800, lambda: self._read_video_cards(profile_url))

    def _read_video_cards(self, profile_url: str) -> None:
        if not self._is_collecting or self.browser.url().toString() != profile_url:
            self._capture_in_progress = False
            return
        self._capture_attempts += 1
        self.status.setText(
            f"正在识别主页视频……（第 {self._capture_attempts}/{self._max_capture_attempts} 次）"
        )
        self.browser.page().runJavaScript(
            VIDEO_CARDS_SCRIPT,
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
        if not isinstance(cards, list):
            self._capture_in_progress = False
            self.status.setText("网页脚本返回的数据不是视频列表。请刷新主页后重试。")
            return
        valid_cards = [
            card
            for card in cards
            if isinstance(card, dict)
            and isinstance(card.get("href"), str)
            and isinstance(card.get("title", ""), str)
            and isinstance(card.get("text", ""), str)
            and isinstance(card.get("play_count", ""), str)
        ]
        videos = parse_video_cards(valid_cards)
        if not videos:
            if self._capture_attempts < self._max_capture_attempts:
                self.status.setText(
                    "主页内容仍在加载，正在继续查找视频卡片……"
                )
                self.browser.page().runJavaScript(
                    "window.scrollTo(0, document.body.scrollHeight); true",
                    lambda scrolled: self._schedule_video_read(profile_url, scrolled),
                )
                return
            self._capture_in_progress = False
            self.status.setText(
                "已确认这是个人主页，但多次等待和滚动后仍未找到视频卡片。"
                "请确认主页内容已加载且无需登录即可查看；本程序不会绕过登录、验证码或平台访问限制。"
            )
            return

        self._start_video_like_scan(profile_url, videos)

    def _start_video_like_scan(
        self, profile_url: str, videos: list[VideoMetric]
    ) -> None:
        self._like_scan_active = True
        self._like_scan_profile_url = profile_url
        self._like_scan_videos = videos
        self._like_scan_results = {}
        self._like_scan_index = 0
        self._like_scan_attempts = 0
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
        self._like_scan_index += 1
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
        try:
            generate_chart(DATABASE_PATH, CHART_PATH)
        except (OSError, RuntimeError, sqlite3.Error) as error:
            self._capture_in_progress = False
            self.status.setText(f"数据已保存，但生成趋势图失败：{error}")
            self._restore_profile_after_like_scan(profile_url)
            return
        pixmap = QPixmap(str(CHART_PATH))
        if not pixmap.isNull():
            self.chart.setPixmap(
                pixmap.scaled(
                    self.chart.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
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
        total_plays = sum(video.play_count or 0 for video in videos)
        total_likes = sum(video.like_count or 0 for video in videos)
        known_plays = sum(video.play_count is not None for video in videos)
        known_likes = sum(video.like_count is not None for video in videos)
        self.metrics_summary.setText(
            f"播放量合计：{total_plays:,}（识别 {known_plays} 条）"
            f"    点赞量合计：{total_likes:,}（识别 {known_likes} 条）"
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
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if CHART_PATH.exists():
            pixmap = QPixmap(str(CHART_PATH))
            if not pixmap.isNull():
                self.chart.setPixmap(
                    pixmap.scaled(
                        self.chart.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("DouyinDataAnalyzer")
    app.setApplicationName("DouyinDataAnalyzer")
    window = AnalyzerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
