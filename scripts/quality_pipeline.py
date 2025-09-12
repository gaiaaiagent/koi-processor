#!/usr/bin/env python3
"""
Quality Control Pipeline Integration
Connects Daily Curator → Quality Control → X Bot for complete workflow
"""

import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from loguru import logger

# Add parent directories to path
sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent.parent.parent / "koi-sensors"))

from quality_control import QualityControl, ContentType
from daily_curator import DailyCurator
from weekly_aggregator import WeeklyAggregator
from bots.x_daily_bot import XDailyBot


class QualityPipeline:
    """
    Orchestrates the complete quality control pipeline:
    1. Content generation (Daily Curator / Weekly Aggregator)
    2. Quality validation and review
    3. Approval workflow
    4. Publishing (X Bot for daily, Podcast for weekly)
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize pipeline components"""
        # Load shared configuration
        self.config_path = config_path or Path(__file__).parent.parent / "config" / "quality_config.yaml"
        
        # Initialize components
        self.qc = QualityControl(str(self.config_path))
        self.daily_curator = None
        self.weekly_aggregator = None
        self.x_bot = None
        
        logger.info("Quality Pipeline initialized")
    
    async def initialize(self):
        """Initialize all components"""
        # Initialize quality control database
        await self.qc.initialize_db()
        
        # Initialize curators lazily when needed
        logger.info("Pipeline components ready")
    
    async def process_daily_content(self, test_mode: bool = False) -> Dict[str, Any]:
        """
        Process daily content through quality control
        
        Args:
            test_mode: If True, use test data instead of live
            
        Returns:
            Processing results
        """
        logger.info("Processing daily content through quality pipeline")
        
        try:
            # Step 1: Generate daily content
            if not self.daily_curator:
                self.daily_curator = DailyCurator()
                await self.daily_curator.initialize()
            
            if test_mode:
                # Use test data
                curator_output = self._get_test_daily_content()
            else:
                # Generate real content
                curator_output = await self.daily_curator.create_daily_thread()
            
            # Step 2: Generate X thread from curator output
            if not self.x_bot:
                from bots.x_daily_bot import XDailyBot
                self.x_bot = XDailyBot()
            
            thread_data = await self.x_bot.process_curator_output_data(curator_output)
            
            # Step 3: Submit for quality review
            review_id = await self.qc.submit_for_review(
                content=thread_data,
                content_type=ContentType.DAILY_THREAD,
                content_id=curator_output.get('thread_id')
            )
            
            # Step 4: Get review results
            review = await self.qc.get_review(review_id)
            
            # Step 5: Check if auto-publish eligible
            if review['auto_publish_eligible'] and self.qc.auto_publish_enabled:
                # Auto-publish if quality meets threshold
                logger.info(f"Content eligible for auto-publish: {review_id}")
                published = await self.qc.auto_publish_check()
                if review_id in published:
                    logger.info(f"Content auto-published: {review_id}")
                    # Trigger actual publishing through X Bot
                    await self._publish_to_x(thread_data, review_id)
            else:
                # Requires manual review
                logger.info(f"Content requires manual review: {review_id}")
                logger.info(f"Style score: {review['style_score']:.2f}")
                logger.info(f"Validation score: {review['validation_score']:.2f}")
                
                if review['quality_issues']:
                    logger.warning(f"Quality issues found: {review['quality_issues']}")
            
            return {
                'success': True,
                'review_id': review_id,
                'status': review['approval_status'],
                'auto_published': review_id in (published if 'published' in locals() else []),
                'scores': {
                    'style': review['style_score'],
                    'validation': review['validation_score']
                }
            }
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def process_weekly_content(self, test_mode: bool = False) -> Dict[str, Any]:
        """
        Process weekly digest through quality control
        
        Args:
            test_mode: If True, use test data instead of live
            
        Returns:
            Processing results
        """
        logger.info("Processing weekly content through quality pipeline")
        
        try:
            # Step 1: Generate weekly digest
            if not self.weekly_aggregator:
                self.weekly_aggregator = WeeklyAggregator()
                await self.weekly_aggregator.initialize()
            
            if test_mode:
                # Use test data
                digest_data = self._get_test_weekly_content()
            else:
                # Generate real content
                digest_data = await self.weekly_aggregator.create_weekly_digest()
            
            # Step 2: Submit for quality review
            review_id = await self.qc.submit_for_review(
                content=digest_data,
                content_type=ContentType.WEEKLY_DIGEST,
                content_id=digest_data.get('digest_id')
            )
            
            # Step 3: Get review results
            review = await self.qc.get_review(review_id)
            
            # Step 4: Check if ready for podcast generation
            if review['approval_status'] in ['approved', 'auto_published']:
                logger.info(f"Weekly digest approved for podcast generation: {review_id}")
                # Trigger podcast generation (Session 13)
                # await self._generate_podcast(digest_data, review_id)
            else:
                logger.info(f"Weekly digest requires review: {review_id}")
            
            return {
                'success': True,
                'review_id': review_id,
                'status': review['approval_status'],
                'scores': {
                    'style': review['style_score'],
                    'validation': review['validation_score']
                }
            }
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def _publish_to_x(self, thread_data: Dict[str, Any], review_id: str):
        """
        Publish approved content to X/Twitter
        
        Args:
            thread_data: Thread content
            review_id: Review ID for tracking
        """
        # This will be implemented when X API integration is ready
        logger.info(f"Would publish to X: {review_id}")
        # For now, just save as published draft
        if self.x_bot:
            draft_id = await self.x_bot.storage.save_draft(
                thread=thread_data,
                curator_output={},
                status='published'
            )
            logger.info(f"Saved as published draft: {draft_id}")
    
    def _get_test_daily_content(self) -> Dict[str, Any]:
        """Generate test daily content for pipeline testing"""
        return {
            'thread_id': 'test-daily-001',
            'thread_date': datetime.now(timezone.utc).isoformat(),
            'theme': 'Regenerative Agriculture',
            'headline': 'Regen Network Launches New Carbon Credit Methodology',
            'stat': {
                'metric': 'Carbon Credits Issued',
                'value': '1.2M',
                'change': '+15%',
                'source': 'Regen Registry'
            },
            'stories': [
                {
                    'title': 'New Soil Carbon Methodology Released',
                    'summary': 'Regen Network announced a new methodology for measuring soil carbon sequestration.',
                    'link': 'https://regen.network/blog/soil-carbon-methodology',
                    'source': 'Regen Blog'
                },
                {
                    'title': 'Partnership with Climate Collective',
                    'summary': 'Strategic partnership to scale regenerative agriculture practices.',
                    'link': 'https://regen.network/partnerships/climate-collective',
                    'source': 'Press Release'
                }
            ],
            'cta': 'Learn more about our carbon credit methodologies at regen.network',
            'metadata': {
                'sources_count': 5,
                'confidence': 0.95
            }
        }
    
    def _get_test_weekly_content(self) -> Dict[str, Any]:
        """Generate test weekly content for pipeline testing"""
        return {
            'digest_id': 'test-weekly-001',
            'week_of': datetime.now(timezone.utc).isoformat(),
            'brief': """# Regen Network Weekly Digest

## Executive Summary
This week saw significant developments in regenerative agriculture and carbon credit markets.

## Top Stories
1. **New Soil Carbon Methodology** - Regen Network released an updated methodology for measuring soil carbon sequestration, verified by leading scientists.
2. **Climate Collective Partnership** - Strategic partnership announced to scale regenerative practices across 1M hectares.
3. **Governance Proposal Passed** - Community approved proposal for enhanced credit retirement mechanisms.

## Network Statistics
- Total Credits Issued: 1.2M (+15% WoW)
- Active Projects: 47 (+3)
- Validator Count: 51 (stable)

## Upcoming Events
- Community Call: Tuesday 2pm ET
- Developer Workshop: Thursday 3pm ET

## Links
- [Soil Carbon Methodology](https://regen.network/blog/soil-carbon)
- [Partnership Announcement](https://regen.network/partnerships)
- [Governance Forum](https://forum.regen.network)
""",
            'sources': [
                'https://regen.network/blog',
                'https://forum.regen.network',
                'https://discord.gg/regen-network'
            ],
            'word_count': 982,
            'themes': ['carbon credits', 'governance', 'partnerships'],
            'metadata': {
                'stories_count': 12,
                'sources_count': 8,
                'confidence': 0.92
            }
        }
    
    async def check_pending_approvals(self) -> List[Dict[str, Any]]:
        """
        Check for content pending approval
        
        Returns:
            List of pending reviews
        """
        return await self.qc.get_pending_reviews(limit=20)
    
    async def trigger_auto_publish(self) -> List[str]:
        """
        Manually trigger auto-publish check
        
        Returns:
            List of auto-published review IDs
        """
        return await self.qc.auto_publish_check()
    
    async def rollback_content(self, review_id: str, reason: str) -> bool:
        """
        Rollback published content
        
        Args:
            review_id: Review ID to rollback
            reason: Reason for rollback
            
        Returns:
            Success status
        """
        return await self.qc.rollback_publication(
            review_id=review_id,
            reason=reason,
            rolled_back_by="Pipeline"
        )
    
    async def get_statistics(self, days: int = 7) -> Dict[str, Any]:
        """
        Get pipeline statistics
        
        Args:
            days: Number of days to look back
            
        Returns:
            Statistics dictionary
        """
        return await self.qc.get_approval_stats(days=days)
    
    async def cleanup(self):
        """Clean up resources"""
        await self.qc.cleanup()
        if self.daily_curator:
            await self.daily_curator.cleanup()
        if self.weekly_aggregator:
            await self.weekly_aggregator.cleanup()
        if self.x_bot and hasattr(self.x_bot.storage, 'cleanup'):
            await self.x_bot.storage.cleanup()


async def main():
    """Main entry point for testing"""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
    
    # Initialize pipeline
    pipeline = QualityPipeline()
    await pipeline.initialize()
    
    # Process test content
    logger.info("Processing test daily content...")
    daily_result = await pipeline.process_daily_content(test_mode=True)
    logger.info(f"Daily result: {daily_result}")
    
    logger.info("\nProcessing test weekly content...")
    weekly_result = await pipeline.process_weekly_content(test_mode=True)
    logger.info(f"Weekly result: {weekly_result}")
    
    # Check pending approvals
    pending = await pipeline.check_pending_approvals()
    logger.info(f"\nPending approvals: {len(pending)}")
    
    # Get statistics
    stats = await pipeline.get_statistics()
    logger.info(f"\nPipeline statistics: {stats}")
    
    # Cleanup
    await pipeline.cleanup()
    logger.info("\nPipeline test complete!")


if __name__ == "__main__":
    asyncio.run(main())