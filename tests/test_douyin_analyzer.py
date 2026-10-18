import unittest

from douyin_analyzer import (
    extract_metrics,
    normalize_profile_url,
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


if __name__ == "__main__":
    unittest.main()
