#!/usr/bin/env python3
"""
Script to help update remaining sensors with publication date metadata
This script provides templates and examples for updating each sensor type
"""

import sys
from pathlib import Path
from typing import Dict, Any

# Templates for different sensor types
SENSOR_UPDATES = {
    "website": {
        "description": "Website sensors need HTML date extraction",
        "example_code": '''
# Add to imports
sys.path.append('/Users/darrenzal/projects/RegenAI/koi-processor')
from utils.date_extractor import extract_publication_date

# In the document/event creation method:
def create_document(self, url, html_content, text_content):
    # Extract publication date from HTML
    published_at, confidence = extract_publication_date(html_content, 'website')
    
    # Fallback to HTTP headers if available
    if not published_at and response.headers.get('Last-Modified'):
        published_at = parse_http_date(response.headers['Last-Modified'])
        confidence = 0.6
    
    document = {
        "rid": generate_rid(url),
        "content": text_content,
        "metadata": {
            # Add these fields for Daily Curator
            "published_at": published_at.isoformat() if published_at else None,
            "published_confidence": confidence,
            "extracted_from": "meta_tags" if confidence > 0.8 else "last_modified",
            
            # Keep existing metadata
            "url": url,
            # ... other fields
        }
    }
''',
        "files_to_update": [
            "/koi-sensors/sensors/websites/website_sensor.py",
            "/koi-sensors/sensors/websites/enhanced_website_sensor.py"
        ]
    },
    
    "podcast": {
        "description": "Podcast sensors extract dates from RSS feeds",
        "example_code": '''
# RSS feeds typically have pubDate field
def process_episode(self, episode_data):
    # RSS provides publication date
    pub_date = episode_data.get('pubDate') or episode_data.get('published')
    
    # Parse RSS date format
    if pub_date:
        from dateutil import parser
        published_at = parser.parse(pub_date)
    else:
        published_at = None
    
    document = {
        "rid": f"podcast.episode.{episode_id}",
        "content": transcript or description,
        "metadata": {
            # Add these fields for Daily Curator
            "published_at": published_at.isoformat() if published_at else None,
            "published_confidence": 0.95 if published_at else 0.0,
            
            # Keep existing metadata
            "episode_number": episode_data.get('episode'),
            "duration": episode_data.get('duration'),
            # ... other fields
        }
    }
''',
        "files_to_update": [
            "/koi-sensors/sensors/podcast/podcast_sensor.py",
            "/koi-sensors/sensors/podcast/enhanced_podcast_sensor.py"
        ]
    },
    
    "github": {
        "description": "GitHub/GitLab sensors use commit dates or file metadata",
        "example_code": '''
# For code files, use commit date
def process_document(self, file_path, content, repo_info):
    # Try to extract date from markdown content first
    published_at, confidence = None, 0.0
    
    if file_path.endswith('.md'):
        # Try to extract from markdown frontmatter or content
        import re
        date_pattern = r'date:\s*(\d{4}-\d{2}-\d{2})'
        match = re.search(date_pattern, content)
        if match:
            from datetime import datetime
            published_at = datetime.strptime(match.group(1), '%Y-%m-%d')
            confidence = 0.8
    
    # Fallback to last commit date
    if not published_at:
        commit_date = get_last_commit_date(file_path)
        published_at = commit_date
        confidence = 0.7  # Lower confidence for commit dates
    
    document = {
        "rid": f"github.doc.{repo_name}.{file_path}",
        "content": content,
        "metadata": {
            # Add these fields for Daily Curator
            "published_at": published_at.isoformat() if published_at else None,
            "published_confidence": confidence,
            
            # Keep existing metadata
            "repo": repo_name,
            "file_path": file_path,
            "commit_sha": commit_sha,
            # ... other fields
        }
    }
''',
        "files_to_update": [
            "/koi-sensors/sensors/github/github_sensor.py",
            "/koi-sensors/sensors/gitlab/gitlab_sensor.py"
        ]
    },
    
    "notion": {
        "description": "Notion API provides created_time and last_edited_time",
        "example_code": '''
# Notion API provides timestamps
def process_page(self, page_data):
    # Notion provides both created and edited times
    created_time = page_data.get('created_time')
    last_edited_time = page_data.get('last_edited_time')
    
    # Use created_time as publication date
    # Note: Notion times are in ISO format already
    
    document = {
        "rid": f"notion.page.{page_id}",
        "content": extracted_content,
        "metadata": {
            # Add these fields for Daily Curator
            "published_at": created_time,  # Already in ISO format
            "published_confidence": 0.85,  # Good confidence for API data
            "last_modified": last_edited_time,
            
            # Keep existing metadata
            "database": page_data.get('parent', {}).get('database_id'),
            "properties": page_data.get('properties', {}),
            # ... other fields
        }
    }
''',
        "files_to_update": [
            "/koi-sensors/sensors/notion/notion_sensor.py",
            "/koi-sensors/sensors/notion/enhanced_notion_sensor.py"
        ]
    },
    
    "ledger": {
        "description": "Blockchain data has inherent timestamps",
        "example_code": '''
# Blockchain transactions have block timestamps
def process_transaction(self, tx_data, block_data):
    # Block time is the publication time for blockchain data
    block_time = block_data.get('time')  # Unix timestamp
    
    if block_time:
        from datetime import datetime
        published_at = datetime.fromtimestamp(block_time)
    else:
        published_at = None
    
    document = {
        "rid": f"ledger.tx.{tx_hash}",
        "content": tx_description,
        "metadata": {
            # Add these fields for Daily Curator
            "published_at": published_at.isoformat() if published_at else None,
            "published_confidence": 1.0,  # Blockchain timestamps are immutable
            
            # Keep existing metadata
            "tx_hash": tx_hash,
            "block_height": block_data.get('height'),
            "chain_id": chain_id,
            # ... other fields
        }
    }
''',
        "files_to_update": [
            "/koi-sensors/sensors/ledger/ledger_sensor.py"
        ]
    }
}


