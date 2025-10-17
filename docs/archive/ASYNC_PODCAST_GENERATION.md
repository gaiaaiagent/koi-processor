# [ARCHIVED] Async Podcast Generation System

Note: This document is archived. The active KOI processor docs focus on the hybrid graph/NL→SPARQL system.

## Overview

The podcast generation system has been upgraded to use an **async job queue pattern** to prevent network timeout errors during long-running podcast generation tasks.

## Architecture

### Before (Synchronous)
```
User clicks "Generate Podcast"
    ↓
Frontend waits for response (180s timeout)
    ↓
Backend runs podcast generation
    ↓
Response sent back (or timeout error)
```

**Problem:** Network changes during the 2-5 minute generation would cause `ERR_NETWORK_CHANGED` errors.

### After (Async with Polling)
```
User clicks "Generate Podcast"
    ↓
Frontend submits job → receives job_id immediately
    ↓
Frontend polls /podcast/status/<job_id> every 2 seconds
    ↓
Backend processes job in background thread
    ↓
Frontend shows real-time progress updates
    ↓
Job completes → Frontend refreshes draft list
```

**Benefits:**
- ✅ No long HTTP connections vulnerable to network changes
- ✅ Real-time progress updates (10%, 30%, 50%, etc.)
- ✅ User can navigate away and check back later
- ✅ More robust error handling

## Components

### 1. Backend Job Queue (`src/content/podcast_job_queue.py`)

**PodcastJobQueue** - In-memory job queue that:
- Accepts job submissions
- Runs podcast generation in background threads
- Tracks job status, progress, and results
- Updates database when complete

**Job States:**
- `pending` - Job queued
- `processing` - Currently generating
- `completed` - Successfully finished
- `failed` - Error occurred

### 2. Backend API Endpoints (`src/content/content_dashboard.py`)

**POST `/api/dashboard/drafts/<draft_id>/generate_podcast`**
- Submits job to queue
- Returns immediately with `job_id`

**GET `/api/dashboard/podcast/status/<job_id>`**
- Returns current job status
- Includes progress percentage and message

### 3. Frontend Polling (`static/dashboard.v5.js`)

**generatePodcast(draftId)** - Main function:
1. Submits job via POST
2. Receives job_id
3. Calls pollPodcastJobStatus()

**pollPodcastJobStatus(jobId, ...)** - Polling loop:
- Polls every 2 seconds
- Updates button with progress
- Handles completion/failure
- 10-minute timeout safety

## Usage

### For Users

Click "Generate Podcast" button:
- Button shows "Starting..."
- Changes to "10% - Generating podcast script..."
- Updates to "50% - Generating audio (this may take a few minutes)..."
- Finally "100% - Podcast generated successfully!"

You can:
- See real-time progress
- Navigate to other tabs (polling continues)
- Close browser and check back later

### For Developers

**Testing the job queue:**
```python
from src.content.podcast_job_queue import get_job_queue

# Get queue instance
queue = get_job_queue(DB_CONFIG)

# Submit job
job_id = queue.submit_job(draft_id, content_data)

# Check status
status = queue.get_job_status(job_id)
print(status['progress'])  # 0-100
print(status['message'])   # Current step
```

**Testing via API:**
```bash
# Submit job
curl -X POST http://localhost:8400/digests/api/dashboard/drafts/abc123/generate_podcast

# Check status
curl http://localhost:8400/digests/api/dashboard/podcast/status/podcast_abc123_1234567890
```

## Configuration

**Polling interval:** 2 seconds (adjustable in `dashboard.v5.js:1190`)
**Max polling duration:** 10 minutes / 300 attempts (adjustable in `dashboard.v5.js:1191`)
**Job timeout:** 5 minutes subprocess timeout (adjustable in `podcast_job_queue.py:137`)

## Error Handling

### Network Errors
- Frontend: Short 2-second requests → less vulnerable to network changes
- Backend: Job continues running even if client disconnects

### Job Failures
- Captured in job status: `status: 'failed', error: '...'`
- User sees: "❌ Podcast generation failed: [error message]"

### Timeouts
- Backend: 5-minute subprocess timeout
- Frontend: 10-minute polling timeout
- User sees: "⏱️ Podcast generation is taking longer than expected"

## Migration Notes

The old synchronous endpoint is **replaced** (not deprecated) to ensure all requests use the new system.

**Breaking change:**
- Old response: `{success: true, audio_file: '...', file_size: '...'}`
- New response: `{success: true, job_id: 'podcast_...'}`

Frontend updated to handle new flow automatically.

## Future Improvements

1. **Persistent job queue** - Use Redis/PostgreSQL instead of in-memory
2. **WebSocket updates** - Push updates instead of polling
3. **Job history** - Store completed jobs for audit
4. **Cancellation** - Allow users to cancel running jobs
5. **Queue limits** - Prevent too many concurrent jobs

## Troubleshooting

**Job stuck in "processing":**
- Check backend logs: `tail -f /opt/projects/koi-processor/logs/dashboard.log`
- Check for subprocess errors
- Restart Flask server to clear in-memory queue

**Frontend not updating:**
- Check browser console for polling errors
- Verify `/podcast/status/<job_id>` endpoint responds
- Check for CORS issues

**Database not updated:**
- Verify PostgreSQL connection in job queue
- Check `quality_reviews` table for updated metadata
- Look for database errors in backend logs

**"OpenAI not installed" error:**
- Ensure `openai>=1.0.0` is in `requirements.txt` (already included)
- Install dependencies: `source venv/bin/activate && pip install -r requirements.txt`
- The job queue uses venv Python automatically (`podcast_job_queue.py:104-106`)
- Restart dashboard after installing: `./stop_dashboard.sh && ./start_dashboard.sh`
