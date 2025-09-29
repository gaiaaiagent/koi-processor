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

Create a comprehensive weekly brief that:
1. Is 800-1200 words in length (aim for 1000 words)
2. Uses a neutral, professional tone suitable for a general audience
3. Weaves together the week's developments into a coherent narrative
4. MUST integrate the ledger statistics into the narrative (credit issuances, proposals, marketplace activity, network metrics)
5. Provides context and analysis, not just summaries
6. Highlights connections between different discussions and on-chain activity
7. Identifies emerging patterns and trends from both community discussions and blockchain data
8. Includes specific examples and details from the discussions and ledger activity
9. DO NOT escape newlines - use proper markdown formatting
10. Use natural paragraph breaks without escaped characters
11. NEVER include technical details like transaction hashes, block numbers, or other cryptographic identifiers
12. Write for a human audience - avoid overly technical blockchain jargon
13. When mentioning on-chain activity, focus on the impact and meaning, not the technical details

The brief should flow naturally, integrating all significant developments from the week. Don't use bullet points or numbered lists - write in prose paragraphs. Focus on telling the story of what happened in the Regen Network ecosystem this week.

Also provide:
- A 2-3 sentence executive summary
- Key themes (3-5 main themes)
- Community pulse metrics

Format as structured JSON with these exact keys:
- executive_summary (2-3 sentences capturing the week's essence)
- brief_content (the full 800-1200 word narrative brief in markdown)
- themes (object with theme names as keys, related topics as values)
- community_pulse (object with overall_activity, key_focus_areas array, emerging_trends array)
- key_discussions (array of objects with url and title for citations)
"""

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert curator for the Regen Network ecosystem, skilled at identifying key developments and trends in regenerative finance, blockchain governance, and ecological economics."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000,  # Increased to accommodate longer briefs
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

    async def export_files(self, digest: Dict):
        """Export digest to JSON and Markdown files"""
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