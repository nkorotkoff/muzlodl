"""Additional yt-dlp powered sources. Free, no API keys needed.

These wrap yt-dlp's extractor with a search prefix. Search prefixes follow
yt-dlp's `<extractor>search<N>:` convention.
"""
from __future__ import annotations

from .ytdlp_based import YTDLPBasedSource


class DailymotionSource(YTDLPBasedSource):
    name = "dailymotion"
    search_prefix = "dailymotionsearch5:"
