# [ARCHIVED] NotebookLM Audio Generation Guide

Note: This document is archived for reference.

## Overview

This guide documents the manual process for generating podcast audio using Google's NotebookLM when automated solutions (like Podcastfy) are unavailable. NotebookLM's Audio Overview feature creates natural, conversational podcasts from written content.

## Prerequisites

- Google account with access to NotebookLM (https://notebooklm.google.com)
- Weekly digest exported to NotebookLM format (from Session 8/9)
- Approximately 30 minutes for the complete process

## Step-by-Step Process

### 1. Prepare Content Sources

**Automated Export:**
```bash
# Generate and export weekly digest to NotebookLM format
python scripts/export_to_notebooklm.py

# Output will be in: output/notebooklm/YYYY-MM-DD/
```

The export creates:
- `brief.md` - Main weekly brief (800-1200 words)
- `stories_1.md` through `stories_N.md` - Individual story files
- `statistics.md` - Network statistics and metrics
- `citations.md` - All sources and references
- `manifest.json` - Metadata about the export

### 2. Create or Open NotebookLM Notebook

1. Go to https://notebooklm.google.com
2. Click **"+ New Notebook"** or open existing **"Regen Weekly"** notebook
3. Name it: `Regen Weekly - [Date]` (e.g., "Regen Weekly - December 13, 2024")

### 3. Upload Source Documents

1. Click **"+ Add Source"** button
2. Select **"Upload from computer"**
3. Navigate to your export directory: `output/notebooklm/YYYY-MM-DD/`
4. Upload files in this order:
   - `brief.md` (primary content)
   - `stories_*.md` (top stories)
   - `statistics.md` (data points)
   - `citations.md` (sources)

**Important:** NotebookLM has a limit of 50 sources per notebook. If you have more files, prioritize:
- Brief (most important)
- Top 5-10 stories
- Statistics
- Key citations

### 4. Configure Audio Generation

1. Once sources are uploaded, click **"Generate"** or **"Audio Overview"** button
2. NotebookLM will show **"Generating audio overview..."**
3. Generation typically takes **3-5 minutes** for 20 minutes of audio

**Audio Settings (if available):**
- Style: **Conversational** (default)
- Length: Target **20 minutes**
- Hosts: **Two hosts** (default)
- Tone: **Professional but accessible**

### 5. Review Generated Audio

1. Once generated, click **Play** to preview
2. Check for:
   - Duration (should be 16-24 minutes)
   - Content accuracy
   - Natural conversation flow
   - Proper pronunciation of "Regen Network" terms

**Common Issues:**
- If too short (<16 min): Add more source content
- If too long (>24 min): Remove less important stories
- If pronunciation issues: Can't fix directly, note for future

### 6. Download Audio File

1. Click the **three dots menu** (⋮) next to the audio player
2. Select **"Download audio"**
3. File will download as: `Audio overview.m4a` or similar
4. Rename to: `regen_weekly_YYYY-MM-DD.mp3`

### 7. Place in Watch Directory

The audio pipeline watches for uploaded files:

```bash
# Default watch directory
output/notebooklm_uploads/

# Place your downloaded file here
mv ~/Downloads/"Audio overview.m4a" output/notebooklm_uploads/regen_weekly_2024-12-13.mp3
```

### 8. Automatic Processing

Once the file is in the watch directory, the pipeline automatically:

1. **Validates duration** (must be 16-24 minutes)
2. **Adds metadata** (title, episode number, description)
3. **Creates versions** (high/medium/low quality)
4. **Moves to podcast directory**

Run the watcher:
```bash
# Start watching for audio file
python audio_pipeline_enhanced.py --action watch --wait 30

# Or process existing file directly
python audio_pipeline_enhanced.py --action process --audio path/to/audio.mp3 --digest output/weekly/digest.json
```

## Validation and Quality Control

### Duration Requirements

- **Target**: 20 minutes
- **Minimum**: 16 minutes (80% of target)
- **Maximum**: 24 minutes (120% of target)

Validate manually downloaded audio:
```bash
python audio_pipeline_enhanced.py --action validate --audio regen_weekly_2024-12-13.mp3
```

### Content Quality Checklist

- [ ] Opens with week overview
- [ ] Covers top 3-5 stories
- [ ] Includes network statistics
- [ ] Mentions governance updates (if any)
- [ ] Has clear call-to-action
- [ ] Natural conversation flow
- [ ] No significant errors or hallucinations

## Troubleshooting

### NotebookLM Issues

**"Audio generation failed"**
- Reduce number of sources (max 50)
- Ensure sources are text files (.md, .txt)
- Check file sizes (very large files may fail)
- Try regenerating

**"Audio too short/long"**
- Adjust source content amount
- Brief should be 800-1200 words for ~20 min
- Each story adds ~2-3 minutes

**"Can't access NotebookLM"**
- Requires Google account
- May have regional restrictions
- Try different browser or clear cache

### Pipeline Issues

**"Audio validation failed"**
```bash
# Check duration manually
ffprobe -v error -show_entries format=duration audio_file.mp3

# Should output between 960-1440 seconds (16-24 minutes)
```

**"Metadata not added"**
```bash
# Install required library
pip install mutagen

# Retry processing
python audio_pipeline_enhanced.py --action process --audio audio_file.mp3
```

**"Versions not created"**
```bash
# Check ffmpeg installation
which ffmpeg

# Install if missing
brew install ffmpeg  # macOS
sudo apt install ffmpeg  # Ubuntu/Debian
```

## Alternative: Podcastfy (Automated)

If Podcastfy is available, use automated generation:

```bash
# Fully automated pipeline
python audio_pipeline.py --backend podcastfy

# Generates audio without manual steps
```

## Best Practices

### Content Optimization for Audio

1. **Brief Structure**:
   - Strong opening hook
   - Clear section breaks
   - Conversational language
   - Avoid too many acronyms

2. **Story Selection**:
   - Prioritize engaging narratives
   - Include variety (technical, community, governance)
   - Add human interest elements

3. **Statistics Presentation**:
   - Round numbers for speech (1.2M not 1,234,567)
   - Provide context ("15% increase")
   - Compare to previous periods

### File Organization

```
output/
├── notebooklm/           # Source exports
│   └── 2024-12-13/       # Date-based folders
├── notebooklm_uploads/   # Watch directory
├── podcasts/             # Final audio files
└── versions/             # Quality versions
```

### Archival

Keep for each episode:
- Original NotebookLM export
- Downloaded audio file
- Processing logs
- Validation results

## Integration with Podcast Feed

Once audio is validated and processed:

1. File is moved to: `output/podcasts/regen_weekly_YYYY-MM-DD.mp3`
2. Metadata is embedded in ID3 tags
3. Ready for RSS feed generation (Session 14)
4. Can be uploaded to podcast hosts

## Monitoring and Metrics

### Track Success Metrics

- Generation time (target: <5 minutes)
- Duration accuracy (target: 20±2 minutes)
- Manual intervention required
- Listener feedback

### Generate Report

```bash
# Check storage and versions
python audio_pipeline_enhanced.py --action report
```

## Quick Reference Commands

```bash
# Complete workflow
# 1. Generate digest and export
python scripts/weekly_digest_pipeline.py

# 2. Start watcher
python audio_pipeline_enhanced.py --action watch

# 3. Generate in NotebookLM (manual)
# ... follow steps above ...

# 4. Validate uploaded audio
python audio_pipeline_enhanced.py --action validate --audio output/notebooklm_uploads/*.mp3

# 5. Process for podcast
python audio_pipeline_enhanced.py --action process --audio output/podcasts/regen_weekly_*.mp3
```

## Support

For issues:
1. Check validation output for specific errors
2. Review logs in `logs/audio_pipeline.log`
3. Ensure all dependencies installed
4. Verify NotebookLM access and quotas
