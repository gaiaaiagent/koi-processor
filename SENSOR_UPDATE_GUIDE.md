# Sensor Update Guide for Publication Date Tracking

## Overview
All sensors need to be updated to extract and pass publication dates in their metadata so the Daily Curator can filter content properly.

## Standard Metadata Format

Every sensor should include these fields in the metadata when sending to KOI Event Bridge:

```python
metadata = {
    # Required fields
    "published_at": "2025-09-11T10:30:00Z",  # ISO 8601 format
    "published_confidence": 0.95,             # 0.0 to 1.0
    
    # Optional but recommended
    "content_hash": "sha256_hash_here",       # For deduplication
    "last_modified": "2025-09-11T10:30:00Z",  # If different from published
    "extracted_from": "meta_tag",             # Source of date extraction
    
    # Keep existing metadata
    **existing_metadata
}
```

## Sensor Update Examples

### 1. Twitter Sensor Update

**File**: `/koi-sensors/sensors/twitter/twitter_sensor.py`

```python
# Add to imports
from datetime import datetime

def process_tweet(tweet_data):
    # Extract existing created_at
    created_at = tweet_data.get('created_at')
    
    # Build KOI event with publication metadata
    event = {
        "rid": f"twitter.tweet.{tweet_id}",
        "content": tweet_data,
        "metadata": {
            "published_at": created_at,  # Twitter provides exact timestamps
            "published_confidence": 0.95,  # High confidence for API data
            "source": "twitter",
            "author": tweet_data.get('author'),
            # ... other metadata
        }
    }
    return event
```

### 2. Website Sensor Update

**File**: `/koi-sensors/sensors/websites/website_sensor.py`

```python
# Add date extraction
from utils.date_extractor import extract_publication_date

def process_webpage(url, html_content):
    # Extract publication date from HTML
    published_at, confidence = extract_publication_date(html_content, 'website')
    
    # Fall back to HTTP Last-Modified header if available
    if not published_at and response.headers.get('Last-Modified'):
        published_at = parse_http_date(response.headers['Last-Modified'])
        confidence = 0.6  # Lower confidence for modification date
    
    event = {
        "rid": f"website.page.{url_hash}",
        "content": extracted_text,
        "metadata": {
            "url": url,
            "published_at": published_at.isoformat() if published_at else None,
            "published_confidence": confidence,
            "extracted_from": "meta_tags" if confidence > 0.8 else "last_modified",
            # ... other metadata
        }
    }
    return event
```

### 3. Discourse Forum Sensor Update

**File**: `/koi-sensors/sensors/discourse/discourse_sensor.py`

```python
def process_forum_post(post_data):
    # Discourse API provides created_at
    created_at = post_data.get('created_at')
    updated_at = post_data.get('updated_at')
    
    event = {
        "rid": f"discourse.post.{post_id}",
        "content": post_data,
        "metadata": {
            "published_at": created_at,
            "published_confidence": 0.95,  # API data is reliable
            "last_modified": updated_at,
            "forum": post_data.get('forum_name'),
            "category": post_data.get('category'),
            # ... other metadata
        }
    }
    return event
```

### 4. GitHub/GitLab Sensor Update

**File**: `/koi-sensors/sensors/github/github_sensor.py`

```python
def process_document(file_path, content, repo_info):
    # For markdown docs, try to extract date from content
    published_at, confidence = extract_publication_date(content, 'markdown')
    
    # Fall back to last commit date
    if not published_at:
        commit_date = get_last_commit_date(file_path)
        published_at = commit_date
        confidence = 0.7  # Commit date is approximate
    
    event = {
        "rid": f"github.doc.{repo_name}.{file_path}",
        "content": content,
        "metadata": {
            "published_at": published_at.isoformat() if published_at else None,
            "published_confidence": confidence,
            "repo": repo_name,
            "file_path": file_path,
            "commit_sha": commit_sha,
            # ... other metadata
        }
    }
    return event
```

### 5. Podcast Sensor Update

**File**: `/koi-sensors/sensors/podcast/podcast_sensor.py`

```python
def process_podcast_episode(episode_data):
    # RSS feeds have pubDate
    pub_date = episode_data.get('pubDate') or episode_data.get('published')
    
    event = {
        "rid": f"podcast.episode.{episode_id}",
        "content": {
            "title": episode_data['title'],
            "description": episode_data['description'],
            "transcript": episode_data.get('transcript')
        },
        "metadata": {
            "published_at": parse_rss_date(pub_date),
            "published_confidence": 0.95,  # RSS dates are reliable
            "duration": episode_data.get('duration'),
            "episode_number": episode_data.get('episode'),
            # ... other metadata
        }
    }
    return event
```

### 6. Notion Sensor Update

**File**: `/koi-sensors/sensors/notion/notion_sensor.py`

```python
def process_notion_page(page_data):
    # Notion API provides timestamps
    created_time = page_data.get('created_time')
    last_edited_time = page_data.get('last_edited_time')
    
    # Use created_time as publication date
    event = {
        "rid": f"notion.page.{page_id}",
        "content": extracted_content,
        "metadata": {
            "published_at": created_time,
            "published_confidence": 0.85,  # Created time is good proxy
            "last_modified": last_edited_time,
            "database": page_data.get('parent', {}).get('database_id'),
            "properties": page_data.get('properties', {}),
            # ... other metadata
        }
    }
    return event
```

## Testing Sensor Updates

After updating a sensor, test that publication dates are being extracted:

```python
# Test script for sensor
import asyncio
from sensors.twitter.twitter_sensor import TwitterSensor

async def test_sensor():
    sensor = TwitterSensor()
    
    # Process some test content
    event = await sensor.process_content(test_data)
    
    # Verify metadata includes publication info
    assert 'published_at' in event['metadata']
    assert 'published_confidence' in event['metadata']
    assert event['metadata']['published_confidence'] > 0
    
    print(f"Publication date: {event['metadata']['published_at']}")
    print(f"Confidence: {event['metadata']['published_confidence']}")

asyncio.run(test_sensor())
```

## Priority Order for Sensor Updates

1. **High Priority** (do first):
   - `twitter` - Easy, already has dates
   - `discourse` - Easy, API provides dates
   - `medium` - Already implemented
   
2. **Medium Priority**:
   - `websites` - Needs HTML parsing
   - `podcast` - RSS parsing
   - `notion` - API timestamps

3. **Low Priority**:
   - `github/gitlab` - Complex, multiple strategies needed
   - `ledger` - Blockchain timestamps inherent
   - `youtube` - Similar to podcast/RSS

## Integration with Event Bridge

The updated `koi_event_bridge_v2.py` will automatically:
1. Extract `published_at` from metadata
2. Store in database with confidence score
3. Calculate content hash for deduplication
4. Make content available for Daily Curator queries

## Backward Compatibility

Sensors that don't provide publication dates will still work:
- `published_at` will be NULL in database
- Daily Curator will use `created_at` (ingestion time) as fallback
- Lower priority in content selection

## Next Steps

1. Update each sensor following the examples above
2. Test date extraction for each source type
3. Run migration to add database fields
4. Test Daily Curator with real data