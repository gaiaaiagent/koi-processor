#!/usr/bin/env python3
"""
GitHub Webhook Handler for Code Graph Extraction

Receives push events from GitHub and triggers extraction for the changed repo.

Setup:
1. Run this server: python api/github_webhook.py
2. Configure GitHub webhooks for each repo:
   - URL: https://your-server/webhook/github
   - Content type: application/json
   - Events: Just the push event
   - Secret: Set GITHUB_WEBHOOK_SECRET env var
"""

import os
import sys
import hmac
import hashlib
import asyncio
import subprocess
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from loguru import logger

app = FastAPI(title="GitHub Webhook Handler")

# Repo name -> local path mapping
REPO_PATHS = {
    "regen-ledger": "/opt/projects/regen-repos/regen-ledger",
    "regen-web": "/opt/projects/regen-repos/regen-web",
    "regen-data-standards": "/opt/projects/regen-repos/regen-data-standards",
    "koi-processor": "/opt/projects/koi-processor",
    "koi-sensors": "/opt/projects/koi-sensors",
    "koi-research": "/opt/projects/koi-research",
    "regen-koi-mcp": "/opt/projects/regen-koi-mcp",
}

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
EXTRACTION_SCRIPT = "/opt/projects/koi-processor/scripts/load_to_staging.py"
LOG_DIR = "/opt/projects/koi-processor/logs"

# Track running extractions to avoid duplicates
running_extractions = set()


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature."""
    if not WEBHOOK_SECRET:
        logger.warning("No webhook secret configured - skipping verification")
        return True
    
    if not signature:
        return False
    
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected, signature)


async def run_extraction(repo_name: str, repo_path: str, trigger: str):
    """Run extraction for a repo in the background."""
    if repo_name in running_extractions:
        logger.info(f"Extraction already running for {repo_name}, skipping")
        return
    
    running_extractions.add(repo_name)
    log_file = f"{LOG_DIR}/webhook_{repo_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    try:
        logger.info(f"Starting extraction for {repo_name} (triggered by {trigger})")
        
        # First, git pull to get latest changes
        pull_result = subprocess.run(
            ["git", "pull"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60
        )
        logger.info(f"Git pull for {repo_name}: {pull_result.stdout.strip()}")
        
        # Run extraction
        cmd = [
            sys.executable, EXTRACTION_SCRIPT,
            "--repo", repo_name,
            "--path", repo_path
        ]
        
        with open(log_file, "w") as f:
            result = subprocess.run(
                cmd,
                cwd="/opt/projects/koi-processor",
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=300,  # 5 min timeout
                env={**os.environ, "VIRTUAL_ENV": "/opt/projects/koi-processor/.venv"}
            )
        
        if result.returncode == 0:
            logger.info(f"Extraction completed for {repo_name}")
        else:
            logger.error(f"Extraction failed for {repo_name}, see {log_file}")
            
    except subprocess.TimeoutExpired:
        logger.error(f"Extraction timed out for {repo_name}")
    except Exception as e:
        logger.error(f"Extraction error for {repo_name}: {e}")
    finally:
        running_extractions.discard(repo_name)


@app.get("/health")
async def health():
    return {"status": "healthy", "running_extractions": list(running_extractions)}


@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub push webhook."""
    payload = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_type = request.headers.get("X-GitHub-Event", "")
    
    # Verify signature
    if not verify_signature(payload, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Only handle push events
    if event_type != "push":
        return {"status": "ignored", "reason": f"Event type {event_type} not handled"}
    
    data = await request.json()
    repo_name = data.get("repository", {}).get("name", "")
    ref = data.get("ref", "")
    pusher = data.get("pusher", {}).get("name", "unknown")
    
    # Only process pushes to main/master branch
    if ref not in ("refs/heads/main", "refs/heads/master", "refs/heads/regen-prod"):
        return {"status": "ignored", "reason": f"Branch {ref} not tracked"}
    
    # Check if we know this repo
    repo_path = REPO_PATHS.get(repo_name)
    if not repo_path:
        return {"status": "ignored", "reason": f"Unknown repo {repo_name}"}
    
    # Schedule extraction in background
    background_tasks.add_task(run_extraction, repo_name, repo_path, f"push by {pusher}")
    
    return {
        "status": "extraction_scheduled",
        "repo": repo_name,
        "ref": ref,
        "pusher": pusher
    }


@app.post("/webhook/extract/{repo_name}")
async def manual_extract(repo_name: str, background_tasks: BackgroundTasks):
    """Manually trigger extraction for a repo."""
    repo_path = REPO_PATHS.get(repo_name)
    if not repo_path:
        raise HTTPException(status_code=404, detail=f"Unknown repo: {repo_name}")
    
    background_tasks.add_task(run_extraction, repo_name, repo_path, "manual trigger")
    
    return {"status": "extraction_scheduled", "repo": repo_name}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8360)
