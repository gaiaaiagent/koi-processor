#!/usr/bin/env python3
"""
GitHub Webhook Handler for Code Graph Updates

Receives push events from GitHub and triggers code extraction for changed repos.

Setup:
1. Start the server: uvicorn api.github_webhook:app --host 0.0.0.0 --port 8360
2. Configure webhooks in GitHub repo settings:
   - Payload URL: http://your-server:8360/webhook/github
   - Content type: application/json
   - Secret: Set GITHUB_WEBHOOK_SECRET env var
   - Events: Just the push event
"""

import os
import sys
import hmac
import hashlib
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from loguru import logger

# Configuration
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
# Default to /opt/projects for production, override with CODE_GRAPH_BASE_PATH for local dev
BASE_PATH = Path(os.environ.get("CODE_GRAPH_BASE_PATH", "/opt/projects"))

# Map GitHub repo names to local paths
# Production uses /opt/projects with regen-repos/ subdirectory for some repos
REGEN_REPOS_PATH = Path(os.environ.get("REGEN_REPOS_PATH", str(BASE_PATH / "regen-repos")))
REPO_PATHS = {
    # Regen repos (may be in subdirectory on production)
    "regen-ledger": REGEN_REPOS_PATH / "regen-ledger",
    "regen-web": REGEN_REPOS_PATH / "regen-web",
    "regen-data-standards": REGEN_REPOS_PATH / "regen-data-standards",
    # KOI repos (directly under base path)
    "koi-processor": BASE_PATH / "koi-processor",
    "koi-sensors": BASE_PATH / "koi-sensors",
    "koi-research": BASE_PATH / "koi-research",
    "regen-koi-mcp": BASE_PATH / "regen-koi-mcp",
}

# Track running extractions
running_extractions: dict[str, datetime] = {}

app = FastAPI(
    title="Code Graph Webhook",
    description="GitHub webhook handler for automatic code graph updates"
)


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature."""
    if not WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set, skipping signature verification")
        return True

    if not signature or not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


async def run_extraction(repo_name: str, repo_path: Path):
    """Run extraction for a single repo."""
    try:
        running_extractions[repo_name] = datetime.now()
        logger.info(f"Starting extraction for {repo_name}")

        # Pull latest changes
        pull_result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60
        )

        if pull_result.returncode != 0:
            logger.warning(f"Git pull for {repo_name} returned non-zero: {pull_result.stderr}")
        else:
            logger.info(f"Git pull for {repo_name}: {pull_result.stdout.strip()}")

        # Run extraction
        koi_processor = BASE_PATH / "koi-processor"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(koi_processor)

        extract_result = subprocess.run(
            [
                sys.executable,
                str(koi_processor / "scripts" / "load_to_staging.py"),
                "--repo", repo_name,
                "--path", str(repo_path)
            ],
            cwd=koi_processor,
            capture_output=True,
            text=True,
            env=env,
            timeout=600  # 10 minute timeout
        )

        if extract_result.returncode != 0:
            logger.error(f"Extraction failed for {repo_name}: {extract_result.stderr}")
        else:
            logger.info(f"Extraction completed for {repo_name}")
            # Log summary from output
            for line in extract_result.stdout.split("\n"):
                if "entities" in line.lower() or "edges" in line.lower():
                    logger.info(f"  {line.strip()}")

    except subprocess.TimeoutExpired:
        logger.error(f"Extraction timed out for {repo_name}")
    except Exception as e:
        logger.error(f"Extraction error for {repo_name}: {e}")
    finally:
        running_extractions.pop(repo_name, None)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "code-graph-webhook",
        "status": "healthy",
        "configured_repos": list(REPO_PATHS.keys()),
        "running_extractions": list(running_extractions.keys())
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub webhook push events."""
    # Get raw body for signature verification
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # Get event type
    event_type = request.headers.get("X-GitHub-Event", "")

    # Only handle push events
    if event_type != "push":
        return {"status": "ignored", "reason": f"Event type '{event_type}' not handled"}

    # Get repo name from payload
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    repo_name = payload.get("repository", {}).get("name", "")
    ref = payload.get("ref", "")

    logger.info(f"Received push event for {repo_full_name} on {ref}")

    # Only process pushes to main/master branch
    if ref not in ("refs/heads/main", "refs/heads/master"):
        return {
            "status": "ignored",
            "reason": f"Branch '{ref}' not tracked (only main/master)"
        }

    # Check if repo is configured
    if repo_name not in REPO_PATHS:
        return {
            "status": "ignored",
            "reason": f"Repository '{repo_name}' not configured"
        }

    # Check if extraction is already running
    if repo_name in running_extractions:
        return {
            "status": "skipped",
            "reason": f"Extraction already running for {repo_name}",
            "started_at": running_extractions[repo_name].isoformat()
        }

    # Get commit info
    commits = payload.get("commits", [])
    commit_count = len(commits)
    head_commit = payload.get("head_commit", {})
    commit_msg = head_commit.get("message", "")[:100]
    pusher = payload.get("pusher", {}).get("name", "unknown")

    logger.info(f"Processing {commit_count} commit(s) by {pusher}: {commit_msg}")

    # Schedule extraction in background
    repo_path = REPO_PATHS[repo_name]
    background_tasks.add_task(run_extraction, repo_name, repo_path)

    return {
        "status": "accepted",
        "repo": repo_name,
        "commits": commit_count,
        "message": f"Extraction scheduled for {repo_name}"
    }


@app.post("/webhook/extract/{repo_name}")
async def manual_extract(repo_name: str, background_tasks: BackgroundTasks):
    """Manually trigger extraction for a repo."""
    if repo_name not in REPO_PATHS:
        raise HTTPException(
            status_code=404,
            detail=f"Repository '{repo_name}' not configured. Available: {list(REPO_PATHS.keys())}"
        )

    if repo_name in running_extractions:
        return {
            "status": "skipped",
            "reason": f"Extraction already running for {repo_name}",
            "started_at": running_extractions[repo_name].isoformat()
        }

    repo_path = REPO_PATHS[repo_name]
    background_tasks.add_task(run_extraction, repo_name, repo_path)

    return {
        "status": "accepted",
        "repo": repo_name,
        "message": f"Manual extraction scheduled for {repo_name}"
    }


@app.post("/webhook/extract-all")
async def extract_all(background_tasks: BackgroundTasks):
    """Trigger extraction for all configured repos."""
    scheduled = []
    skipped = []

    for repo_name, repo_path in REPO_PATHS.items():
        if repo_name in running_extractions:
            skipped.append(repo_name)
        else:
            background_tasks.add_task(run_extraction, repo_name, repo_path)
            scheduled.append(repo_name)

    return {
        "status": "accepted",
        "scheduled": scheduled,
        "skipped": skipped,
        "message": f"Scheduled extraction for {len(scheduled)} repos"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8360)
