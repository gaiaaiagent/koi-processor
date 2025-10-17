# [ARCHIVED] Podcast Hosting Integration Guide

Note: This document is archived for reference.

## Overview

This guide documents the complete process for generating, publishing, and hosting the **Pathway to Planetary Regeneration** podcast, which provides weekly audio digests of Regen Network activities.

## Architecture

```
Weekly Aggregator → Audio Generation → Podcast Publisher → RSS Feed
       ↓                    ↓                 ↓              ↓
  Database Query      Podcastfy/NotebookLM   Episode Meta   Distribution
                      (Automated/Manual)
```

## Audio Generation Methods

### Primary: Podcastfy (Automated)
- Fully automated audio generation
- No manual steps required
- Configurable voices and conversation styles
- 20-minute conversational podcasts

### Fallback: NotebookLM (Manual)
- Export to NotebookLM format
- Manual audio generation via web interface
- High-quality conversational audio
- Requires manual download step

## Components

### 1. Weekly Aggregator (`weekly_aggregator.py`)
- Collects content from past 7 days
- Uses BGE embeddings for semantic clustering
- Generates 800-1200 word digest
- Identifies key themes and stories

### 2. Podcastfy Generator (`podcastfy_generator.py`)
- **Automated Audio Generation** using Podcastfy library
- **Conversation Script Creation** from digest content
- **Voice Configuration** with multiple TTS providers
- **No Manual Steps** required for audio creation

### 3. NotebookLM Exporter (`notebooklm_exporter.py`)
- Fallback method when Podcastfy unavailable
- Exports digest to NotebookLM-compatible format
- Creates structured markdown documents
- Requires manual audio generation step

### 4. Podcast Publisher (`podcast_publisher.py`)
- **RSS 2.0 Feed Generation** with iTunes extensions
- **Episode Management** with metadata tracking
- **Google Drive Integration** for backup storage
- **Audio Validation** for duration and quality

### 5. Integration Pipeline (`podcast_integration.py`)
- Orchestrates the complete workflow
- Automatic method selection (Podcastfy → NotebookLM fallback)
- Handles publishing automation
- Supports both automated and manual workflows

## Setup Instructions

### Prerequisites

```bash
# Install required Python packages
pip install -r requirements.txt

# For automated audio generation (recommended)
pip install podcastfy

# Optional: Google Drive API (for backup)
pip install google-api-python-client google-auth

# Optional: Audio processing
pip install mutagen  # For metadata tagging
```

### Configuration

1. **Create Configuration Files**

```bash
# Create config directory
mkdir -p config

# Podcast configuration will be auto-generated on first run
python podcast_publisher.py --test
```

2. **Edit `config/podcast_config.json`**

```json
{
  "podcast": {
    "title": "Pathway to Planetary Regeneration",
    "subtitle": "Weekly insights from the Regen Network ecosystem",
    "author": "Regen Network",
    "owner_email": "podcast@regen.network",
    "image_url": "https://regen.network/podcast/cover.jpg",
    "website": "https://regen.network/podcast"
  },
  "storage_path": "./podcast",
  "google_drive": {
    "enabled": false,
    "folder_id": "YOUR_DRIVE_FOLDER_ID",
    "credentials_path": "credentials.json"
  },
  "hosting": {
    "base_url": "https://regen.network/podcast/episodes/",
    "feed_url": "https://regen.network/podcast/feed.xml"
  }
}
```

### Google Drive Setup (Optional)

1. **Enable Google Drive API**
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project or select existing
   - Enable Google Drive API
   - Create credentials (Service Account recommended)

2. **Download Credentials**
   - Download the JSON key file
   - Save as `credentials.json` in the project directory

3. **Share Drive Folder**
   - Create a folder in Google Drive for podcast storage
   - Share it with the service account email
   - Copy the folder ID from the URL

## Weekly Podcast Generation Workflow

### Fully Automated Pipeline (with Podcastfy)

```bash
# Run the complete pipeline with automated audio
python podcast_integration.py

# With auto-publish (skips confirmation)
python podcast_integration.py --auto-publish

# Force specific audio method
python podcast_integration.py --audio-method podcastfy  # Automated
python podcast_integration.py --audio-method notebooklm  # Manual fallback

# Using existing audio file
python podcast_integration.py --audio-file path/to/audio.mp3
```

### Manual Steps

#### Step 1: Generate Weekly Digest
```bash
python weekly_aggregator.py
```
This creates a comprehensive digest from the past week's content.

#### Step 2: Export for NotebookLM
```bash
python notebooklm_exporter.py --input digest.json --output notebooklm_export.md
```

#### Step 3: Generate Audio with NotebookLM

