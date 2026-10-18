import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication, QMainWindow

from douyin_analyzer import VideoMetric
from douyin_analyzer_gui import AnalyzerWindow, SCROLL_PROFILE_WORKS_SCRIPT


class FakeBrowser:
    def __init__(self, url: str):
        self._url = QUrl(url)

    def url(self) -> QUrl:
        return self._url

    def setUrl(self, url: QUrl) -> None:
        self._url = url


class FakeStatus:
    def setText(self, text: str) -> None:
        self.text = text


class FakePage:
    def __init__(self):
        self.scripts = []

    def runJavaScript(self, script, callback=None):
        self.scripts.append(script)
        if callback is not None:
            callback(True)


class LikeScanNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_returns_to_profile_before_opening_next_video(self):
        window = AnalyzerWindow.__new__(AnalyzerWindow)
        QMainWindow.__init__(window)
        profile_url = "https://www.douyin.com/user/example"
        window.browser = FakeBrowser("https://www.douyin.com/video/101")
        window.status = FakeStatus()
        window._is_collecting = True
        window._like_scan_active = True
        window._returning_to_profile = False
        window._profile_return_attempts = 0
        window._max_profile_return_attempts = 2
        window._restoring_profile = False
        window._like_scan_profile_url = profile_url
        window._like_scan_videos = [
            VideoMetric("101", "first", 10, None),
            VideoMetric("202", "second", 20, None),
        ]
        window._like_scan_results = {}
        window._like_scan_index = 0
        window._like_scan_attempts = 1
        window._like_scan_delay_ms = 0

        original_single_shot = QTimer.singleShot
        QTimer.singleShot = staticmethod(
            lambda delay, callback: None if delay == 30_000 else callback()
        )
        try:
            window._finish_video_like_scan("101", 15)
            self.assertEqual(window.browser.url().toString(), profile_url)
            self.assertTrue(window._returning_to_profile)

            window._page_loaded(True)

            self.assertEqual(
                window.browser.url().toString(),
                "https://www.douyin.com/video/202",
            )
            self.assertFalse(window._returning_to_profile)
        finally:
            QTimer.singleShot = original_single_shot

    def make_collection_window(self):
        window = AnalyzerWindow.__new__(AnalyzerWindow)
        QMainWindow.__init__(window)
        window.browser = FakeBrowser("https://www.douyin.com/user/example")
        window.page = FakePage()
        window.browser.page = lambda: window.page
        window.status = FakeStatus()
        window._is_collecting = True
        window._capture_in_progress = True
        window._capture_attempts = 1
        window._max_capture_attempts = 200
        window._profile_expected_count = None
        window._profile_videos = {}
        window._profile_previous_count = 0
        window._profile_no_progress_attempts = 0
        window._max_profile_no_progress_attempts = 8
        window.started_scan = None
        window._start_video_like_scan = (
            lambda profile, videos: setattr(
                window, "started_scan", (profile, videos)
            )
        )
        return window

    def test_starts_like_scan_only_when_video_count_matches_profile(self):
        window = self.make_collection_window()
        snapshot = {
            "expected_count": 1,
            "videos": [
                {
                    "href": "https://www.douyin.com/video/101",
                    "title": "first",
                    "text": "first",
                    "play_count": "10",
                }
            ],
        }

        window._finish_collection(
            "https://www.douyin.com/user/example", json.dumps(snapshot)
        )

        self.assertIsNotNone(window.started_scan)
        self.assertEqual(len(window.started_scan[1]), 1)

    def test_scrolls_more_when_collected_count_is_below_declared_count(self):
        window = self.make_collection_window()
        snapshot = {
            "expected_count": 2,
            "videos": [
                {
                    "href": "https://www.douyin.com/video/101",
                    "title": "first",
                    "text": "first",
                    "play_count": "10",
                }
            ],
        }
        original_single_shot = QTimer.singleShot
        QTimer.singleShot = staticmethod(lambda _delay, _callback: None)
        try:
            window._finish_collection(
                "https://www.douyin.com/user/example", json.dumps(snapshot)
            )
        finally:
            QTimer.singleShot = original_single_shot

        self.assertIsNone(window.started_scan)
        self.assertEqual(len(window._profile_videos), 1)
        self.assertEqual(window.page.scripts, [SCROLL_PROFILE_WORKS_SCRIPT])
        self.assertIn("1/2", window.status.text)

    def test_rejects_collection_that_exceeds_declared_count(self):
        window = self.make_collection_window()
        snapshot = {
            "expected_count": 1,
            "videos": [
                {
                    "href": f"https://www.douyin.com/video/{video_id}",
                    "title": video_id,
                    "text": video_id,
                    "play_count": "10",
                }
                for video_id in ("101", "202")
            ],
        }

        window._finish_collection(
            "https://www.douyin.com/user/example", json.dumps(snapshot)
        )

        self.assertIsNone(window.started_scan)
        self.assertIn("超过主页显示的 1 条作品", window.status.text)


if __name__ == "__main__":
    unittest.main()
