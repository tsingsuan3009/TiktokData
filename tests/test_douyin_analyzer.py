import unittest

from douyin_analyzer import (
    extract_metrics,
    normalize_profile_url,
    parse_video_cards,
    parse_count,
)


class ParseCountTests(unittest.TestCase):
    def test_chinese_and_latin_suffixes(self):
        self.assertEqual(parse_count("1.2万"), 12_000)
        self.assertEqual(parse_count("2亿"), 200_000_000)
        self.assertEqual(parse_count("3.5k"), 3_500)
        self.assertEqual(parse_count("1,234"), 1_234)

    def test_rejects_unrecognized_values(self):
        with self.assertRaises(ValueError):
            parse_count("很多")


class ExtractMetricsTests(unittest.TestCase):
    def test_label_before_count(self):
        self.assertEqual(extract_metrics("播放量 1.2万 点赞 345"), (12_000, 345))

    def test_count_before_label(self):
        self.assertEqual(extract_metrics("约 2.5万次播放，获赞 1.1万"), (25_000, 11_000))

    def test_missing_metric_is_not_guessed(self):
        self.assertEqual(extract_metrics("视频标题 1.2万"), (None, None))


class ProfileUrlTests(unittest.TestCase):
    def test_accepts_douyin_profile(self):
        url = "https://www.douyin.com/user/example"
        self.assertEqual(normalize_profile_url(url), url)

    def test_rejects_non_douyin_hosts(self):
        with self.assertRaises(ValueError):
            normalize_profile_url("https://example.com/user")

    def test_rejects_douyin_feed_as_a_profile(self):
        with self.assertRaisesRegex(ValueError, "不是个人主页"):
            normalize_profile_url("https://www.douyin.com/jingxuan")

    def test_accepts_profile_url_with_query_and_whitespace(self):
        url = " https://www.douyin.com/user/example?from_tab_name=main "
        self.assertEqual(
            normalize_profile_url(url),
            "https://www.douyin.com/user/example?from_tab_name=main",
        )


class ParseVideoCardsTests(unittest.TestCase):
    def test_extracts_video_metrics_and_deduplicates_video_ids(self):
        videos = parse_video_cards(
            [
                {
                    "href": "https://www.douyin.com/video/12345",
                    "title": "  一个  视频 ",
                    "text": "播放量 1.2万 点赞 345",
                },
                {
                    "href": "https://www.douyin.com/video/12345",
                    "title": "更新标题",
                    "text": "播放量 2万 点赞 500",
                },
                {"href": "https://www.douyin.com/user/example", "title": "", "text": ""},
            ]
        )
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].video_id, "12345")
        self.assertEqual(videos[0].title, "更新标题")
        self.assertEqual(videos[0].play_count, 20_000)
        self.assertEqual(videos[0].like_count, 500)

    def test_parses_play_count_displayed_without_a_text_label(self):
        videos = parse_video_cards(
            [
                {
                    "href": "https://www.douyin.com/video/12345",
                    "title": "示例作品",
                    "text": "示例作品",
                    "play_count": "1.2万",
                }
            ]
        )
        self.assertEqual(videos[0].play_count, 12_000)
        self.assertIsNone(videos[0].like_count)


if __name__ == "__main__":
    unittest.main()
