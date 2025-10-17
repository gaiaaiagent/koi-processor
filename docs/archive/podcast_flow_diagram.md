# [ARCHIVED] Podcast Generation Flow

Note: This document is archived for reference.

## New Async Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                          │
│                                                                 │
│  [Generate Podcast Button] ──────────────────────┐             │
│         │                                         │             │
│         │ onClick                                 │             │
│         ▼                                         │             │
│  generatePodcast(draftId)                        │             │
│         │                                         │             │
│         │ POST /generate_podcast                  │             │
│         ▼                                         │             │
└─────────┼─────────────────────────────────────────┼─────────────┘
          │                                         │
          │                                         │
┌─────────▼─────────────────────────────────────────▼─────────────┐
│                      BACKEND (Flask)                            │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐    │
│  │  POST /generate_podcast                                │    │
│  │  ────────────────────────────────────────────────┐    │    │
│  │  1. Fetch draft from DB                          │    │    │
│  │  2. Submit to job queue ──────────┐             │    │    │
│  │  3. Return {job_id} immediately ◄─┼─────────┐   │    │    │
│  └─────────────────────────────────┼─┼─────────┼───┘    │    │
│                                    │ │         │         │    │
│  ┌─────────────────────────────────▼─┼─────────┼───────┐ │    │
│  │  PodcastJobQueue                  │         │       │ │    │
│  │                                    │         │       │ │    │
│  │  jobs[job_id] = {                 │         │       │ │    │
│  │    status: 'pending' ─────────────┼─────────┼──┐    │ │    │
│  │    progress: 0%                   │         │  │    │ │    │
│  │  }                                │         │  │    │ │    │
│  │                                   │         │  │    │ │    │
│  │  Background Thread ───────────────┼─────────┼──┘    │ │    │
│  │    ├─ 10%: Generate script        │         │       │ │    │
│  │    ├─ 30%: Save temp file         │         │       │ │    │
│  │    ├─ 50%: Run audio gen (3-5min) │         │       │ │    │
│  │    ├─ 80%: Parse output           │         │       │ │    │
│  │    ├─ 90%: Generate markdown      │         │       │ │    │
│  │    ├─ 95%: Update database        │         │       │ │    │
│  │    └─100%: Status = 'completed'   │         │       │ │    │
│  └────────────────────────────────────┼─────────┼───────┘ │    │
│                                       │         │         │    │
│  ┌────────────────────────────────────▼─────────┼───────┐ │    │
│  │  GET /podcast/status/<job_id>      │         │       │ │    │
│  │  ────────────────────────────────  │         │       │ │    │
│  │  Return current job status ◄───────┼─────────┘       │ │    │
│  │  {status, progress, message}       │                 │ │    │
│  └────────────────────────────────────┼─────────────────┘ │    │
└─────────────────────────────────────────────────────────────────┘
          │                             │
          │ job_id response             │ polling every 2s
          ▼                             │
┌─────────────────────────────────────────▼───────────────────────┐
│                    USER INTERFACE (continued)                   │
│                                                                 │
│  Button updates:                                               │
│    "Starting..." ──────────────────────────┐                  │
│            │                                │                  │
│            ▼                                │                  │
│  pollPodcastJobStatus(job_id) ─────────────┘                  │
│            │                                                   │
│            │ GET /status/<job_id> (every 2s)                  │
│            │                                                   │
│            ├─ "10% - Generating script..."                    │
│            ├─ "30% - Saving content..."                       │
│            ├─ "50% - Generating audio..."                     │
│            ├─ "80% - Processing output..."                    │
│            └─ "100% ✅ Success!"                              │
│                     │                                          │
│                     ▼                                          │
│            Reload drafts list                                 │
│            Show audio player                                  │
│                                                                │
└─────────────────────────────────────────────────────────────────┘
```

## Key Benefits

1. **No Long HTTP Connections**
   - Initial request: <100ms (just submits job)
   - Status polls: <50ms each
   - Immune to network changes during generation

2. **Real-Time Feedback**
   - User sees progress: 10%, 30%, 50%, 80%, 95%, 100%
   - Clear status messages at each step

3. **Fault Tolerant**
   - Job continues even if browser closes
   - Can check back later with same job_id
   - Network interruptions don't kill the job

4. **Better UX**
   - No mysterious "generating..." black box
   - User knows what's happening at each step
   - Estimated time remaining visible

## Error Recovery

```
Network Error During Polling
         │
         ▼
Frontend: Retry poll after 2s
         │
         ├─ Success? Continue polling
         │
         └─ Still failing? Show error after 3 retries
                │
                ▼
         User can manually refresh page
                │
                ▼
         Status preserved in backend
```

## Code Locations

- **Job Queue**: `/opt/projects/koi-processor/src/content/podcast_job_queue.py`
- **Backend API**: `/opt/projects/koi-processor/src/content/content_dashboard.py:1106-1163`
- **Frontend**: `/opt/projects/koi-processor/static/dashboard.v5.js:1139-1257`
- **Documentation**: `/opt/projects/koi-processor/docs/ASYNC_PODCAST_GENERATION.md`
