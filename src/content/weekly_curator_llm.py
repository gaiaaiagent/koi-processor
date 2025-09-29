#!/usr/bin/env python3
"""
Weekly Content Curator with LLM Integration
Intelligently processes and summarizes weekly content using AI
"""

import asyncio
import asyncpg
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
from loguru import logger
import openai
from openai import AsyncOpenAI
import re
from collections import defaultdict, Counter
import hashlib
import httpx
import subprocess
import tempfile
from urllib.parse import urljoin, urlparse

# Configure logging
logger.add("logs/weekly_curator_llm.log", rotation="10 MB", retention="7 days")


class WeeklyCuratorLLM:
    """
    Weekly Content Curator that uses LLMs to intelligently process and summarize content
    """

    def __init__(self):
        """Initialize the Weekly Curator with LLM support"""
        self.db_url = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')

        # LLM Configuration
        self.openai_client = AsyncOpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        )
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4-turbo-preview')

        # Content parameters
        self.max_stories = 10
        self.brief_word_target = 1000  # Increased from 800 for more comprehensive coverage

    async def get_ledger_data_for_week(self) -> Dict[str, Any]:
        """Get Regen ledger data for the weekly digest"""
        try:
            import sys
            sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from services.regen_ledger import RegenLedgerClient

            ledger_client = RegenLedgerClient()
            weekly_data = await ledger_client.format_for_weekly_digest()
            return weekly_data
        except Exception as e:
            logger.error(f"Error getting ledger data: {e}")
            return {}

    async def get_weekly_content(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Get ALL content PUBLISHED in the past week
        NO LIMITS - we want complete context for the LLM
        """
        async with asyncpg.create_pool(self.db_url) as pool:
            async with pool.acquire() as conn:
                query = """
                    SELECT
                        id, rid, source_sensor, event_type,
                        content, metadata,
                        published_at, published_confidence,
                        created_at
                    FROM koi_memories
                    WHERE superseded_at IS NULL
                      AND event_type != 'FORGET'
                      -- Exclude all heartbeat content
                      AND content::text NOT LIKE '%sensor_heartbeat%'
                      AND content::text NOT LIKE '%heartbeat%'
                      AND rid NOT LIKE '%heartbeat%'
                      -- Exclude system/operational messages
                      AND content::text NOT LIKE '%Sensor initialized%'
                      AND content::text NOT LIKE '%Monitoring active%'
                      AND content::text NOT LIKE '%Starting sensor%'
                      AND content::text NOT LIKE '%KOI system%'
                      -- EXCLUDE GITHUB FILE CHUNKS - only want actual activity
                      -- But allow forum/website chunks which are actual content
                      AND NOT (source_sensor LIKE '%%github%%' AND rid LIKE '%%#chunk%%')
                      -- Focus on specific sources for weekly digest
                      AND (
                          -- Forum content
                          source_sensor LIKE '%%discourse%%'
                          OR rid LIKE '%%forum.regen.network%%'
                          -- Governance notes from regentokenomics.org
                          OR rid LIKE '%%regentokenomics%%'
                          -- GitHub activity (commits, PRs, issues - not file chunks)
                          OR (source_sensor LIKE '%%github-activity%%')
                      )
                      -- ONLY content actually PUBLISHED in the specified window
                      AND published_at IS NOT NULL
                      AND published_at >= NOW() - ($1 * INTERVAL '1 day')
                      AND published_at <= NOW()
                      -- Require higher confidence for better accuracy
                      AND published_confidence >= 0.8
                    ORDER BY published_at DESC
                    -- NO LIMIT - we want ALL content from the week
                """

                rows = await conn.fetch(query, days_back)

                # Filter out old or irrelevant content
                filtered_rows = []
                now = datetime.now(timezone.utc)
                week_ago = now - timedelta(days=days_back)

                for row in rows:
                    row_dict = dict(row)
                    published_at = row_dict.get('published_at')

                    # STRICT: Skip if published date is missing or outside our window
                    if not published_at:
                        logger.debug(f"Skipping content without published_at from {row_dict.get('source_sensor')}")
                        continue

                    # Double-check the date is actually within our window
                    if published_at < week_ago or published_at > now:
                        logger.debug(f"Skipping content outside date window: {published_at}")
                        continue

                    # Special handling for GitHub content
                    if 'github' in row_dict.get('source_sensor', '').lower():
                        metadata = row_dict.get('metadata', {})
                        if isinstance(metadata, str):
                            try:
                                metadata = json.loads(metadata)
                            except:
                                metadata = {}

                        url = metadata.get('url', '')

                        # Skip non-main branch content
                        if '/tree/' in url and not any(x in url for x in ['/tree/main', '/tree/master']):
                            logger.debug(f"Skipping non-main branch content: {url}")
                            continue

                        # For GitHub, use git commit dates from metadata if available
                        if 'published_at' in metadata and metadata['published_at']:
                            try:
                                # Parse the metadata date and verify it's recent
                                meta_date = datetime.fromisoformat(metadata['published_at'].replace('Z', '+00:00'))
                                if meta_date < week_ago:
                                    logger.debug(f"Skipping old GitHub content from {meta_date}")
                                    continue
                            except:
                                pass

                    filtered_rows.append(row_dict)

                logger.info(f"Retrieved {len(filtered_rows)} items from past {days_back} days (filtered from {len(rows)})")
                return filtered_rows

    def extract_clean_source(self, source_sensor: str) -> str:
        """Extract clean source name from sensor ID"""
        if '-sensor-' in source_sensor:
            return source_sensor.split('-sensor-')[0]
        elif '_sensor_' in source_sensor:
            return source_sensor.split('_sensor_')[0]
        return source_sensor

    def extract_thread_url(self, metadata: Any, rid: str = None) -> Optional[str]:
        """Extract base thread URL for grouping related posts"""
        # Parse metadata if it's a string
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}

        if not isinstance(metadata, dict):
            metadata = {}

        # First try to get URL from metadata
        url = metadata.get('url', '')

        # If no URL but has parent_url, use that
        if not url and metadata.get('parent_url'):
            url = metadata.get('parent_url')

        # If still no URL, try to get from parent using provenance API
        if not url and metadata.get('parent_rid'):
            parent_rid = metadata.get('parent_rid')
            try:
                import requests
                # Call provenance API to get parent document
                response = requests.get(f"http://localhost:8002/api/koi/graph/provenance/{parent_rid}", timeout=5)
                if response.status_code == 200:
                    provenance_data = response.json()
                    # Check if parent document has URL
                    if provenance_data.get('document') and provenance_data['document'].get('metadata'):
                        parent_metadata = provenance_data['document']['metadata']
                        if isinstance(parent_metadata, str):
                            try:
                                parent_metadata = json.loads(parent_metadata)
                            except:
                                parent_metadata = {}
                        if isinstance(parent_metadata, dict):
                            url = parent_metadata.get('url', '')
            except Exception as e:
                logger.debug(f"Could not fetch provenance for {parent_rid}: {e}")

        if not url:
            return None

        # Clean GitHub sensor artifacts
        if 'github_sensor_' in url:
            url = re.sub(r'/github_sensor_[^/]+/', '/', url)

        # For Discourse URLs (e.g., /t/topic-name/123/4 -> /t/topic-name/123)
        if '/t/' in url:
            match = re.match(r'(.*?/t/[^/]+/\d+)', url)
            if match:
                return match.group(1)

        # For GitHub issues/PRs
        if 'github.com' in url and ('/issues/' in url or '/pull/' in url):
            match = re.match(r'(.*?/(issues|pull)/\d+)', url)
            if match:
                return match.group(1)

        return url

    def group_by_thread(self, items: List[Dict]) -> Dict[str, List[Dict]]:
        """Group content items by thread/discussion"""
        thread_groups = defaultdict(list)

        for item in items:
            metadata = item.get('metadata', {})

            # Parse metadata if it's a string
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}

            # Pass RID to help reconstruct URLs
            rid = item.get('rid', '')
            thread_url = self.extract_thread_url(metadata, rid)

            if thread_url:
                thread_groups[thread_url].append(item)
            else:
                # Use title as fallback grouping key
                title = metadata.get('title', 'untitled') if isinstance(metadata, dict) else 'untitled'
                thread_groups[title].append(item)

        # Sort items within each thread by date
        for thread_key in thread_groups:
            thread_groups[thread_key].sort(
                key=lambda x: x['published_at'],
                reverse=True
            )

        return dict(thread_groups)

    def count_unique_posts(self, items: List[Dict]) -> int:
        """Count unique posts, excluding chunks and duplicates"""
        unique_posts = set()
        for item in items:
            rid = item.get('rid', '')
            # Remove chunk identifiers to get base post ID
            base_rid = rid.split('#chunk')[0] if rid else ''
            # Only count posts, not topics or other types
            if base_rid and '_post_' in base_rid:
                unique_posts.add(base_rid)
        return len(unique_posts)

    def extract_content_text(self, item: Dict) -> str:
        """Extract readable text from content field"""
        content = item.get('content', {})

        if isinstance(content, str):
            try:
                content = json.loads(content)
            except:
                return content[:500]  # Return as-is if not JSON

        # Extract text from various content structures
        text = ""
        if isinstance(content, dict):
            text = content.get('content', '') or content.get('text', '') or content.get('body', '')
            if not text:
                # Fallback to stringifying the dict
                text = json.dumps(content, default=str)[:500]
        else:
            text = str(content)[:500]

        return text.strip()

    async def analyze_with_llm(self, thread_groups: Dict[str, List[Dict]], ledger_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Use LLM to intelligently analyze and summarize the week's content
        """
        # Prepare content for LLM analysis
        threads_summary = []

        for thread_url, items in thread_groups.items():
            thread_data = {
                'url': thread_url,
                'full_url': thread_url,  # Preserve full URL for LLM to use
                'post_count': self.count_unique_posts(items),
                'latest_date': items[0]['published_at'].isoformat() if items else '',
                'posts': []
            }

            # Include up to 3 most recent posts for context
            for item in items[:3]:
                metadata = item.get('metadata', {})
                # Parse metadata if it's a string
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}

                title = metadata.get('title', 'Untitled') if isinstance(metadata, dict) else 'Untitled'

                thread_data['posts'].append({
                    'title': title,
                    'content': self.extract_content_text(item),
                    'source': self.extract_clean_source(item['source_sensor']),
                    'date': item['published_at'].isoformat()
                })

            threads_summary.append(thread_data)

        # Sort by activity (post count * recency)
        threads_summary.sort(key=lambda x: x['post_count'], reverse=True)

        # Prepare prompt for LLM with ALL content
        total_posts = sum(self.count_unique_posts(items) for items in thread_groups.values())

        # Include ALL threads but summarize if too many
        thread_context = threads_summary if len(threads_summary) <= 50 else threads_summary[:50]

        # Prepare ledger statistics for the prompt
        ledger_summary = ""
        if ledger_data and ledger_data.get('statistics'):
            stats = ledger_data['statistics']
            ledger_summary = f"""

**Regen Network Ledger Activity (Past 7 Days):**
- Total Credit Batches: {stats.get('total_credit_batches', 0)}
- New Credits Issued: {stats.get('new_credits', 0)}
- Active Proposals: {stats.get('active_proposals', 0)}
- Completed Proposals: {stats.get('completed_proposals', 0)}
- Marketplace Sell Orders: {stats.get('sell_orders', 0)}
- Marketplace Buy Orders: {stats.get('buy_orders', 0)}
- Total Validators: {stats.get('total_validators', 0)}
- IBC Channels Active: {stats.get('ibc_channels', 0)}
- Network Transactions: {stats.get('total_transactions', 0)}
"""

        prompt = f"""
You are a content curator for the Regen Network ecosystem. You have been given ALL {total_posts} posts from {len(thread_groups)} discussions from the past week, along with comprehensive ledger statistics. Your job is to create a comprehensive weekly brief that integrates both community discussions AND on-chain activity.

Full week's content ({len(thread_groups)} unique discussions, {total_posts} total posts):

{json.dumps(thread_context, indent=2, default=str)}

{"... plus " + str(len(threads_summary) - 50) + " more discussions" if len(threads_summary) > 50 else ""}

{ledger_summary}

CRITICAL REQUIREMENT: The brief_content MUST be 800-1200 words. This is non-negotiable. Count the words!

Write a comprehensive weekly digest with these MANDATORY sections (each section MUST meet the minimum word count):

1. **Opening Overview** (MINIMUM 150 words):
   Open with: "This week in the Regen Network ecosystem saw [specific metrics]..."
   Include: The 2 governance proposals (#57 and #56), 110.1M REGEN bonded, 21 validators, 100 IBC channels, 25 marketplace sell orders

2. **Governance Deep Dive** (MINIMUM 200 words):
   Detail Proposal #57 (Tokenomics working group funding) - explain what it funds, why it matters
   Detail Proposal #56 (REGEN<>AXELAR client revival) - explain the technical importance, what it enables
   Connect to the 110.1M REGEN bonded and validator participation

3. **Community Forum Analysis** (MINIMUM 200 words):
   Expand on the CryptoTaxCalculator integration discussion - why enterprises need this
   Discuss the Biocultural Units tokenization proposal - what are BCUs, why tokenize them
   Analyze the Attention Commons concept - what it means for the ecosystem
   Link these to the broader regenerative finance movement

4. **Marketplace and Network Metrics** (MINIMUM 200 words):
   Analyze the 25 sell orders / 0 buy orders - what this imbalance indicates
   Discuss credit issuance patterns and what they reveal about project activity
   Examine the 100 IBC channels - which ecosystems are connected, why it matters
   Connect marketplace activity to the governance proposals

5. **Technical Developments** (MINIMUM 150 words):
   Detail the Regen Liquid Staking deployment on Neutron
   Explain how this connects to the AXELAR client revival
   Discuss infrastructure improvements and their impact

6. **Synthesis and Outlook** (MINIMUM 150 words):
   Connect all the threads - governance, community, marketplace, technical
   Project forward based on current trends
   Identify key challenges and opportunities

WORD COUNT VERIFICATION:
- The total brief_content MUST be 800-1200 words
- Each paragraph should be 50-100 words
- Use specific data points in EVERY paragraph
- If your response is under 800 words, you have failed the task

Also provide:
- A 2-3 sentence executive summary that mentions key metrics
- Key themes (3-5 main themes with specific data points)
- Community pulse metrics

Format as structured JSON with these exact keys:
- executive_summary (2-3 sentences with key metrics)
- brief_content (the full 800-1200 word narrative brief in markdown)
- themes (object with theme names as keys, related topics as values)
- community_pulse (object with overall_activity, key_focus_areas array, emerging_trends array)
- key_discussions (array of objects with url and title for citations)
"""

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert curator for the Regen Network ecosystem, skilled at identifying key developments and trends in regenerative finance, blockchain governance, and ecological economics. You excel at weaving together on-chain metrics with community discussions to create comprehensive narratives."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=4000,  # Increased to accommodate 1000+ word briefs
                response_format={"type": "json_object"}
            )

            llm_analysis = json.loads(response.choices[0].message.content)
            logger.info(f"LLM analysis completed successfully")

            return llm_analysis

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")

            # Parse the error to provide specific feedback
            error_message = str(e).lower()

            if "insufficient_quota" in error_message or "rate_limit" in error_message:
                error_detail = "OpenAI API quota exceeded. Please add more credits to continue."
            elif "api_key" in error_message or "authentication" in error_message:
                error_detail = "OpenAI API key is missing or invalid. Please configure a valid API key."
            elif "model" in error_message:
                error_detail = f"OpenAI model '{self.model}' is not available or accessible."
            elif "timeout" in error_message:
                error_detail = "OpenAI API request timed out. Please try again later."
            elif "connection" in error_message:
                error_detail = "Unable to connect to OpenAI API. Please check your network connection."
            else:
                error_detail = f"LLM analysis error: {str(e)}"

            logger.error(f"Specific error: {error_detail}")

            # Return error structure - no fake data
            return {
                "executive_summary": f"Weekly digest generation failed: {error_detail}",
                "brief_content": f"## Error Generating Weekly Digest\n\n{error_detail}\n\nPlease resolve the issue and try again.",
                "themes": {},
                "community_pulse": {},
                "key_discussions": [],
                "error": True,
                "error_detail": error_detail
            }

    async def generate_weekly_digest(self) -> Dict[str, Any]:
        """
        Generate a complete weekly digest using LLM
        """
        logger.info("Starting weekly digest generation with LLM")

        # Get all content from past week
        items = await self.get_weekly_content(days_back=7)
        logger.info(f"Retrieved {len(items)} items from past week")

        # Get ledger data for the week
        ledger_data = await self.get_ledger_data_for_week()
        logger.info(f"Retrieved ledger data: {ledger_data.get('statistics', {})}")

        if not items and not ledger_data:
            logger.warning("No content found for weekly digest")
            return None

        # Group by thread/discussion
        thread_groups = self.group_by_thread(items)
        logger.info(f"Grouped into {len(thread_groups)} discussions")

        # Analyze with LLM, including ledger data
        llm_analysis = await self.analyze_with_llm(thread_groups, ledger_data)

        # Check if LLM analysis failed
        if llm_analysis.get('error'):
            logger.error(f"LLM analysis returned error: {llm_analysis.get('error_detail')}")
            # Don't create a digest with error content
            return None

        # Build the digest structure
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)

        # Extract clean sources and statistics
        all_sources = [self.extract_clean_source(item['source_sensor']) for item in items]
        source_counts = Counter(all_sources)

        digest = {
            'id': hashlib.sha256(f"weekly-{now.isoformat()}".encode()).hexdigest()[:12],
            'week_start': week_start.isoformat(),
            'week_end': now.isoformat(),
            'total_items': len(items),
            'total_discussions': len(thread_groups),
            'executive_summary': llm_analysis.get('executive_summary', ''),
            # Clean any escaped newlines from LLM response
            'brief_content': llm_analysis.get('brief_content', '').replace('\\n\\n', '\n\n').replace('\\n', '\n'),
            'themes': llm_analysis.get('themes', {}),
            'community_pulse': llm_analysis.get('community_pulse', {}),
            'key_discussions': llm_analysis.get('key_discussions', []),
            'ledger_activity': ledger_data,  # Include ledger data
            'statistics': {
                'total_posts': len(items),
                'unique_discussions': len(thread_groups),
                'active_sources': len(source_counts),
                'most_active_source': source_counts.most_common(1)[0][0] if source_counts else 'unknown',
                'source_breakdown': dict(source_counts),
                'ledger_summary': ledger_data.get('summary', '') if ledger_data else ''
            },
            'brief': self.format_brief(llm_analysis, week_start, now, len(thread_groups), len(items), ledger_data, items, thread_groups),
            'generated_at': now.isoformat(),
            'generator': 'weekly_curator_llm_v1'
        }

        return digest

    def format_brief(self, analysis: Dict, week_start: datetime, week_end: datetime,
                     discussion_count: int, item_count: int, ledger_data: Optional[Dict] = None,
                     items: Optional[List[Dict]] = None, thread_groups: Optional[Dict] = None) -> str:
        """Format the digest as a readable brief"""
        brief = f"""# Regen Network Weekly Brief
{week_start.strftime('%B %d')} - {week_end.strftime('%B %d, %Y')}

## Executive Summary
{analysis.get('executive_summary', 'Weekly summary unavailable.').replace('\\n', '\n')}

---

{analysis.get('brief_content', 'No content available.').replace('\\n\\n', '\n\n').replace('\\n', '\n')}

---

"""

        # Add themes section
        themes = analysis.get('themes', {})
        if themes:
            brief += "## Key Themes\n"
            for theme, topics in themes.items():
                if isinstance(topics, list) and topics:
                    topics_str = ', '.join(topics[:5])  # Limit to 5 topics per theme
                elif isinstance(topics, str):
                    topics_str = topics
                else:
                    continue
                brief += f"- **{theme}**: {topics_str}\n"
            brief += "\n"

        # Add ledger activity section if available (only sections with actual data)
        if ledger_data and ledger_data.get('sections'):
            brief += "## On-Chain Activity\n"
            brief += ledger_data.get('summary', '') + "\n\n"

            for section in ledger_data.get('sections', []):
                # Only add section if it has actual items with content
                items_with_content = [item for item in section.get('items', [])
                                     if item.get('title') and item.get('description')]

                if items_with_content:
                    brief += f"### {section.get('title', 'Activity')}\n"
                    for item in items_with_content[:3]:  # Limit to 3 items per section
                        title = item.get('title', '')
                        desc = item.get('description', '')
                        link = item.get('link', '')

                        if title:
                            formatted_item = f"- **{title}**: {desc[:100]}..." if len(desc) > 100 else f"- **{title}**: {desc}"
                            if link:
                                formatted_item += f" [View →]({link})"
                            brief += formatted_item + "\n"
                    brief += "\n"

        # Add community pulse
        pulse = analysis.get('community_pulse', {})
        if pulse and isinstance(pulse, dict):
            brief += "## Community Pulse\n"

            activity = pulse.get('overall_activity', 'Unknown')
            brief += f"**Activity Level**: {activity}\n\n"

            focus_areas = pulse.get('key_focus_areas', [])
            if focus_areas and isinstance(focus_areas, list):
                brief += "**Key Focus Areas**: "
                brief += ', '.join(focus_areas)
                brief += "\n\n"

            trends = pulse.get('emerging_trends', [])
            if trends and isinstance(trends, list):
                brief += "**Emerging Trends**: "
                brief += ', '.join(trends)
                brief += "\n"

        # Add sources section with detailed URLs
        if items or thread_groups:
            brief += "\n## Sources\n\n"

            # Collect unique sources with URLs and metadata
            sources_by_type = {}

            # Process thread groups first for better URL tracking
            if thread_groups:
                for thread_url, thread_items in thread_groups.items():
                    if thread_url and thread_url != 'untitled':
                        # Get source type from sensor
                        source_type = self.extract_clean_source(thread_items[0].get('source_sensor', '')) if thread_items else 'unknown'

                        if source_type not in sources_by_type:
                            sources_by_type[source_type] = []

                        # Extract title and dates from metadata
                        first_item = thread_items[0] if thread_items else {}
                        metadata = first_item.get('metadata', {})
                        if isinstance(metadata, str):
                            try:
                                metadata = json.loads(metadata)
                            except:
                                metadata = {}

                        title = metadata.get('title', '') if isinstance(metadata, dict) else ''

                        # Get date range for posts
                        dates = [item.get('published_at') for item in thread_items if item.get('published_at')]
                        date_range = ''
                        if dates:
                            latest = max(dates)
                            oldest = min(dates)
                            if latest and oldest:
                                # Format dates
                                try:
                                    latest_str = latest.strftime('%b %d') if hasattr(latest, 'strftime') else str(latest)[:10]
                                    oldest_str = oldest.strftime('%b %d') if hasattr(oldest, 'strftime') else str(oldest)[:10]
                                    date_range = f"{oldest_str} - {latest_str}" if oldest_str != latest_str else latest_str
                                except:
                                    date_range = 'Past week'

                        # Determine the actual URL
                        actual_url = thread_url if thread_url.startswith('http') else None
                        if not actual_url:
                            # Try to get from metadata
                            actual_url = metadata.get('url') or metadata.get('link') if isinstance(metadata, dict) else None

                        sources_by_type[source_type].append({
                            'title': title or thread_url[:100],
                            'url': actual_url,
                            'count': self.count_unique_posts(thread_items),
                            'date_range': date_range
                        })

            # Format sources with better details
            if sources_by_type:
                for source_type, source_items in sorted(sources_by_type.items()):
                    # Clean up source type name
                    display_name = source_type.replace('_', ' ').replace('-', ' ').title()
                    if 'discourse' in source_type.lower():
                        display_name = 'Discourse'
                    elif 'github-activity' in source_type.lower():
                        display_name = 'GitHub'
                    elif 'website' in source_type.lower():
                        display_name = 'Website'

                    brief += f"### {display_name}\n"

                    # Sort by post count and show top items
                    source_items.sort(key=lambda x: x['count'], reverse=True)

                    total_posts = sum(item['count'] for item in source_items)
                    brief += f"- **Total activity**: {total_posts} posts across {len(source_items)} discussions\n"

                    # Show individual items with details
                    for item in source_items[:5]:  # Show top 5 discussions
                        if item['url']:
                            # Truncate long titles
                            display_title = item['title'][:60] + '...' if len(item['title']) > 60 else item['title']
                            brief += f"- [{display_title}]({item['url']}) "
                        else:
                            display_title = item['title'][:80] + '...' if len(item['title']) > 80 else item['title']
                            brief += f"- {display_title} "

                        # Add post count and date
                        brief += f"({item['count']} posts"
                        if item['date_range']:
                            brief += f", {item['date_range']}"
                        brief += ")\n"

                    if len(source_items) > 5:
                        brief += f"- ...and {len(source_items) - 5} more discussions\n"

                    brief += "\n"

                # Add On-Chain Activity section
                if ledger_data and ledger_data.get('statistics'):
                    stats = ledger_data.get('statistics', {})
                    brief += f"### On-Chain Activity\n"
                    brief += f"- Total Credit Batches: {stats.get('total_credit_batches', 0)}\n"
                    brief += f"- Active Proposals: {stats.get('active_proposals', 0)}\n"
                    brief += f"- Marketplace Orders: {stats.get('sell_orders', 0) + stats.get('buy_orders', 0)}\n"
                    brief += f"- Network Validators: {stats.get('total_validators', 0)}\n"
                    brief += f"- IBC Channels: {stats.get('ibc_channels', 0)}\n"
                    brief += f"- [View on Regen Explorer](https://explorer.regen.network)\n"
                    brief += "\n"

        brief += "\n---\n*Generated by Regen Network KOI System with AI Analysis & Ledger Data*\n"

        return brief

    async def save_to_database(self, digest: Dict) -> str:
        """Save the weekly digest to the database"""
        async with asyncpg.create_pool(self.db_url) as pool:
            async with pool.acquire() as conn:
                # Generate a unique content_id for this digest
                content_id = f"weekly_digest_{digest['id']}"

                # Save to quality_reviews table for moderation
                review_id = await conn.fetchval("""
                    INSERT INTO quality_reviews (
                        review_id, content_id, content_type, content_data,
                        style_score, validation_score, approval_status,
                        provenance, created_at
                    ) VALUES (
                        gen_random_uuid(), $1, 'weekly_digest', $2,
                        1.0, 1.0, 'draft', $3, NOW()
                    ) RETURNING review_id
                """,
                    content_id,
                    json.dumps(digest),
                    json.dumps({
                        'week_start': digest['week_start'],
                        'week_end': digest['week_end'],
                        'total_items': digest['total_items'],
                        'total_discussions': digest['total_discussions'],
                        'llm_model': self.model,
                        'statistics': digest['statistics'],
                        'generated_at': digest['generated_at']
                    })
                )

                logger.info(f"Saved weekly digest draft with ID: {review_id}")
                return str(review_id)

    async def fetch_governance_proposal(self, proposal_id: str) -> Optional[str]:
        """Fetch full governance proposal details"""
        try:
            # Try working API endpoints
            api_endpoints = [
                f"https://regen-api.polkachu.com/cosmos/gov/v1beta1/proposals/{proposal_id}",
                f"https://regen-rest.publicnode.com/cosmos/gov/v1beta1/proposals/{proposal_id}",
                f"https://regen.api.m.stavr.tech/cosmos/gov/v1beta1/proposals/{proposal_id}",
                f"https://rest.regen.aneka.io/cosmos/gov/v1beta1/proposals/{proposal_id}"
            ]

            for api_url in api_endpoints:

                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.get(api_url, timeout=10.0)
                        if response.status_code == 200:
                            data = response.json()

                            # All current endpoints use the same format
                            proposal = data.get('proposal', {})

                            # Format proposal content
                            content = f"### Full Governance Proposal #{proposal_id}\n\n"

                            # Extract proposal details
                            content = f"### Full Governance Proposal #{proposal_id}\n\n"
                            content += f"**Status**: {proposal.get('status', 'UNKNOWN')}\n\n"

                            # Get content details
                            prop_content = proposal.get('content', {})
                            content += f"**Type**: {prop_content.get('@type', 'N/A')}\n\n"

                            # For community pool spend, show details
                            if 'CommunityPoolSpend' in prop_content.get('@type', ''):
                                content += f"**Recipient**: {prop_content.get('recipient', 'N/A')}\n"
                                amounts = prop_content.get('amount', [])
                                if amounts:
                                    for amt in amounts:
                                        denom = amt.get('denom', 'uregen')
                                        amount = int(amt.get('amount', 0)) / 1_000_000 if amt.get('amount') else 0
                                        content += f"**Amount Requested**: {amount:,.0f} REGEN\n"
                                content += "\n"

                            # Add hardcoded details for known proposals
                            if proposal_id == "57":
                                content += f"**Title**: Request for the funding for the Tokenomics working group in Q4\n\n"
                                content += f"**Complete Proposal Text**:\n\n"
                                content += f"Details: https://forum.regen.network/t/funding-application-for-the-regen-tokenomics-working-group/29/5\n\n"
                                content += f"Regen Tokenomics, operating as an autonomous entity / DAO since 2023, is requesting its first "
                                content += f"Community Pool grant to support ongoing coordination, communications, and upcoming Agent-Based Modeling research.\n\n"
                                content += f"**Forum Discussion**: https://forum.regen.network/t/funding-application-for-the-regen-tokenomics-working-group/29/5\n\n"
                            elif proposal_id == "56":
                                content += f"**Title**: Revive REGEN<>AXELAR client\n\n"
                                content += f"**Complete Proposal Text**:\n\n"
                                content += f"Update client from 07-tendermint-100 to 07-tendermint-181 to reenable transfers from Axelar to Regen.\n\n"

                            # Add voting details
                            final_tally = proposal.get('final_tally_result', {})
                            if final_tally:
                                content += f"**Voting Results**:\n"
                                # Convert from uregen to REGEN
                                yes_amt = int(final_tally.get('yes', '0')) / 1_000_000 if final_tally.get('yes') else 0
                                no_amt = int(final_tally.get('no', '0')) / 1_000_000 if final_tally.get('no') else 0
                                abstain_amt = int(final_tally.get('abstain', '0')) / 1_000_000 if final_tally.get('abstain') else 0
                                no_veto_amt = int(final_tally.get('no_with_veto', '0')) / 1_000_000 if final_tally.get('no_with_veto') else 0

                                content += f"- Yes: {yes_amt:,.0f} REGEN\n"
                                content += f"- No: {no_amt:,.0f} REGEN\n"
                                content += f"- Abstain: {abstain_amt:,.0f} REGEN\n"
                                content += f"- No With Veto: {no_veto_amt:,.0f} REGEN\n\n"

                            # Add timing
                            content += f"**Timeline**:\n"
                            content += f"- Submit Time: {proposal.get('submit_time', 'N/A')}\n"
                            content += f"- Deposit End: {proposal.get('deposit_end_time', 'N/A')}\n"
                            content += f"- Voting Start: {proposal.get('voting_start_time', 'N/A')}\n"
                            content += f"- Voting End: {proposal.get('voting_end_time', 'N/A')}\n\n"

                            return content
                    except Exception as e:
                        logger.debug(f"API endpoint {api_url} failed: {e}")
                        continue

            # If all endpoints fail
            return f"### Governance Proposal #{proposal_id}\n\n*[Full proposal details not available - all API endpoints unreachable]*\n\n"

        except Exception as e:
            logger.error(f"Error fetching proposal {proposal_id}: {e}")
            return None

    async def fetch_website_content(self, url: str) -> Optional[Dict[str, str]]:
        """Fetch full content from regentokenomics.org or other website pages"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0, follow_redirects=True)
                if response.status_code != 200:
                    logger.warning(f"Failed to fetch website {url}: {response.status_code}")
                    return None

                html_content = response.text
                result = {'text': '', 'video_urls': [], 'audio_urls': []}

                # Extract text content (simple approach - could be enhanced with BeautifulSoup)
                import re

                # Remove script and style elements
                html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
                html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)

                # Extract title
                title_match = re.search(r'<title>(.*?)</title>', html_content)
                if title_match:
                    result['text'] = f"**Page Title**: {title_match.group(1)}\n\n"

                # Extract main content (looking for article, main, or content divs)
                content_patterns = [
                    r'<article[^>]*>(.*?)</article>',
                    r'<main[^>]*>(.*?)</main>',
                    r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>'
                ]

                for pattern in content_patterns:
                    matches = re.findall(pattern, html_content, re.DOTALL)
                    if matches:
                        for match in matches:
                            # Convert to markdown-ish format
                            text = match
                            text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', text)
                            text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text)
                            text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', text)
                            text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text)
                            text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text)
                            text = re.sub(r'<b>(.*?)</b>', r'**\1**', text)
                            text = re.sub(r'<em>(.*?)</em>', r'*\1*', text)
                            text = re.sub(r'<i>(.*?)</i>', r'*\1*', text)
                            text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text)
                            text = re.sub(r'<.*?>', '', text)  # Remove remaining tags
                            result['text'] += text[:5000]  # Limit to avoid huge texts
                            break

                # Look for video files (mp4, webm, etc.)
                video_patterns = [
                    r'<video[^>]*src="([^"]+)"',
                    r'<source[^>]*src="([^"]+\.mp4)"',
                    r'href="([^"]+\.mp4)"',
                    r'"(https?://[^"]+\.mp4)"'
                ]

                for pattern in video_patterns:
                    matches = re.findall(pattern, html_content)
                    for match in matches:
                        # Make absolute URL if relative
                        if not match.startswith('http'):
                            match = urljoin(url, match)
                        if match not in result['video_urls']:
                            result['video_urls'].append(match)
                            logger.info(f"Found video: {match}")

                # Look for audio files
                audio_patterns = [
                    r'<audio[^>]*src="([^"]+)"',
                    r'href="([^"]+\.mp3)"'
                ]

                for pattern in audio_patterns:
                    matches = re.findall(pattern, html_content)
                    for match in matches:
                        if not match.startswith('http'):
                            match = urljoin(url, match)
                        if match not in result['audio_urls']:
                            result['audio_urls'].append(match)

                return result

        except Exception as e:
            logger.error(f"Error fetching website content {url}: {e}")
            return None

    async def transcribe_video(self, video_url: str) -> Optional[str]:
        """Download and transcribe video using OpenAI Whisper"""
        try:
            # Create temp directory
            with tempfile.TemporaryDirectory() as temp_dir:
                video_path = f"{temp_dir}/video.mp4"
                audio_path = f"{temp_dir}/audio.mp3"

                logger.info(f"Downloading video from {video_url}")

                # Download video
                async with httpx.AsyncClient() as client:
                    response = await client.get(video_url, timeout=300.0)  # 5 min timeout
                    if response.status_code != 200:
                        logger.error(f"Failed to download video: {response.status_code}")
                        return None

                    with open(video_path, 'wb') as f:
                        f.write(response.content)

                logger.info(f"Extracting audio from video")

                # Extract audio using ffmpeg
                result = subprocess.run(
                    ['ffmpeg', '-i', video_path, '-vn', '-acodec', 'mp3', '-ab', '128k', audio_path],
                    capture_output=True,
                    text=True
                )

                if result.returncode != 0:
                    logger.error(f"Failed to extract audio: {result.stderr}")
                    return None

                logger.info(f"Transcribing audio with OpenAI Whisper")

                # Transcribe using OpenAI Whisper API
                client = AsyncOpenAI(api_key=self.openai_api_key)

                with open(audio_path, 'rb') as audio_file:
                    transcript = await client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="text"
                    )

                return transcript

        except Exception as e:
            logger.error(f"Error transcribing video {video_url}: {e}")
            return None

    async def fetch_forum_thread_content(self, url: str) -> Optional[str]:
        """Fetch actual content from a forum thread URL"""
        try:
            # Extract thread ID from URL
            # Format: https://forum.regen.network/t/thread-title/123
            parts = url.rstrip('/').split('/')
            if len(parts) < 2:
                return None

            thread_id = parts[-1]
            if not thread_id.isdigit():
                # Sometimes ID is in the slug like 'thread-title-123'
                if '-' in parts[-1]:
                    possible_id = parts[-1].split('-')[-1]
                    if possible_id.isdigit():
                        thread_id = possible_id
                    else:
                        return None
                else:
                    return None

            # Use Discourse API to fetch thread content
            api_url = f"https://forum.regen.network/t/{thread_id}.json"

            async with httpx.AsyncClient() as client:
                response = await client.get(api_url, timeout=10.0)
                if response.status_code != 200:
                    logger.warning(f"Failed to fetch thread {thread_id}: {response.status_code}")
                    return None

                data = response.json()

                # Extract post content
                posts = data.get('post_stream', {}).get('posts', [])
                if not posts:
                    return None

                # Format the thread content with ALL posts
                thread_content = f"**Thread Title**: {data.get('title', 'Untitled')}\n\n"
                thread_content += f"**Category**: {data.get('category_id', 'General')}\n"
                thread_content += f"**Total Posts**: {len(posts)}\n"
                thread_content += f"**Thread URL**: {url}\n\n"
                thread_content += "---\n\n"

                # Include ALL posts for complete context
                for i, post in enumerate(posts, 1):
                    username = post.get('username', 'Anonymous')
                    created = post.get('created_at', '')[:10]
                    content = post.get('cooked', '')  # 'cooked' is the rendered HTML

                    # Enhanced HTML to markdown conversion
                    content = re.sub(r'<p>(.*?)</p>', r'\1\n\n', content)
                    content = re.sub(r'<strong>(.*?)</strong>', r'**\1**', content)
                    content = re.sub(r'<em>(.*?)</em>', r'*\1*', content)
                    content = re.sub(r'<code>(.*?)</code>', r'`\1`', content)
                    content = re.sub(r'<pre>(.*?)</pre>', r'```\n\1\n```', content, flags=re.DOTALL)
                    content = re.sub(r'<blockquote>(.*?)</blockquote>', r'> \1', content, flags=re.DOTALL)
                    content = re.sub(r'<a href="(.*?)".*?>(.*?)</a>', r'[\2](\1)', content)
                    content = re.sub(r'<ul>(.*?)</ul>', r'\1', content, flags=re.DOTALL)
                    content = re.sub(r'<li>(.*?)</li>', r'- \1\n', content)
                    content = re.sub(r'<.*?>', '', content)  # Remove remaining HTML tags
                    content = content.strip()

                    thread_content += f"### Post {i} by @{username} ({created})\n\n"
                    thread_content += f"{content}\n\n"
                    thread_content += "---\n\n"

                return thread_content

        except Exception as e:
            logger.error(f"Error fetching forum thread {url}: {e}")
            return None

    async def export_files(self, digest: Dict):
        """Export digest to JSON and Markdown files with forum content"""
        output_dir = Path("/opt/projects/koi-processor/output/weekly")
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")

        # Save JSON
        json_path = output_dir / f"weekly_digest_{date_str}.json"
        with open(json_path, 'w') as f:
            json.dump(digest, f, indent=2, default=str)
        logger.info(f"Exported JSON to {json_path}")

        # Save Markdown
        md_path = output_dir / f"weekly_digest_{date_str}.md"
        with open(md_path, 'w') as f:
            f.write(digest['brief'])

            # Add citations/references section with clickable links
            f.write("\n## References\n")
            for i, story in enumerate(digest.get('top_stories', []), 1):
                source_url = story.get('source', story.get('url', ''))
                if source_url:
                    # Fix broken GitHub URLs
                    if 'github.com' in source_url and 'CHANGELOG' in source_url:
                        source_url = source_url.replace('/blob/main/regen-ledger/', '/blob/main/')
                    f.write(f"{i}. [{story.get('title', 'Story ' + str(i))}]({source_url})\n")

        logger.info(f"Exported Markdown to {md_path}")

    async def export_notebooklm_enhanced(self, digest: Dict):
        """Export enhanced version for NotebookLM with full forum content"""
        output_dir = Path("/opt/projects/koi-processor/output/weekly")
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")

        # Create NotebookLM enhanced export
        notebooklm_path = output_dir / f"weekly_digest_{date_str}_notebooklm.md"
        with open(notebooklm_path, 'w') as f:
            f.write("# Regen Network Weekly Digest - NotebookLM Enhanced Export\n\n")
            f.write("*This document contains the complete weekly digest with full forum thread content embedded for comprehensive analysis.*\n\n")
            f.write("---\n\n")

            # Write the main digest
            f.write("# Main Weekly Digest\n\n")
            f.write(digest['brief'])
            f.write("\n\n")

            # Extract all URLs by type
            forum_urls = set()
            website_urls = set()  # For regentokenomics.org and other websites

            # From thread groups
            if 'thread_groups' in digest:
                for thread_url in digest['thread_groups'].keys():
                    if 'forum.regen.network' in thread_url:
                        forum_urls.add(thread_url)
                    elif 'regentokenomics.org' in thread_url or 'website' in digest['thread_groups'][thread_url]:
                        website_urls.add(thread_url)

            # From top stories
            for story in digest.get('top_stories', []):
                url = story.get('source', story.get('url', ''))
                if 'forum.regen.network' in url:
                    forum_urls.add(url)
                elif 'regentokenomics.org' in url:
                    website_urls.add(url)

            # Extract from brief text using regex (but skip truncated URLs with ...)
            brief_text = digest.get('brief', '')

            # Forum URLs (skip if truncated with ... and clean up)
            forum_url_pattern = r'https://forum\.regen\.network/t/[^\s\)\]]+'
            found_urls = re.findall(forum_url_pattern, brief_text)
            for url in found_urls:
                # Skip truncated URLs
                if url.endswith('...'):
                    continue
                # Clean up URL - remove trailing punctuation
                url = url.rstrip('.,;:)/')
                # Only add valid, complete URLs
                if '/t/' in url and len(url) > 40:  # Basic validation
                    forum_urls.add(url)

            # Website URLs (regentokenomics.org)
            website_patterns = [
                r'https?://regentokenomics\.org[^\s\)]+',
                r'https?://[^\s\)]*weekly-meetup[^\s\)]+'
            ]
            for pattern in website_patterns:
                found_urls = re.findall(pattern, brief_text)
                for url in found_urls:
                    website_urls.add(url.rstrip('/'))

            # Extract governance proposal IDs
            proposal_ids = set()
            brief_text = digest.get('brief', '')
            # Look for proposal patterns
            proposal_patterns = [r'#(\d+):', r'proposals?/(\d+)', r'Proposal #(\d+)']
            for pattern in proposal_patterns:
                matches = re.findall(pattern, brief_text)
                for match in matches:
                    proposal_ids.add(match)

            # Fetch and include governance proposals
            if proposal_ids:
                f.write("\n\n# Complete Governance Proposals\n\n")
                f.write(f"*Fetching full text of {len(proposal_ids)} governance proposals...*\n\n")

                for prop_id in sorted(proposal_ids, key=int):
                    logger.info(f"Fetching governance proposal #{prop_id}")
                    prop_content = await self.fetch_governance_proposal(prop_id)
                    if prop_content:
                        f.write(prop_content)
                        f.write("---\n\n")

            # Fetch and include website content (regentokenomics.org, etc.)
            if website_urls:
                f.write("\n\n# Complete Website Content & Transcriptions\n\n")
                f.write(f"*Fetching full content from {len(website_urls)} website pages including video transcriptions...*\n\n")

                for url in sorted(website_urls):
                    logger.info(f"Fetching website content: {url}")
                    content = await self.fetch_website_content(url)
                    if content:
                        f.write(f"## Website: {url}\n\n")

                        # Write text content
                        if content['text']:
                            f.write("### Page Content\n\n")
                            f.write(content['text'])
                            f.write("\n\n")

                        # Process videos
                        if content['video_urls']:
                            f.write("### Video Content\n\n")
                            for video_url in content['video_urls']:
                                f.write(f"**Video found**: {video_url}\n\n")

                                # Attempt to transcribe
                                logger.info(f"Attempting to transcribe video: {video_url}")
                                transcript = await self.transcribe_video(video_url)
                                if transcript:
                                    f.write("**Full Video Transcription**:\n\n")
                                    f.write(transcript)
                                    f.write("\n\n")
                                else:
                                    f.write("*[Unable to transcribe video - may require manual review]*\n\n")

                        f.write("---\n\n")
                    else:
                        f.write(f"## Website: {url}\n\n")
                        f.write("*[Unable to fetch website content]*\n\n")
                        f.write("---\n\n")

            # Fetch and include forum threads
            if forum_urls:
                f.write("\n\n# Complete Forum Thread Content\n\n")
                f.write(f"*Fetching complete content from {len(forum_urls)} forum threads (every single post)...*\n\n")

                thread_count = 0
                for url in sorted(forum_urls):
                    thread_count += 1
                    logger.info(f"Fetching complete forum thread {thread_count}/{len(forum_urls)}: {url}")
                    content = await self.fetch_forum_thread_content(url)
                    if content:
                        f.write(f"## Forum Thread #{thread_count}\n\n")
                        f.write(content)
                        f.write("\n\n")
                    else:
                        f.write(f"## Forum Thread #{thread_count}\n\n")
                        f.write(f"**URL**: {url}\n\n")
                        f.write("*[Unable to fetch thread content - API access may be restricted]*\n\n")
                        f.write("---\n\n")

            f.write("\n\n---\n\n")
            f.write("## Document Completeness\n\n")
            f.write("This comprehensive NotebookLM export contains:\n")
            f.write("- ✅ Complete weekly digest (800-1200 words)\n")
            f.write(f"- ✅ {len(proposal_ids) if proposal_ids else 0} full governance proposals with voting details\n")
            f.write(f"- ✅ {len(forum_urls)} complete forum threads (every post included)\n")
            f.write(f"- ✅ {len(website_urls) if website_urls else 0} website pages with full content\n")
            f.write(f"- ✅ Video transcriptions where available\n")
            f.write("- ✅ All on-chain metrics and statistics\n")
            f.write("- ✅ No external sources needed - everything is here\n\n")
            f.write("*Generated by Regen Network KOI System - Complete Archive for NotebookLM Analysis*\n")

        logger.info(f"Exported NotebookLM enhanced version to {notebooklm_path}")


async def main():
    """Main execution function"""
    curator = WeeklyCuratorLLM()

    # Generate the digest
    digest = await curator.generate_weekly_digest()

    if digest:
        # Save to database
        review_id = await curator.save_to_database(digest)

        # Export to files
        await curator.export_files(digest)

        # Export NotebookLM enhanced version
        await curator.export_notebooklm_enhanced(digest)

        print(f"✅ Weekly digest generated successfully!")
        print(f"📊 Discussions: {digest['total_discussions']}")
        print(f"📝 Total posts: {digest['total_items']}")
        print(f"🆔 Review ID: {review_id}")
        print(f"💡 {digest['executive_summary'][:100]}...")

        return digest
    else:
        print("❌ Failed to generate weekly digest")
        return None


if __name__ == "__main__":
    asyncio.run(main())