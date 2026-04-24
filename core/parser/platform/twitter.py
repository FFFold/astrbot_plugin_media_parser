"""Twitter/X parser implementation."""
import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from ...constants import Config
from ...logger import logger
from ..utils import build_request_headers
from .base import BaseVideoParser


TWITTER_BEARER_TOKEN = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOu"
    "H5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
TWITTER_GUEST_ACTIVATE_API = (
    "https://api.twitter.com/1.1/guest/activate.json"
)
TWITTER_GRAPHQL_TWEET_API = (
    "https://api.twitter.com/graphql/"
    "kPLTRmMnzbPTv70___D06w/TweetResultByRestId"
)
TWITTER_GRAPHQL_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "tweet_with_visibility_results_prefer_gql_media_interstitial_enabled": False,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}
TWITTER_GRAPHQL_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
}


class TwitterParser(BaseVideoParser):
    """Twitter/X parser with FxTwitter primary path and GraphQL fallback."""

    def __init__(
        self,
        use_parse_proxy: bool = False,
        use_image_proxy: bool = False,
        use_video_proxy: bool = False,
        proxy_url: str = None
    ):
        super().__init__("twitter")
        self.use_parse_proxy = use_parse_proxy
        self.use_image_proxy = use_image_proxy
        self.use_video_proxy = use_video_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

    def can_parse(self, url: str) -> bool:
        if not url:
            logger.debug(f"[{self.name}] can_parse: empty URL")
            return False
        url_lower = url.lower()
        if ("twitter.com" in url_lower or "x.com" in url_lower) and re.search(
            r"/status/(\d+)",
            url
        ):
            logger.debug(f"[{self.name}] can_parse: matched Twitter URL {url}")
            return True
        logger.debug(f"[{self.name}] can_parse: unsupported URL {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        result_links_set = set()
        seen_ids = set()
        pattern = (
            r"https?://(?:twitter\.com|x\.com)/"
            r"[^\s]*?status/(\d+)[^\s<>\"'()]*"
        )
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            tweet_id = match.group(1)
            if tweet_id not in seen_ids:
                seen_ids.add(tweet_id)
                result_links_set.add(match.group(0))
        result = list(result_links_set)
        if result:
            logger.debug(
                f"[{self.name}] extract_links: extracted {len(result)} links "
                f"{result[:3]}{'...' if len(result) > 3 else ''}"
            )
        else:
            logger.debug(f"[{self.name}] extract_links: no links found")
        return result

    @staticmethod
    def _compact_json(data: Dict[str, Any]) -> str:
        return json.dumps(data, separators=(",", ":"))

    @staticmethod
    def _build_img_url(url: str, size: str = "orig") -> str:
        if not url:
            return ""
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}name={size}"

    @staticmethod
    def _format_twitter_time(created_at: str) -> str:
        if not created_at:
            return ""
        try:
            dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return created_at

    @staticmethod
    def _strip_trailing_tco(text: str) -> str:
        if not text:
            return ""
        return re.sub(r"\s*https://t\.co/[^\s,]+$", "", text).strip()

    @staticmethod
    def _variant_bitrate(item: Dict[str, Any]) -> int:
        try:
            return int(item.get("bitrate") or item.get("bit_rate") or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _best_video_variant(cls, variants: List[Dict[str, Any]]) -> Optional[str]:
        mp4_variants = [
            item for item in variants
            if isinstance(item, dict)
            and item.get("url")
            and item.get("content_type") == "video/mp4"
        ]
        if mp4_variants:
            best = max(mp4_variants, key=cls._variant_bitrate)
            return best.get("url")
        for item in variants:
            if isinstance(item, dict) and item.get("url"):
                return item.get("url")
        return None

    def _build_result_from_media_info(
        self,
        url: str,
        media_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        videos = media_info.get("videos", [])
        images = media_info.get("images", [])
        video_urls = [
            item.get("url")
            for item in videos
            if isinstance(item, dict) and item.get("url")
        ]
        image_urls = [
            image for image in images
            if isinstance(image, str) and image
        ]

        if not video_urls and not image_urls:
            raise RuntimeError("Tweet does not contain media")

        text = media_info.get("text", "")
        metadata_base = {
            "url": url,
            "title": text[:100] if text else "Twitter tweet",
            "author": media_info.get("author", ""),
            "desc": text,
            "timestamp": media_info.get("timestamp", ""),
            "platform": self.name,
            "image_headers": build_request_headers(is_video=False),
            "video_headers": build_request_headers(is_video=True),
            "use_image_proxy": self.use_image_proxy,
            "use_video_proxy": self.use_video_proxy,
            "proxy_url": self.proxy_url if (
                self.use_image_proxy or self.use_video_proxy
            ) else None,
        }

        if video_urls:
            return {
                **metadata_base,
                "video_urls": self._add_range_prefix_to_video_urls(
                    [[item] for item in video_urls]
                ),
                "image_urls": [[item] for item in image_urls],
                "force_pre_download": True,
                "is_twitter_video": True,
            }
        return {
            **metadata_base,
            "video_urls": [],
            "image_urls": [[item] for item in image_urls],
            "force_pre_download": False,
            "is_twitter_video": False,
        }

    async def _fetch_media_info(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Dict[str, Any]:
        api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
        proxy = self.proxy_url if self.use_parse_proxy else None
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    api_url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=proxy
                ) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)
                return self._parse_fxtwitter_response(data)
            except aiohttp.ClientResponseError as e:
                if e.status < 500:
                    raise RuntimeError(f"HTTP {e.status} {e.message}")
                last_exception = e
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                aiohttp.ServerTimeoutError
            ) as e:
                last_exception = e
            except Exception as e:
                raise RuntimeError(str(e))

            if attempt < max_retries:
                await asyncio.sleep(retry_delay * (2 ** attempt))

        error_msg = str(last_exception) if last_exception else "unknown error"
        raise RuntimeError(f"{error_msg} (retried {max_retries} times)")

    def _parse_fxtwitter_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        media_urls = {
            "images": [],
            "videos": [],
            "text": "",
            "author": "",
            "timestamp": "",
        }
        tweet = data.get("tweet") or {}
        if not isinstance(tweet, dict):
            return media_urls

        media_urls["text"] = tweet.get("text", "")
        author_info = tweet.get("author", {})
        if isinstance(author_info, dict):
            author_name = author_info.get("name", "")
            author_username = author_info.get("screen_name", "")
            media_urls["author"] = (
                f"{author_name}(@{author_username})"
                if author_name else author_username
            )

        media_urls["timestamp"] = self._format_twitter_time(
            tweet.get("created_at", "")
        )

        media = tweet.get("media") or {}
        for photo in media.get("photos") or []:
            if isinstance(photo, dict) and photo.get("url"):
                media_urls["images"].append(photo.get("url"))
        for video in media.get("videos") or []:
            if isinstance(video, dict) and video.get("url"):
                media_urls["videos"].append({
                    "url": video.get("url", ""),
                    "thumbnail": video.get("thumbnail_url", ""),
                    "duration": video.get("duration", 0),
                })
        return media_urls

    async def _fetch_twitter_guest_token(
        self,
        session: aiohttp.ClientSession
    ) -> str:
        proxy = self.proxy_url if self.use_parse_proxy else None
        headers = {
            "authorization": TWITTER_BEARER_TOKEN,
            "user-agent": self.headers["User-Agent"],
        }
        async with session.post(
            TWITTER_GUEST_ACTIVATE_API,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
            proxy=proxy
        ) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        guest_token = str(data.get("guest_token") or "").strip()
        if not guest_token:
            raise RuntimeError("Failed to fetch Twitter guest token")
        return guest_token

    async def _fetch_media_info_graphql(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str
    ) -> Dict[str, Any]:
        guest_token = await self._fetch_twitter_guest_token(session)
        proxy = self.proxy_url if self.use_parse_proxy else None
        headers = {
            "accept-language": "zh-CN,zh;q=0.9",
            "authorization": TWITTER_BEARER_TOKEN,
            "content-type": "application/json",
            "user-agent": self.headers["User-Agent"],
            "x-guest-token": guest_token,
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "zh-cn",
        }
        params = {
            "variables": self._compact_json({
                "tweetId": tweet_id,
                "withCommunity": False,
                "includePromotedContent": False,
                "withVoice": False,
            }),
            "features": self._compact_json(TWITTER_GRAPHQL_FEATURES),
            "fieldToggles": self._compact_json(
                TWITTER_GRAPHQL_FIELD_TOGGLES
            ),
        }

        async with session.get(
            TWITTER_GRAPHQL_TWEET_API,
            headers=headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
            proxy=proxy
        ) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)

        errors = data.get("errors") or []
        if errors:
            message = errors[0].get("message", errors[0])
            raise RuntimeError(f"Twitter GraphQL error: {message}")

        result = (
            (data.get("data") or {})
            .get("tweetResult", {})
            .get("result")
        )
        if not isinstance(result, dict):
            raise RuntimeError("Twitter GraphQL did not return tweet data")

        tweet = (
            result.get("tweet")
            if isinstance(result.get("tweet"), dict)
            else result
        )
        legacy = tweet.get("legacy") or {}
        if not legacy:
            reason = result.get("reason") or result.get("__typename") or "unknown"
            raise RuntimeError(f"Twitter GraphQL cannot read tweet: {reason}")

        return self._parse_graphql_tweet(tweet, legacy)

    def _parse_graphql_tweet(
        self,
        tweet: Dict[str, Any],
        legacy: Dict[str, Any]
    ) -> Dict[str, Any]:
        note_result = (
            (tweet.get("note_tweet") or {})
            .get("note_tweet_results", {})
            .get("result", {})
        )
        text = note_result.get("text") or legacy.get("full_text", "")
        text = self._strip_trailing_tco(text)

        user_result = (
            (tweet.get("core") or {})
            .get("user_results", {})
            .get("result", {})
        )
        user_legacy = user_result.get("legacy") or {}
        author_name = user_legacy.get("name", "")
        author_username = user_legacy.get("screen_name", "")
        author = (
            f"{author_name}(@{author_username})"
            if author_name and author_username
            else author_name or author_username
        )

        media_info = {
            "images": [],
            "videos": [],
            "text": text,
            "author": author,
            "timestamp": self._format_twitter_time(
                legacy.get("created_at", "")
            ),
        }
        media_items = (
            (legacy.get("extended_entities") or {}).get("media")
            or (legacy.get("entities") or {}).get("media")
            or []
        )
        seen_urls = set()
        for item in media_items:
            if not isinstance(item, dict):
                continue
            media_type = item.get("type")
            original_info = item.get("original_info") or {}
            if media_type == "photo":
                photo_url = self._build_img_url(
                    item.get("media_url_https", ""),
                    "orig"
                )
                if photo_url and photo_url not in seen_urls:
                    seen_urls.add(photo_url)
                    media_info["images"].append(photo_url)
            elif media_type in ("video", "animated_gif"):
                video_info = item.get("video_info") or {}
                video_url = self._best_video_variant(
                    video_info.get("variants") or []
                )
                if video_url and video_url not in seen_urls:
                    seen_urls.add(video_url)
                    media_info["videos"].append({
                        "url": video_url,
                        "thumbnail": self._build_img_url(
                            item.get("media_url_https", ""),
                            "medium"
                        ),
                        "duration": video_info.get("duration_millis", 0),
                        "width": original_info.get("width", 0),
                        "height": original_info.get("height", 0),
                    })
        return media_info

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        async with self.semaphore:
            tweet_id_match = re.search(r"/status/(\d+)", url)
            if not tweet_id_match:
                raise RuntimeError(f"Unable to parse Twitter URL: {url}")
            tweet_id = tweet_id_match.group(1)

            try:
                media_info = await self._fetch_media_info(session, tweet_id)
                result = self._build_result_from_media_info(url, media_info)
                logger.debug(
                    f"[{self.name}] parse: FxTwitter parsed {url}, "
                    f"video_count={len(result.get('video_urls', []))}, "
                    f"image_count={len(result.get('image_urls', []))}"
                )
                return result
            except Exception as primary_error:
                logger.debug(
                    f"[{self.name}] FxTwitter failed, trying GraphQL fallback: "
                    f"{url}, error: {primary_error}"
                )

            try:
                media_info = await self._fetch_media_info_graphql(
                    session,
                    tweet_id
                )
                result = self._build_result_from_media_info(url, media_info)
                logger.debug(
                    f"[{self.name}] parse: GraphQL fallback parsed {url}, "
                    f"video_count={len(result.get('video_urls', []))}, "
                    f"image_count={len(result.get('image_urls', []))}"
                )
                return result
            except Exception as fallback_error:
                logger.debug(
                    f"[{self.name}] GraphQL fallback failed: {url}, "
                    f"error: {fallback_error}"
                )
                raise RuntimeError(str(fallback_error)) from fallback_error
