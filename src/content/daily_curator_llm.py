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
                      AND content::text NOT LIKE '%sensor_heartbeat%'
                      AND rid NOT LIKE '%heartbeat%'
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

    async def generate_tweet_from_content(self, content_items: List[Dict], tweet_type: str, position: int) -> str:
        """
        Generate a specific tweet using LLM based on all content

        Args:
            content_items: All content from past 24h
            tweet_type: Type of tweet (headline, stats, highlight, community, cta)
            position: Position in thread (1-5)
        """
        # First, get a summary if content is large
        if len(content_items) > 50:
            summary = await self.summarize_content_batch(content_items)
            context = f"Summary of {len(content_items)} updates:\n{summary}"
        else:
            # Use full content for smaller sets
            content_texts = []
            for item in content_items[:30]:  # Limit to prevent token overflow
                content_data = item.get('content', {})
                if isinstance(content_data, dict):
                    text = content_data.get('text', '') or str(content_data.get('content', ''))
                else:
                    text = str(content_data)
                if text and len(text.strip()) > 10:
                    content_texts.append(text[:200])
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

Requirements:
- Start with 📊 emoji
- Include specific numbers and metrics
- Mention new credits, proposals, validator count, or volume
- Make numbers compelling with context
- Use exactly 260-280 characters
- Include #ReFi hashtag""",

            'governance': f"""Create a governance-focused tweet about Regen Network proposals and decisions.
Context: {context}

Requirements:
- Start with 🗳️ emoji
- Highlight active proposals or recent votes
- Emphasize community participation
- Include specific proposal details if available
- Use exactly 260-280 characters
- Make it actionable""",

            'community': f"""Create a community highlight tweet showcasing discussions and contributions.
Context: {context}

Requirements:
- Start with 💬 emoji
- Highlight interesting discussions or contributions
- Mention specific community members or topics if relevant
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
                    {"role": "system", "content": "You are a social media expert for Regen Network, crafting engaging tweets about regenerative finance and ecological credits. Always write tweets that are exactly 260-280 characters."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=100
            )

            tweet = response.choices[0].message.content.strip()

            # Ensure tweet is within limits
            if len(tweet) > 280:
                tweet = tweet[:277] + "..."
            elif len(tweet) < 200:
                # Too short, add context
                tweet += " Join us in building regenerative finance infrastructure. #RegenNetwork"

            return tweet

        except Exception as e:
            logger.error(f"Error generating tweet: {e}")
            # Fallback to simple tweet
            return "🌱 Regen Network is building the infrastructure for planetary regeneration. Join us in creating transparent, verifiable pathways to ecological health. #RegenNetwork #ReFi"

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
        """Get blockchain statistics"""
        try:
            import sys
            sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from integrations.regen_mcp_client import RegenMCPClient

            mcp_client = RegenMCPClient()
            stats = await mcp_client.query_ledger_stats()
            return stats
        except Exception as e:
            logger.error(f"Error getting ledger stats: {e}")
            return {
                'new_credits': 0,
                'active_proposals': 0,
                'validator_count': 75,
                'marketplace_volume': 0
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
        headline = await self.generate_tweet_from_content(all_content, 'headline', 1)
        thread['posts'].append({
            'type': 'headline',
            'content': headline,
            'metadata': {'position': 1, 'generated_by': 'llm'}
        })

        # Post 2: Stats or governance based on what's most active
        if themes.get('key_governance_items'):
            tweet = await self.generate_tweet_from_content(all_content, 'governance', 2)
            tweet_type = 'governance'
        else:
            tweet = await self.generate_tweet_from_content(all_content, 'stats', 2)
            tweet_type = 'stats'

        thread['posts'].append({
            'type': tweet_type,
            'content': tweet,
            'metadata': {'position': 2, 'generated_by': 'llm'}
        })

        # Post 3: Community highlight
        community_tweet = await self.generate_tweet_from_content(all_content, 'community', 3)
        thread['posts'].append({
            'type': 'community',
            'content': community_tweet,
            'metadata': {'position': 3, 'generated_by': 'llm'}
        })

        # Post 4: Additional highlight based on themes
        if themes.get('new_credits_summary'):
            # Focus on credits if there are new ones
            extra_tweet = await self.generate_tweet_from_content(all_content, 'stats', 4)
        else:
            # Otherwise another community/governance post
            extra_tweet = await self.generate_tweet_from_content(all_content, 'community', 4)

        thread['posts'].append({
            'type': 'highlight',
            'content': extra_tweet,
            'metadata': {'position': 4, 'generated_by': 'llm'}
        })

        # Post 5: Call to action
        cta = await self.generate_tweet_from_content(all_content, 'cta', 5)
        thread['posts'].append({
            'type': 'cta',
            'content': cta,
            'metadata': {'position': 5, 'generated_by': 'llm'}
        })

        logger.info(f"Generated thread with {len(thread['posts'])} posts using LLM")
        return thread


# Make it compatible with existing system
class DailyCurator(DailyCuratorLLM):
    """Alias for backwards compatibility"""
    pass