1. **Access NotebookLM**
   - Go to [notebooklm.google.com](https://notebooklm.google.com)
   - Sign in with Google account

2. **Create or Open Notebook**
   - Create new notebook named "Regen Weekly"
   - Or open existing weekly podcast notebook

3. **Upload Content**
   - Click "Upload" or drag the markdown file
   - Wait for processing to complete

4. **Generate Audio Overview**
   - Click "Generate Audio Overview" button
   - Select conversation style (recommended: "Informative Discussion")
   - Wait 5-10 minutes for generation

5. **Download Audio**
   - Click download button when ready
   - Save to `notebooklm_audio/` directory
   - Filename format: `regen_weekly_YYYYMMDD.mp3`

#### Step 4: Publish Episode
```bash
# Publish with existing audio
python podcast_publisher.py --audio path/to/audio.mp3 --digest digest.json
```

### Verification

1. **Check RSS Feed**
```bash
# Validate RSS feed
python -c "from podcast_publisher import PodcastPublisher; p = PodcastPublisher(); print(p.validate_feed())"

# View feed
cat podcast/feed/feed.xml
```

2. **Test in Podcast App**
   - Add feed URL to podcast app
   - Verify episode appears correctly
   - Check audio playback

## Hosting Options

### Option 1: Self-Hosted (Recommended for Start)

1. **Web Server Setup**
```bash
# Copy files to web server
scp -r podcast/feed/feed.xml user@server:/var/www/podcast/
scp -r podcast/episodes/*.mp3 user@server:/var/www/podcast/episodes/
```

2. **Nginx Configuration**
```nginx
location /podcast/ {
    root /var/www;
    autoindex off;
    
    # Set correct content types
    location ~ \.xml$ {
        add_header Content-Type application/rss+xml;
    }
    location ~ \.mp3$ {
        add_header Content-Type audio/mpeg;
    }
}
```

### Option 2: Google Drive + GitHub Pages

1. **Audio on Google Drive**
   - Upload episodes to shared Drive folder
   - Get direct download links
   - Update RSS feed with Drive URLs

2. **RSS on GitHub Pages**
   - Create `gh-pages` branch
   - Commit `feed.xml` to root
   - Access at: `https://[username].github.io/[repo]/feed.xml`

### Option 3: Podcast Hosting Service

Popular options:
- **Anchor** (free, owned by Spotify)
- **Buzzsprout** (professional features)
- **Transistor** (multiple podcasts)
- **Podbean** (built-in monetization)

## Distribution

### Submit to Podcast Directories

1. **Apple Podcasts**
   - Use [Podcasts Connect](https://podcastsconnect.apple.com)
   - Submit RSS feed URL
   - Wait for approval (24-48 hours)

2. **Spotify**
   - Use [Spotify for Podcasters](https://podcasters.spotify.com)
   - Claim your podcast
   - Add RSS feed

3. **Google Podcasts**
   - Submit via [Google Podcasts Manager](https://podcastsmanager.google.com)
   - Verify ownership
   - Monitor analytics

4. **Other Platforms**
   - Stitcher
   - TuneIn
   - iHeartRadio
   - Amazon Music

## Monitoring and Analytics

### Feed Validation
```bash
# Use podcast validator services
curl -X POST https://podba.se/validate -d "url=https://regen.network/podcast/feed.xml"
```

### Analytics Integration
- Add tracking prefix to audio URLs
- Use hosting service analytics
- Monitor download statistics

## Troubleshooting

### Common Issues

1. **Audio Duration Incorrect**
   - Install `ffprobe`: `brew install ffmpeg`
   - Or install `mutagen`: `pip install mutagen`

2. **RSS Feed Invalid**
   - Check XML syntax
   - Verify all required fields
   - Test with validator

3. **Audio File Too Large**
   - Compress audio: `ffmpeg -i input.mp3 -b:a 96k output.mp3`
   - Consider splitting into parts

4. **Google Drive Rate Limits**
   - Use public folder
   - Enable link sharing
   - Consider CDN for popular episodes

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
python podcast_integration.py
```

## Best Practices

1. **Consistent Schedule**
   - Publish same day each week
   - Maintain regular episode length
   - Update feed immediately after publishing

2. **Audio Quality**
   - Target 20-25 minutes duration
   - Use 96-128 kbps bitrate
   - Normalize audio levels

3. **Metadata**
   - Include relevant keywords
   - Write compelling descriptions
   - Add episode numbers

4. **Backup Strategy**
   - Keep local copies of all episodes
   - Backup RSS feed regularly
   - Store metadata separately

## API Reference

### PodcastPublisher

```python
from podcast_publisher import PodcastPublisher

# Initialize
publisher = PodcastPublisher(config_path="config/podcast_config.json")

# Add episode
episode = publisher.add_episode(
    audio_file="path/to/audio.mp3",
    title="Episode Title",
    description="Full description",
    keywords=["regen", "climate", "blockchain"]
)

# Generate RSS
feed_xml = publisher.generate_rss_feed()

# Save feed
publisher.save_rss_feed("feed.xml")

# Validate
is_valid = publisher.validate_feed()
```

### Integration Pipeline

```python
from podcast_integration import PodcastIntegration

# Initialize
integration = PodcastIntegration()

# Run full pipeline
results = integration.run_full_pipeline(auto_publish=False)

# Or run individual steps
digest = integration.generate_weekly_digest()
export_file = integration.export_for_notebooklm(digest)
audio_file = integration.wait_for_audio(timeout=3600)
episode = integration.publish_podcast(audio_file, digest)
```

## Future Enhancements

1. **Automated Audio Generation**
   - Integrate text-to-speech APIs
   - Use ElevenLabs or similar services
   - Generate multi-voice conversations

2. **Enhanced Analytics**
   - Track listener engagement
   - Monitor completion rates
   - A/B test episode formats

3. **Interactive Features**
   - Chapter markers
   - Show notes with links
   - Transcript generation

4. **Monetization**
   - Sponsorship integration
   - Premium subscriber feed
   - Donation integration

## Support

For issues or questions:
- Check logs in `./logs/` directory
- Review configuration files
- Test individual components separately
- Contact: podcast@regen.network
