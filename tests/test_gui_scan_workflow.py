import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication, QMainWindow

from douyin_analyzer import VideoMetric
from douyin_analyzer_gui import AnalyzerWindow


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


if __name__ == "__main__":
    unittest.main()
