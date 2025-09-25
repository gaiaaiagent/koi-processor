#!/usr/bin/env python3
"""
Enhanced Daily Content Curator with LLM Integration
Uses RAG approach to intelligently process ALL content from past 24 hours
"""

import asyncio
import asyncpg
import httpx
import json
import yaml
import re
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from loguru import logger
import os
import hashlib
import openai
from openai import AsyncOpenAI

# Configure logging
logger.add("logs/daily_curator_llm.log", rotation="10 MB", retention="7 days")


class DailyCuratorLLM:
    """
    Enhanced Daily Content Curator that uses LLMs to intelligently process content
    """

    def __init__(self, config_path: str = "config/curator_config.yaml"):
        """Initialize the Daily Curator with LLM support"""
        self.config = self._load_config(config_path)
        self.db_url = self.config.get('database_url', os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza'))

        # LLM Configuration
        self.openai_client = AsyncOpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        )
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4-turbo-preview')

        # Content parameters
        self.max_thread_posts = 5
        self.min_thread_posts = 3
        self.tweet_max_length = 280

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file, 'r') as f:
                return yaml.safe_load(f)
        else:
            logger.warning(f"Config file {config_path} not found, using defaults")
            return {}

    def clean_url(self, url: str) -> str:
        """Clean malformed GitHub URLs and other URL issues"""
        if not url:
            return url

        # Fix GitHub sensor URLs that include temporary directory paths
        if 'github.com' in url and 'github_sensor_' in url:
            # Remove the github_sensor_XXXXX directory from the path
            url = re.sub(r'/github_sensor_[^/]+/', '/', url)

        return url

    async def get_all_24h_content(self) -> List[Dict[str, Any]]:
        """
        Get ALL content PUBLISHED in the past 24 hours
        Uses published_at date (when content was actually created)
        NOT when it was ingested into KOI
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
                      -- ONLY content actually PUBLISHED in last 24 hours
                      AND published_at IS NOT NULL
                      AND published_at >= NOW() - INTERVAL '24 hours'
                      AND published_at <= NOW()
                      -- Require reasonable confidence in the published date
                      AND published_confidence >= 0.6
                    ORDER BY published_at DESC
                """

                rows = await conn.fetch(query)
                return [dict(row) for row in rows]

    async def summarize_content_batch(self, content_items: List[Dict]) -> str:
        """
        Use LLM to summarize a batch of content items
        """
        # Prepare content for summarization
        content_texts = []
        for item in content_items:
            content_data = item.get('content', {})
            if isinstance(content_data, dict):
                text = content_data.get('text', '') or content_data.get('content', '')
            else:
                text = str(content_data)

            if text and len(text.strip()) > 10:
                source = item.get('source_sensor', 'unknown')
                timestamp = item.get('published_at', item.get('created_at', ''))
                content_texts.append(f"[{source}] {text[:500]}")

        if not content_texts:
            return ""

        prompt = f"""Summarize these Regen Network updates from the past 24 hours.
Focus on key themes, important governance proposals, new carbon credits, and community discussions:

{chr(10).join(content_texts[:20])}  # Limit to prevent token overflow

Provide a concise summary highlighting:
1. Most important developments
2. Key governance activities
3. New ecological credits or batches
4. Notable community discussions
5. Technical updates

Keep the summary under 300 words."""

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert at summarizing blockchain and environmental finance updates."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=400
            )

            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error summarizing content: {e}")
            return ""

    async def generate_tweet_from_content(self, content_items: List[Dict], tweet_type: str, position: int, stats: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Generate a specific tweet using LLM based on all content with source tracking

        Args:
            content_items: All content from past 24h
            tweet_type: Type of tweet (headline, stats, highlight, community, cta)
            position: Position in thread (1-5)
            stats: Optional ledger statistics
        Returns:
            Dict with 'content' and 'sources' keys
        """
        # Track sources for this tweet
        sources = []

        # First, get a summary if content is large
        if len(content_items) > 50:
            summary = await self.summarize_content_batch(content_items)
            context = f"Summary of {len(content_items)} updates:\n{summary}"
            # Add source tracking for large batches
            sensor_counts = {}
            for item in content_items:
                sensor = item.get('source_sensor', 'unknown')
                sensor_counts[sensor] = sensor_counts.get(sensor, 0) + 1
            sources.append({
                'type': 'aggregated',
                'description': f"Aggregated from {len(content_items)} items",
                'sensor_breakdown': sensor_counts
            })
        else:
            # Use full content for smaller sets
            content_texts = []
            for item in content_items[:30]:  # Limit to prevent token overflow
                content_data = item.get('content', {})
                metadata = item.get('metadata', {})

                if isinstance(content_data, dict):
                    text = content_data.get('text', '') or str(content_data.get('content', ''))
                else:
                    text = str(content_data)

                if text and len(text.strip()) > 10:
                    content_texts.append(text[:200])
                    # Track individual sources
                    source_info = {
                        'sensor': item.get('source_sensor', 'unknown'),
                        'event_type': item.get('event_type', 'unknown'),
                        'published_at': item.get('published_at', '').split('T')[0] if item.get('published_at') else 'unknown'
                    }

                    # Add URL if available
                    if isinstance(metadata, dict):
                        if metadata.get('url'):
                            source_info['url'] = self.clean_url(metadata.get('url'))
                        elif metadata.get('link'):
                            source_info['url'] = self.clean_url(metadata.get('link'))

                    sources.append(source_info)

            context = "Recent updates:\n" + "\n".join(content_texts)

        # Create type-specific prompts
        prompts = {
            'headline': f"""Create an engaging headline tweet for Regen Network's daily thread.
Context: {context}

Requirements:
- Start with 🌱 emoji
- Include #RegenNetwork hashtag
- Highlight the most important development from the past 24 hours
- Make it compelling and informative
- Use exactly 260-280 characters
- Focus on impact and call to action""",

            'stats': f"""Create a statistics tweet highlighting Regen Network's 24h metrics.
Context: {context}

Ledger Statistics:
{stats.get('ledger_summary', '') if stats else 'No ledger data available'}
- Active Proposals: {stats.get('active_proposals', 0) if stats else 0}
- New Credit Batches: {stats.get('new_credits', 0) if stats else 0}
- Marketplace Orders: {stats.get('marketplace_volume', 0) if stats else 0}
- Credit Classes: {stats.get('credit_classes', 0) if stats else 0}

Requirements:
- Start with 📊 emoji
- Include specific numbers from the ledger statistics above
- Mention new credits, proposals, validator count, or volume
- Make numbers compelling with context
- Use exactly 260-280 characters
- Include #ReFi hashtag""",

            'governance': f"""Create a governance-focused tweet about Regen Network proposals and decisions.
Context: {context}

Governance Activity:
- Active Proposals: {stats.get('active_proposals', 0) if stats else 0}
- Total Proposals: {stats.get('total_proposals', 0) if stats else 0}
{f"Active Proposal Titles: {', '.join(stats.get('active_proposal_titles', [])[:2])}" if stats and stats.get('active_proposal_titles') else ''}

Requirements:
- Start with 🗳️ emoji
- Highlight active proposals or recent votes (use the data above)
- Emphasize community participation
- Include specific proposal details if available
- Use exactly 260-280 characters
- Make it actionable""",

            'community': f"""Create a community highlight tweet showcasing discussions and contributions.
Context: {context}

IMPORTANT RULES:
- NEVER invent fake usernames or handles (like @EcoWarrior123)
- ONLY mention specific users if they appear in the context above
- If no specific users are mentioned in context, focus on topics and themes instead
- Do NOT make up community members that don't exist

Requirements:
- Start with 💬 emoji
- Highlight interesting discussions or contributions from the actual context
- Focus on real topics and themes, not invented users
- Encourage participation
- Use exactly 260-280 characters
- Include #RegenNetwork""",

            'cta': f"""Create a call-to-action tweet to close the daily thread.
Context: {context}

Requirements:
- Start with 🌍 emoji
- Include links to regen.network and discord
- Summarize the day's key theme
- Inspiring message about regenerative finance
- Use exactly 260-280 characters
- End with #ReFi #ClimateAction"""
        }

        prompt = prompts.get(tweet_type, prompts['headline'])

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": """You are a social media expert for Regen Network, crafting engaging tweets about regenerative finance and ecological credits.
                    CRITICAL RULES:
                    1. Always write tweets that are exactly 260-280 characters
                    2. NEVER invent fake usernames, handles, or specific names that don't appear in the provided context
                    3. Only reference actual data, statistics, and information from the context provided
                    4. If you don't have specific data, use general language instead of making up specifics"""},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=100
            )

            tweet = response.choices[0].message.content.strip()

            # For dashboard display, we want the full content
            # Only truncate if tweet is extremely long (likely an error)
            if len(tweet) > 500:
                tweet = tweet[:497] + "..."
            elif len(tweet) < 200:
                # Too short, add context
                tweet += " Join us in building regenerative finance infrastructure. #RegenNetwork"

            # Add ledger as source for stats tweets
            if tweet_type == 'stats' and stats:
                sources.append({
                    'type': 'ledger',
                    'description': 'Regen Network ledger statistics',
                    'sensor': 'ledger_sensor'
                })

            return {
                'content': tweet,
                'sources': sources
            }

        except Exception as e:
            logger.error(f"Error generating tweet: {e}")
            # Return a fallback tweet based on type with empty sources
            fallbacks = {
                'headline': "🌱 Another day building regenerative finance on #RegenNetwork! Check out today's updates on ecological credits, governance, and our growing community. Together we're creating a sustainable future through blockchain innovation. 🌍",
                'stats': "📊 Regen Network continues to grow: Active validators securing the network, credit classes expanding, and marketplace activity ongoing. Join us in building the infrastructure for planetary regeneration. #ReFi",
                'governance': "🗳️ Governance is at the heart of Regen Network. Every voice matters in shaping regenerative finance. Join our community discussions and help guide the future of ecological credits. Your participation makes a difference! #RegenNetwork",
                'community': "💬 Our community continues to drive innovation in regenerative finance. From technical developments to ecological insights, every contribution shapes our collective impact. Join the conversation! #RegenNetwork",
                'cta': "🌍 Ready to be part of the regeneration movement? Learn more at regen.network and join our Discord community. Together, we're proving that finance can heal the planet. #ReFi #ClimateAction"
            }
            return {
                'content': fallbacks.get(tweet_type, fallbacks['headline']),
                'sources': [{'type': 'fallback', 'description': 'Generated fallback due to error'}]
            }

    async def analyze_content_themes(self, content_items: List[Dict]) -> Dict[str, Any]:
        """
        Use LLM to analyze themes and extract key insights from all content
        """
        # Prepare content for analysis
        content_summary = await self.summarize_content_batch(content_items)

        prompt = f"""Analyze these Regen Network updates and extract key themes:

{content_summary}

Provide a JSON response with:
{{
    "main_themes": ["theme1", "theme2", "theme3"],
    "key_governance_items": ["item1", "item2"],
    "new_credits_summary": "summary of new ecological credits",
    "community_highlights": ["highlight1", "highlight2"],
    "trending_topics": ["topic1", "topic2"],
    "recommended_focus": "what should be the main focus of today's thread"
}}"""

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert analyst for blockchain and environmental finance. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
                max_tokens=300
            )

            return json.loads(response.choices[0].message.content)

        except Exception as e:
            logger.error(f"Error analyzing themes: {e}")
            return {
                "main_themes": ["regenerative finance", "ecological credits"],
                "key_governance_items": [],
                "trending_topics": []
            }

    async def get_ledger_stats(self) -> Dict[str, Any]:
        """Get blockchain statistics from Regen ledger"""
        try:
            import sys
            sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from services.regen_ledger import RegenLedgerClient

            ledger_client = RegenLedgerClient()
            summary = await ledger_client.get_ledger_summary()

            # Format stats for daily thread - include ALL comprehensive data
            stats = {
                'new_credits': summary['statistics'].get('total_credit_batches', 0),
                'active_proposals': summary['statistics'].get('active_proposals', 0),
                'total_proposals': summary['statistics'].get('total_proposals', 0),
                'validator_count': summary['statistics'].get('total_validators', 0),
                'marketplace_volume': summary['statistics'].get('active_sell_orders', 0),
                'credit_classes': summary['statistics'].get('total_credit_classes', 0),
                'baskets': summary['statistics'].get('total_baskets', 0),
                'total_validators': summary['statistics'].get('total_validators', 0),
                'ibc_channels': summary['statistics'].get('ibc_channels', 0),
                'avg_tx_per_block': summary['statistics'].get('avg_tx_per_block', 0),
                'total_bonded': summary['statistics'].get('total_bonded', '0'),
                'ledger_summary': await ledger_client.format_for_daily_post()
            }

            # Add proposal details if any
            if summary.get('proposals'):
                active = [p for p in summary['proposals'] if p.get('status') == 'VOTING_PERIOD']
                if active:
                    stats['active_proposal_titles'] = [p['title'] for p in active[:2]]

            return stats
        except Exception as e:
            logger.error(f"Error getting ledger stats: {e}")
            return {
                'new_credits': 0,
                'active_proposals': 0,
                'validator_count': 75,
                'marketplace_volume': 0,
                'ledger_summary': ''
            }

    async def generate_daily_thread(self) -> Dict[str, Any]:
        """
        Generate a daily thread using LLM-powered content curation
        """
        logger.info("Generating LLM-powered daily thread...")

        # Get ALL content from past 24 hours
        all_content = await self.get_all_24h_content()
        logger.info(f"Found {len(all_content)} content items from past 24 hours")

        # Get ledger stats
        stats = await self.get_ledger_stats()

        # Analyze themes and extract insights
        themes = await self.analyze_content_themes(all_content)
        logger.info(f"Identified themes: {themes.get('main_themes', [])}")

        # Build thread structure
        thread = {
            'thread_id': str(uuid.uuid4()),  # Add a unique ID for the thread
            'thread_date': datetime.now(timezone.utc).isoformat(),
            'posts': [],
            'metadata': {
                'content_sources': {
                    'total_content_24h': len(all_content),
                    'themes': themes.get('main_themes', []),
                    'trending': themes.get('trending_topics', [])
                },
                'stats': stats,
                'llm_analysis': themes
            }
        }

        # Generate tweets based on content and themes

        # Post 1: Dynamic headline based on main theme
        headline_result = await self.generate_tweet_from_content(all_content, 'headline', 1, stats)
        thread['posts'].append({
            'type': 'headline',
            'content': headline_result['content'],
            'sources': headline_result.get('sources', []),
            'metadata': {'position': 1, 'generated_by': 'llm'}
        })

        # Post 2: Stats or governance based on what's most active
        if themes.get('key_governance_items') or stats.get('active_proposals', 0) > 0:
            tweet_result = await self.generate_tweet_from_content(all_content, 'governance', 2, stats)
            tweet_type = 'governance'
        else:
            tweet_result = await self.generate_tweet_from_content(all_content, 'stats', 2, stats)
            tweet_type = 'stats'

        thread['posts'].append({
            'type': tweet_type,
            'content': tweet_result['content'],
            'sources': tweet_result.get('sources', []),
            'metadata': {'position': 2, 'generated_by': 'llm'}
        })

        # Post 3: Community highlight
        community_result = await self.generate_tweet_from_content(all_content, 'community', 3, stats)
        thread['posts'].append({
            'type': 'community',
            'content': community_result['content'],
            'sources': community_result.get('sources', []),
            'metadata': {'position': 3, 'generated_by': 'llm'}
        })

        # Post 4: Additional highlight based on themes
        if themes.get('new_credits_summary') or stats.get('new_credits', 0) > 0:
            # Focus on credits if there are new ones
            extra_result = await self.generate_tweet_from_content(all_content, 'stats', 4, stats)
        else:
            # Otherwise another community/governance post
            extra_result = await self.generate_tweet_from_content(all_content, 'community', 4, stats)

        thread['posts'].append({
            'type': 'highlight',
            'content': extra_result['content'],
            'sources': extra_result.get('sources', []),
            'metadata': {'position': 4, 'generated_by': 'llm'}
        })

        # Post 5: Call to action
        cta_result = await self.generate_tweet_from_content(all_content, 'cta', 5, stats)
        thread['posts'].append({
            'type': 'cta',
            'content': cta_result['content'],
            'sources': cta_result.get('sources', []),
            'metadata': {'position': 5, 'generated_by': 'llm'}
        })

        logger.info(f"Generated thread with {len(thread['posts'])} posts using LLM")

        # Add a text representation with sources for simple displays
        text_representation = f"ID: {thread.get('thread_id', 'N/A')}\n\n"
        text_representation += f"Daily Thread Posts ({len(thread['posts'])})\n"

        for i, post in enumerate(thread['posts'], 1):
            text_representation += f"\nPost {i}\n"
            text_representation += f"{post['content']}\n"

            # Add sources if available
            if post.get('sources') and len(post['sources']) > 0:
                text_representation += "\nSources:\n"
                for source in post['sources']:
                    if isinstance(source, dict):
                        if source.get('type') == 'aggregated':
                            text_representation += f"  • {source.get('description', 'Aggregated content')}\n"
                            if source.get('sensor_breakdown'):
                                for sensor, count in source['sensor_breakdown'].items():
                                    text_representation += f"    - {sensor}: {count} items\n"
                        elif source.get('type') == 'ledger':
                            text_representation += f"  • {source.get('description', 'Ledger data')}\n"
                        elif source.get('type') == 'fallback':
                            text_representation += f"  • {source.get('description', 'Fallback content')}\n"
                        else:
                            source_text = f"  • {source.get('sensor', 'Unknown')}"
                            if source.get('event_type'):
                                source_text += f" ({source['event_type']})"
                            if source.get('published_at'):
                                source_text += f" - {source['published_at']}"
                            if source.get('url'):
                                source_text += f"\n    URL: {source['url']}"
                            text_representation += source_text + "\n"
                    else:
                        text_representation += f"  • {source}\n"

        thread['text_representation'] = text_representation
        return thread


# Make it compatible with existing system
class DailyCurator(DailyCuratorLLM):
    """Alias for backwards compatibility"""
    pass