"""Collect visible Douyin video metrics and save historical snapshots."""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


COUNT_PATTERN = r"(?P<count>\d+(?:\.\d+)?\s*(?:万|亿|千|[kKmM])?)"
METRIC_PATTERNS = {
    "play_count": (
        re.compile(rf"(?:播放量?|播放次数|观看量|views?)\s*[:：]?\s*{COUNT_PATTERN}", re.I),
        re.compile(rf"{COUNT_PATTERN}\s*(?:次)?\s*(?:播放|观看)", re.I),
    ),
    "like_count": (
        re.compile(rf"(?:点赞量?|获赞|喜欢数|likes?)\s*[:：]?\s*{COUNT_PATTERN}", re.I),
        re.compile(rf"{COUNT_PATTERN}\s*(?:次)?\s*(?:点赞|获赞|喜欢)", re.I),
    ),
}
VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)")


@dataclass(frozen=True)
class VideoMetric:
    video_id: str
    title: str
    play_count: int | None
    like_count: int | None


def parse_count(value: str) -> int:
    """Convert a displayed count such as '1.2万' to an integer."""
    normalized = value.strip().replace(",", "").replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿|千|[kKmM])?", normalized)
    if match is None:
        raise ValueError(f"Unsupported count format: {value!r}")

    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {
        None: 1,
        "千": 1_000,
        "万": 10_000,
        "亿": 100_000_000,
        "k": 1_000,
        "K": 1_000,
        "m": 1_000_000,
        "M": 1_000_000,
    }[suffix]
    return round(number * multiplier)


def parse_detail_like_count(value: str) -> int | None:
    """Parse the visible like count from a video-detail page."""
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return parse_count(normalized)
    except ValueError:
        return None


def video_collection_matches_declared_count(
    expected_count: int, videos: list[VideoMetric]
) -> bool:
    """Return whether unique collected videos exactly match the profile count."""
    video_ids = {video.video_id for video in videos}
    return expected_count >= 0 and len(video_ids) == expected_count


def extract_metrics(text: str) -> tuple[int | None, int | None]:
    """Extract play and like counts only when their labels are visible."""
    results: dict[str, int | None] = {"play_count": None, "like_count": None}
    for metric, patterns in METRIC_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(text)
            if match is not None:
                results[metric] = parse_count(match.group("count"))
                break
    return results["play_count"], results["like_count"]


def normalize_profile_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "www.douyin.com",
        "douyin.com",
    }:
        raise ValueError("请提供抖音个人主页链接，例如 https://www.douyin.com/user/用户ID。")
    if not re.fullmatch(r"/user/[^/]+/?", parsed.path):
        raise ValueError("当前网址不是个人主页。请使用 /user/用户ID 格式的抖音主页链接。")
    return parsed.geturl()


def parse_video_cards(cards: list[dict[str, str]]) -> list[VideoMetric]:
    videos: dict[str, VideoMetric] = {}
    for card in cards:
        match = VIDEO_ID_PATTERN.search(card.get("href", ""))
        if match is None:
            continue
        video_id = match.group(1)
        plays, likes = extract_metrics(card.get("text", ""))
        displayed_play_count = card.get("play_count", "").strip()
        if plays is None and displayed_play_count:
            plays = parse_count(displayed_play_count)
        videos[video_id] = VideoMetric(
            video_id=video_id,
            title=" ".join(card.get("title", "").split())[:200],
            play_count=plays,
            like_count=likes,
        )
    return list(videos.values())


def scrape_profile(profile_url: str, scrolls: int) -> list[VideoMetric]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Playwright 依赖。请先运行 pip install -r requirements.txt，"
            "再运行 playwright install chromium。"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale="zh-CN")
            page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3_000)
            for _ in range(scrolls):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            cards = page.locator("a[href*='/video/']").evaluate_all(
                """anchors => anchors.map(anchor => {
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
                })"""
            )
        finally:
            browser.close()

    videos = parse_video_cards(cards)
    if not videos:
        raise RuntimeError(
            "未能从页面读取视频。请确认主页公开可访问；如果出现登录、验证码或"
            "访问限制，请稍后再试，不要尝试绕过平台限制。"
        )
    return videos


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY,
            profile_url TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS video_metrics (
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            video_id TEXT NOT NULL,
            title TEXT NOT NULL,
            play_count INTEGER,
            like_count INTEGER,
            PRIMARY KEY (snapshot_id, video_id)
        );
        """
    )


def save_snapshot(
    database: Path, profile_url: str, videos: list[VideoMetric]
) -> str:
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            initialize_database(connection)
            cursor = connection.execute(
                "INSERT INTO snapshots (profile_url, captured_at) VALUES (?, ?)",
                (profile_url, captured_at),
            )
            connection.executemany(
                """INSERT INTO video_metrics
                   (snapshot_id, video_id, title, play_count, like_count)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        cursor.lastrowid,
                        video.video_id,
                        video.title,
                        video.play_count,
                        video.like_count,
                    )
                    for video in videos
                ],
            )
    return captured_at


def run(args: argparse.Namespace) -> None:
    profile_url = normalize_profile_url(args.profile_url)
    while True:
        videos = scrape_profile(profile_url, args.scrolls)
        captured_at = save_snapshot(args.database, profile_url, videos)
        known_plays = sum(v.play_count or 0 for v in videos)
        known_likes = sum(v.like_count or 0 for v in videos)
        play_entries = sum(v.play_count is not None for v in videos)
        like_entries = sum(v.like_count is not None for v in videos)
        print(
            f"[{captured_at}] 视频 {len(videos)} 条；"
            f"已识别播放量 {play_entries} 条（合计 {known_plays}），"
            f"点赞量 {like_entries} 条（合计 {known_likes}）"
        )
        if args.once:
            return
        time.sleep(args.interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="定时采集公开抖音主页可见的视频播放量和点赞量。"
    )
    parser.add_argument("profile_url", help="公开的抖音个人主页 URL")
    parser.add_argument(
        "--interval", type=int, default=300, help="采集间隔秒数（默认 300）"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/douyin_metrics.sqlite"),
        help="SQLite 数据库文件路径",
    )
    parser.add_argument(
        "--scrolls", type=int, default=5, help="每次采集向下滚动次数（默认 5）"
    )
    parser.add_argument("--once", action="store_true", help="仅采集一次后退出")
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval 必须大于 0")
    if args.scrolls < 0:
        parser.error("--scrolls 不能小于 0")
    return args


if __name__ == "__main__":
    run(parse_args())
