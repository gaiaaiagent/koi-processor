"""Async MediaWiki Action API client for the live sync sensor.

Wraps the MediaWiki Action API (api.php) to fetch recent changes and
page content for incremental sync.

Dependencies: aiohttp (already in requirements.txt)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class RecentChange:
    pageid: int
    title: str
    revid: int
    old_revid: int
    timestamp: str
    change_type: str
    ns: int


@dataclass
class PageContent:
    pageid: int
    title: str
    revid: int
    wikitext: str
    timestamp: str


class MediaWikiClient:
    """Thin async client for the MediaWiki Action API."""

    def __init__(self, api_url: str, request_delay: float = 1.0):
        self.api_url = api_url
        self.request_delay = request_delay
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "BKC-MediaWiki-Sensor/1.0"}
            )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_recent_changes(
        self,
        since: datetime,
        limit: int = 50,
        namespace: int = 0,
    ) -> List[RecentChange]:
        """Fetch recent changes since a given timestamp.

        Uses action=query&list=recentchanges with continuation for
        paginated results.
        """
        await self._ensure_session()
        changes: List[RecentChange] = []
        rc_continue = None

        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            params = {
                "action": "query",
                "list": "recentchanges",
                "rcprop": "title|ids|timestamp",
                "rcdir": "newer",
                "rctype": "edit|new",
                "rcnamespace": str(namespace),
                "rclimit": str(min(limit, 500)),
                "rcstart": since_str,
                "format": "json",
            }
            if rc_continue:
                params.update(rc_continue)

            async with self._session.get(self.api_url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"MW API returned {resp.status}")
                    break
                data = await resp.json()

            for rc in data.get("query", {}).get("recentchanges", []):
                changes.append(RecentChange(
                    pageid=rc.get("pageid", 0),
                    title=rc.get("title", ""),
                    revid=rc.get("revid", 0),
                    old_revid=rc.get("old_revid", 0),
                    timestamp=rc.get("timestamp", ""),
                    change_type=rc.get("type", "edit"),
                    ns=rc.get("ns", 0),
                ))

            if len(changes) >= limit:
                changes = changes[:limit]
                break

            if "continue" in data:
                rc_continue = data["continue"]
                await asyncio.sleep(self.request_delay)
            else:
                break

        return changes

    async def fetch_page_content(self, pageid: int) -> Optional[PageContent]:
        """Fetch a single page's wikitext content by page ID."""
        results = await self.fetch_page_batch([pageid])
        return results[0] if results else None

    async def fetch_page_batch(self, pageids: List[int]) -> List[PageContent]:
        """Fetch page content for multiple pages.

        MW API allows max 50 pageids per call; auto-splits into sub-batches.
        """
        await self._ensure_session()
        results: List[PageContent] = []
        batch_size = 50

        for i in range(0, len(pageids), batch_size):
            sub_batch = pageids[i:i + batch_size]
            params = {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content|ids|timestamp",
                "rvslots": "main",
                "pageids": "|".join(str(pid) for pid in sub_batch),
                "format": "json",
            }

            async with self._session.get(self.api_url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"MW API batch fetch returned {resp.status}")
                    continue
                data = await resp.json()

            pages = data.get("query", {}).get("pages", {})
            for pid_str, page_data in pages.items():
                pid = int(pid_str)
                if pid < 0:
                    continue
                revisions = page_data.get("revisions", [])
                if not revisions:
                    continue
                rev = revisions[0]
                slots = rev.get("slots", {})
                main_slot = slots.get("main", {})
                wikitext = main_slot.get("*", "") or main_slot.get("content", "")
                results.append(PageContent(
                    pageid=pid,
                    title=page_data.get("title", ""),
                    revid=rev.get("revid", 0),
                    wikitext=wikitext,
                    timestamp=rev.get("timestamp", ""),
                ))

            if i + batch_size < len(pageids):
                await asyncio.sleep(self.request_delay)

        return results
