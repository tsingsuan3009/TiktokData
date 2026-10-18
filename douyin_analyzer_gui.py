"""Desktop interface with an embedded browser for Douyin metric collection."""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
from PySide6.QtWebEngineWidgets import QWebEngineView

from douyin_analyzer import (
    VideoMetric,
    generate_chart,
    normalize_profile_url,
    parse_video_cards,
    save_snapshot,
)


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_DIR / "data" / "douyin_metrics.sqlite"
CHART_PATH = PROJECT_DIR / "data" / "metrics_trend.png"
VIDEO_CARDS_SCRIPT = """
(() => Array.from(document.querySelectorAll("a[href*='/video/']")).map(anchor => {
    let node = anchor;
    let cardText = anchor.innerText || '';
    for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
        const text = (node.innerText || '').trim();
        if (text && text.length <= 1200) {
            cardText = text;
            if (/(播放|观看|点赞|获赞|like|view)/i.test(text)) break;
        }
    }
    return {
        href: anchor.href,
        title: anchor.getAttribute('title') ||
            anchor.getAttribute('aria-label') || anchor.innerText || '',
        text: cardText
    };
}))()
"""


class AnalyzerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("抖音视频数据分析")
        self.resize(1440, 900)
        self._is_collecting = False

        self.browser = QWebEngineView()
        self.browser.urlChanged.connect(self._sync_address)
        self.browser.loadFinished.connect(self._page_loaded)

        self.address = QLineEdit("https://www.douyin.com/")
        self.address.setPlaceholderText("输入抖音个人主页网址")
        self.address.returnPressed.connect(self._navigate)
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

    def _page_loaded(self, success: bool) -> None:
        if not success:
            self.status.setText("网页加载失败。请检查网络，或在内置浏览器中重新打开主页。")
            return
        if self._is_collecting:
            self._collect_from_page()

    def _start_collection(self) -> None:
        try:
            profile_url = normalize_profile_url(self.address.text().strip())
        except ValueError as error:
            QMessageBox.warning(self, "网址无效", str(error))
            return

        self._is_collecting = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._collection_timer.start(self.interval.value() * 1_000)
        if self.browser.url().toString() != profile_url:
            self.browser.setUrl(QUrl(profile_url))
        else:
            self._collect_from_page()
        self.status.setText("采集已启动；首次采集将读取当前主页。")

    def _stop_collection(self) -> None:
        self._is_collecting = False
        self._collection_timer.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText("采集已停止。已保存的历史记录和趋势图仍保留。")

    def _collect_from_page(self) -> None:
        try:
            profile_url = normalize_profile_url(self.browser.url().toString())
        except ValueError:
            self.status.setText("当前浏览页面不是抖音主页；请返回公开的个人主页后再采集。")
            return

        self.status.setText("正在读取当前页面中公开可见的视频信息……")
        self.browser.page().runJavaScript(
            VIDEO_CARDS_SCRIPT,
            lambda cards: self._finish_collection(profile_url, cards),
        )

    def _finish_collection(self, profile_url: str, cards: object) -> None:
        if not self._is_collecting:
            return
        if self.browser.url().toString() != profile_url:
            self.status.setText("页面已切换；请在目标抖音主页重新开始采集。")
            return
        if not isinstance(cards, list):
            self.status.setText("页面未返回视频数据。请检查主页是否公开可访问，再刷新后重试。")
            return
        valid_cards = [
            card
            for card in cards
            if isinstance(card, dict)
            and isinstance(card.get("href"), str)
            and isinstance(card.get("title", ""), str)
            and isinstance(card.get("text", ""), str)
        ]
        videos = parse_video_cards(valid_cards)
        if not videos:
            self.status.setText(
                "未找到视频卡片。请确认主页无需登录即可访问；本程序不会绕过登录、"
                "验证码或平台访问限制。"
            )
            return

        try:
            captured_at = save_snapshot(DATABASE_PATH, profile_url, videos)
        except (OSError, RuntimeError, sqlite3.Error) as error:
            self.status.setText(f"保存数据或生成图表失败：{error}")
            return
        self._update_results(videos)
        try:
            generate_chart(DATABASE_PATH, CHART_PATH)
        except (OSError, RuntimeError, sqlite3.Error) as error:
            self.status.setText(f"数据已保存，但生成趋势图失败：{error}")
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
        self.status.setText(f"最近采集时间：{captured_at}；本次读取 {len(videos)} 条视频。")

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

    def resizeEvent(self, event: object) -> None:
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
    window = AnalyzerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