def print_sensor_update_guide():
    """Print guide for updating sensors"""
    print("="*80)
    print("SENSOR UPDATE GUIDE - Adding Publication Date Metadata")
    print("="*80)
    print()
    print("This guide helps update remaining sensors to include publication date metadata")
    print("for the Daily Content Curator to properly filter content by actual publication date.")
    print()
    print("✅ Already Updated:")
    print("  - Twitter sensor (uses created_at)")
    print("  - Discourse sensor (uses created_at from API)")
    print("  - Medium sensor (extracts published_date)")
    print()
    print("❌ Still Need Updates:")
    print()
    
    for sensor_type, info in SENSOR_UPDATES.items():
        print(f"### {sensor_type.upper()} Sensor")
        print(f"Description: {info['description']}")
        print()
        print("Files to update:")
        for file_path in info['files_to_update']:
            print(f"  - {file_path}")
        print()
        print("Example implementation:")
        print("-" * 40)
        print(info['example_code'])
        print("-" * 40)
        print()


def generate_test_script():
    """Generate a test script for verifying sensor updates"""
    test_script = '''#!/usr/bin/env python3
"""
Test script to verify sensors are properly extracting publication dates
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

async def test_sensor_metadata(sensor_name: str, test_data: dict):
    """Test that a sensor properly extracts publication dates"""
    print(f"\\nTesting {sensor_name} sensor...")
    
    # Import the sensor
    if sensor_name == "website":
        from sensors.websites.website_sensor import WebsiteSensor as Sensor
    elif sensor_name == "podcast":
        from sensors.podcast.podcast_sensor import PodcastSensor as Sensor
    elif sensor_name == "github":
        from sensors.github.github_sensor import GitHubSensor as Sensor
    elif sensor_name == "notion":
        from sensors.notion.notion_sensor import NotionSensor as Sensor
    else:
        print(f"Unknown sensor: {sensor_name}")
        return False
    
    # Create sensor instance
    sensor = Sensor()
    
    # Process test data
    result = await sensor.process_content(test_data)
    
    # Check for required metadata fields
    metadata = result.get('metadata', {})
    
    if 'published_at' not in metadata:
        print(f"  ❌ Missing 'published_at' field")
        return False
    
    if 'published_confidence' not in metadata:
        print(f"  ❌ Missing 'published_confidence' field")
        return False
    
    # Validate date format
    try:
        if metadata['published_at']:
            datetime.fromisoformat(metadata['published_at'])
    except:
        print(f"  ❌ Invalid date format: {metadata['published_at']}")
        return False
    
    # Check confidence range
    confidence = metadata['published_confidence']
    if not (0.0 <= confidence <= 1.0):
        print(f"  ❌ Invalid confidence value: {confidence}")
        return False
    
    print(f"  ✅ Sensor properly configured!")
    print(f"     Published at: {metadata['published_at']}")
    print(f"     Confidence: {confidence}")
    
    return True


async def main():
    """Run all sensor tests"""
    print("="*60)
    print("SENSOR PUBLICATION DATE METADATA TEST")
    print("="*60)
    
    # Test data for each sensor type
    test_cases = {
        "website": {
            "url": "https://example.com/article",
            "html": "<meta property='article:published_time' content='2025-09-11T10:00:00Z'>",
            "content": "Test article content"
        },
        "podcast": {
            "pubDate": "Wed, 11 Sep 2025 10:00:00 GMT",
            "title": "Test Episode",
            "description": "Test description"
        },
        # Add more test cases as needed
    }
    
    results = []
    for sensor_name, test_data in test_cases.items():
        success = await test_sensor_metadata(sensor_name, test_data)
        results.append((sensor_name, success))
    
    # Print summary
    print("\\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for sensor_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{sensor_name:20} {status}")
    
    print(f"\\nTotal: {passed}/{total} passed")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
'''
    
    # Save test script
    test_file = Path("/Users/darrenzal/projects/RegenAI/koi-sensors/test_sensor_metadata.py")
    test_file.write_text(test_script)
    test_file.chmod(0o755)
    
    print(f"\n✅ Test script created: {test_file}")
    print("Run it after updating sensors to verify the changes:")
    print(f"  python {test_file}")


if __name__ == "__main__":
    print_sensor_update_guide()
    
    # Optionally generate test script
    if len(sys.argv) > 1 and sys.argv[1] == "--generate-test":
        generate_test_script()
    else:
        print("\nTo generate a test script, run:")
        print("  python update_remaining_sensors.py --generate-test")