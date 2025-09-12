#!/usr/bin/env python3
"""
Test Script for Quality Control System
Tests all components of the quality control pipeline
"""

import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List
from loguru import logger

# Add parent directories to path
sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent.parent.parent / "koi-sensors"))

from quality_control import QualityControl, ContentType, ApprovalStatus


class QualityControlTester:
    """
    Comprehensive test suite for quality control system
    """
    
    def __init__(self):
        """Initialize tester"""
        self.qc = QualityControl()
        self.test_results = []
    
    async def run_all_tests(self):
        """Run all quality control tests"""
        logger.info("Starting Quality Control System Tests\n")
        
        # Initialize database
        await self.qc.initialize_db()
        
        # Run test suite
        await self.test_speculation_detection()
        await self.test_link_validation()
        await self.test_source_checking()
        await self.test_style_scoring()
        await self.test_approval_workflow()
        await self.test_auto_publish()
        await self.test_rollback()
        
        # Print results
        self.print_results()
        
        # Cleanup
        await self.qc.cleanup()
    
    async def test_speculation_detection(self):
        """Test speculation phrase detection"""
        logger.info("Testing speculation detection...")
        
        test_cases = [
            {
                'name': 'Clean content',
                'content': {
                    'posts': [
                        {'content': 'Regen Network announced a new partnership today.'},
                        {'content': 'The carbon credit program has issued 1.2M credits.'}
                    ]
                },
                'expected_speculation': False
            },
            {
                'name': 'Speculative content',
                'content': {
                    'posts': [
                        {'content': 'This partnership might be game-changing.'},
                        {'content': 'We believe this could lead to major growth.'}
                    ]
                },
                'expected_speculation': True
            },
            {
                'name': 'Mixed content',
                'content': {
                    'posts': [
                        {'content': 'Regen Network confirmed the launch.'},
                        {'content': 'This potentially represents a major shift.'}
                    ]
                },
                'expected_speculation': True
            }
        ]
        
        for test in test_cases:
            result = self.qc._check_speculation(test['content'])
            has_speculation = len(result['issues']) > 0
            
            passed = has_speculation == test['expected_speculation']
            self.test_results.append({
                'test': f"Speculation: {test['name']}",
                'passed': passed,
                'details': f"Found {len(result['issues'])} speculation phrases"
            })
            
            if passed:
                logger.success(f"  ✓ {test['name']}: Correctly detected speculation={has_speculation}")
            else:
                logger.error(f"  ✗ {test['name']}: Expected speculation={test['expected_speculation']}, got {has_speculation}")
    
    async def test_link_validation(self):
        """Test link validation"""
        logger.info("\nTesting link validation...")
        
        test_cases = [
            {
                'name': 'Valid links',
                'content': {
                    'posts': [
                        {'content': 'Learn more at https://regen.network'},
                        {'content': 'Read the docs at https://docs.regen.network'}
                    ]
                },
                'expected_valid': True
            },
            {
                'name': 'Invalid links',
                'content': {
                    'posts': [
                        {'content': 'Visit https://this-domain-definitely-does-not-exist-123456.com'}
                    ]
                },
                'expected_valid': False
            },
            {
                'name': 'No links',
                'content': {
                    'posts': [
                        {'content': 'This post has no links at all.'}
                    ]
                },
                'expected_valid': True  # No links means no failures
            }
        ]
        
        for test in test_cases:
            result = await self.qc._verify_links(test['content'])
            is_valid = len(result['failures']) == 0
            
            passed = is_valid == test['expected_valid']
            self.test_results.append({
                'test': f"Links: {test['name']}",
                'passed': passed,
                'details': f"Valid: {result['valid_links']}/{result['total_links']}"
            })
            
            if passed:
                logger.success(f"  ✓ {test['name']}: Links valid={is_valid}")
            else:
                logger.error(f"  ✗ {test['name']}: Expected valid={test['expected_valid']}, got {is_valid}")
    
    async def test_source_checking(self):
        """Test source citation checking"""
        logger.info("\nTesting source checking...")
        
        test_cases = [
            {
                'name': 'With sources',
                'content': {
                    'posts': [
                        {'content': 'According to the Regen Registry, credits increased 15%.'},
                        {'content': 'Data from our governance forum shows strong support.'}
                    ]
                },
                'expected_sources': True
            },
            {
                'name': 'No sources',
                'content': {
                    'posts': [
                        {'content': 'Credits have increased significantly.'},
                        {'content': 'The community shows strong support.'}
                    ]
                },
                'expected_sources': False
            },
            {
                'name': 'URL as source',
                'content': {
                    'posts': [
                        {'content': 'See details at https://regen.network/blog/update'}
                    ]
                },
                'expected_sources': True
            }
        ]
        
        for test in test_cases:
            result = self.qc._check_sources(test['content'])
            has_sources = result['has_sources']
            
            passed = has_sources == test['expected_sources']
            self.test_results.append({
                'test': f"Sources: {test['name']}",
                'passed': passed,
                'details': f"Sources found: {result['source_count']}"
            })
            
            if passed:
                logger.success(f"  ✓ {test['name']}: Has sources={has_sources}")
            else:
                logger.error(f"  ✗ {test['name']}: Expected sources={test['expected_sources']}, got {has_sources}")
    
    async def test_style_scoring(self):
        """Test style guide compliance scoring"""
        logger.info("\nTesting style scoring...")
        
        test_cases = [
            {
                'name': 'Professional content',
                'content': {
                    'posts': [
                        {'content': 'Regen Network announced the launch of a new carbon credit methodology.'},
                        {'content': 'The verified results demonstrate significant impact.'}
                    ]
                },
                'expected_min_score': 0.8
            },
            {
                'name': 'Unprofessional content',
                'content': {
                    'posts': [
                        {'content': 'THIS IS AMAZING!!! OMG!!!'},
                        {'content': 'Gonna be awesome, lol!'}
                    ]
                },
                'expected_max_score': 0.5
            },
            {
                'name': 'Mixed style',
                'content': {
                    'posts': [
                        {'content': 'Regen Network confirmed the partnership.'},
                        {'content': 'This is gonna be HUGE!!!'}
                    ]
                },
                'expected_range': (0.4, 0.7)
            }
        ]
        
        for test in test_cases:
            score = await self.qc.calculate_style_score(test['content'])
            
            if 'expected_min_score' in test:
                passed = score >= test['expected_min_score']
                expectation = f">= {test['expected_min_score']}"
            elif 'expected_max_score' in test:
                passed = score <= test['expected_max_score']
                expectation = f"<= {test['expected_max_score']}"
            else:
                passed = test['expected_range'][0] <= score <= test['expected_range'][1]
                expectation = f"in {test['expected_range']}"
            
            self.test_results.append({
                'test': f"Style: {test['name']}",
                'passed': passed,
                'details': f"Score: {score:.2f} (expected {expectation})"
            })
            
            if passed:
                logger.success(f"  ✓ {test['name']}: Score {score:.2f} {expectation}")
            else:
                logger.error(f"  ✗ {test['name']}: Score {score:.2f} not {expectation}")
    
    async def test_approval_workflow(self):
        """Test the approval workflow"""
        logger.info("\nTesting approval workflow...")
        
        # Create test content
        test_content = {
            'posts': [
                {'content': 'Regen Network announced a major partnership with Climate Collective.'},
                {'content': 'This collaboration will scale regenerative agriculture practices.'},
                {'content': 'Learn more at https://regen.network'}
            ]
        }
        
        # Submit for review
        review_id = await self.qc.submit_for_review(
            content=test_content,
            content_type=ContentType.DAILY_THREAD,
            content_id='test-thread-001'
        )
        
        # Get review
        review = await self.qc.get_review(review_id)
        
        self.test_results.append({
            'test': 'Workflow: Submit for review',
            'passed': review is not None,
            'details': f"Review ID: {review_id[:8]}..."
        })
        
        if review:
            logger.success(f"  ✓ Review created: {review_id[:8]}...")
            
            # Test approval
            success = await self.qc.approve_content(
                review_id=review_id,
                reviewer="Test Suite",
                notes="Approved by automated test"
            )
            
            self.test_results.append({
                'test': 'Workflow: Approve content',
                'passed': success,
                'details': f"Approval status: {success}"
            })
            
            if success:
                logger.success(f"  ✓ Content approved successfully")
            else:
                logger.error(f"  ✗ Failed to approve content")
            
            # Verify status change
            updated_review = await self.qc.get_review(review_id)
            status_changed = updated_review['approval_status'] == ApprovalStatus.APPROVED.value
            
            self.test_results.append({
                'test': 'Workflow: Status update',
                'passed': status_changed,
                'details': f"Status: {updated_review['approval_status']}"
            })
            
            if status_changed:
                logger.success(f"  ✓ Status updated to approved")
            else:
                logger.error(f"  ✗ Status not updated correctly")
        else:
            logger.error(f"  ✗ Failed to create review")
    
    async def test_auto_publish(self):
        """Test auto-publish functionality"""
        logger.info("\nTesting auto-publish...")
        
        # Temporarily enable auto-publish for testing
        original_enabled = self.qc.auto_publish_enabled
        self.qc.auto_publish_enabled = True
        self.qc.auto_publish_after_days = 0  # Immediate for testing
        
        # Create and approve multiple pieces of content
        for i in range(3):
            content = {
                'posts': [
                    {'content': f'Test content {i+1} for auto-publish testing.'}
                ]
            }
            
            review_id = await self.qc.submit_for_review(
                content=content,
                content_type=ContentType.DAILY_THREAD,
                content_id=f'auto-test-{i+1}'
            )
            
            # Approve it
            await self.qc.approve_content(
                review_id=review_id,
                reviewer="Test Suite",
                notes="Auto-publish test"
            )
        
        # Trigger auto-publish
        published = await self.qc.auto_publish_check()
        
        self.test_results.append({
            'test': 'Auto-publish: Trigger',
            'passed': len(published) > 0,
            'details': f"Published {len(published)} items"
        })
        
        if published:
            logger.success(f"  ✓ Auto-published {len(published)} items")
        else:
            logger.warning(f"  ⚠ No items auto-published (may need history)")
        
        # Restore original setting
        self.qc.auto_publish_enabled = original_enabled
    
    async def test_rollback(self):
        """Test rollback functionality"""
        logger.info("\nTesting rollback...")
        
        # Create and publish content
        content = {
            'posts': [
                {'content': 'Content to be rolled back for testing.'}
            ]
        }
        
        review_id = await self.qc.submit_for_review(
            content=content,
            content_type=ContentType.DAILY_THREAD,
            content_id='rollback-test-001'
        )
        
        # Approve and "publish" it
        await self.qc.approve_content(
            review_id=review_id,
            reviewer="Test Suite",
            notes="For rollback testing"
        )
        
        # Manually set to published status
        await self.qc._update_review_status(
            review_id=review_id,
            new_status=ApprovalStatus.PUBLISHED,
            reviewer="Test Suite",
            notes="Published for testing"
        )
        
        # Perform rollback
        success = await self.qc.rollback_publication(
            review_id=review_id,
            reason="Test rollback",
            rolled_back_by="Test Suite"
        )
        
        self.test_results.append({
            'test': 'Rollback: Execute',
            'passed': success,
            'details': f"Rollback status: {success}"
        })
        
        if success:
            logger.success(f"  ✓ Rollback executed successfully")
            
            # Verify status
            review = await self.qc.get_review(review_id)
            is_rolled_back = review['approval_status'] == ApprovalStatus.ROLLED_BACK.value
            
            self.test_results.append({
                'test': 'Rollback: Status update',
                'passed': is_rolled_back,
                'details': f"Status: {review['approval_status']}"
            })
            
            if is_rolled_back:
                logger.success(f"  ✓ Status updated to rolled_back")
            else:
                logger.error(f"  ✗ Status not updated correctly")
        else:
            logger.error(f"  ✗ Failed to execute rollback")
    
    def print_results(self):
        """Print test results summary"""
        logger.info("\n" + "="*50)
        logger.info("TEST RESULTS SUMMARY")
        logger.info("="*50)
        
        passed = sum(1 for r in self.test_results if r['passed'])
        total = len(self.test_results)
        
        for result in self.test_results:
            status = "✓" if result['passed'] else "✗"
            color = "green" if result['passed'] else "red"
            logger.info(f"[{color}]{status}[/{color}] {result['test']}: {result['details']}")
        
        logger.info("\n" + "="*50)
        percentage = (passed / total * 100) if total > 0 else 0
        
        if percentage == 100:
            logger.success(f"All tests passed! ({passed}/{total})")
        elif percentage >= 80:
            logger.warning(f"Most tests passed: {passed}/{total} ({percentage:.1f}%)")
        else:
            logger.error(f"Many tests failed: {passed}/{total} ({percentage:.1f}%)")


async def main():
    """Main entry point"""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
        level="INFO"
    )
    
    # Add custom log levels
    logger.level("SUCCESS", no=25, color="<green>")
    logger.success = lambda message: logger.log("SUCCESS", message)
    
    # Run tests
    tester = QualityControlTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())