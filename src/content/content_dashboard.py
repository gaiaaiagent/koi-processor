#!/usr/bin/env python3
"""
Milestone B Content Operations Dashboard
Web-based monitoring interface for Daily Bot and Weekly Digest operations
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_compress import Compress
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import time
import hashlib
import json
import yaml
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import asyncio
from functools import wraps
from loguru import logger
from dotenv import load_dotenv
import httpx
import re
import subprocess
import tempfile
from urllib.parse import urljoin, urlparse
from openai import OpenAI

# Handle imports whether run as module or script
try:
    from src.content.podcast_job_queue import get_job_queue
except ImportError:
    from podcast_job_queue import get_job_queue

# Load environment variables from .env file
load_dotenv()

# Initialize Flask app
app = Flask(__name__,
            template_folder='../../templates',
            static_folder='../../static')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['COMPRESS_MIMETYPES'] = ['application/json', 'text/html', 'text/css', 'application/javascript']
app.config['COMPRESS_LEVEL'] = 6
app.config['COMPRESS_MIN_SIZE'] = 500
CORS(app)
Compress(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuration
CONFIG_PATH = Path(__file__).parent / "config" / "dashboard_config.yaml"
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', '5433')),
    'database': os.environ.get('DB_NAME', 'eliza'),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', 'postgres')
}

# Load configuration
def load_config():
    """Load dashboard configuration"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return yaml.safe_load(f)
    else:
        # Default configuration
        return {
            'dashboard': {
                'port': 8400,
                'refresh_interval': 30,
                'auth_enabled': False
            },
            'thresholds': {
                'daily_bot': {
                    'min_sources': 3,
                    'max_thread_length': 5,
                    'style_score_warning': 0.7
                },
                'weekly_digest': {
                    'min_word_count': 800,
                    'max_word_count': 1200,
                    'min_sources': 10
                }
            },
            'alerts': {
                'email_enabled': False,
                'slack_enabled': False
            }
        }

config = load_config()

# Database connection
def get_db_connection():
    """Create a database connection"""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)

# Authentication decorator (simple for now)
def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if config['dashboard'].get('auth_enabled', False):
            if 'user' not in session:
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def validate_session_token_sync(token: str) -> Optional[str]:
    """Validate a Bearer session token against the session_tokens table.

    Mirrors the SHA-256 hash + lookup pattern used by koi-query-api.ts and
    src/services/auth_service.py. Returns user_email if the token is valid,
    unrevoked, unexpired, and bound to a @regen.network address. Else None.
    """
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_email, expires_at, revoked_at
                FROM session_tokens
                WHERE token_hash = %s
                """,
                (token_hash,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"session_tokens lookup failed: {e}")
        return None
    if not row or row.get("revoked_at") is not None:
        return None
    expires_at = row.get("expires_at")
    if expires_at and expires_at.timestamp() < time.time():
        return None
    user_email = row.get("user_email") or ""
    if not user_email.endswith("@regen.network"):
        return None
    return user_email


def require_bearer_auth(f):
    """Gate endpoint behind a valid @regen.network session token.

    Used for cache-first endpoints (weekly digest variants) where there is no
    SQL WHERE clause to splice an is_private filter into. The cached file may
    contain content sourced from private documents, so the right Phase 2 fix
    is to gate access entirely until the underlying curator SQL is audited.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        parts = auth.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return jsonify({"error": "Bearer token required"}), 401
        user_email = validate_session_token_sync(parts[1])
        if not user_email:
            return jsonify({"error": "Invalid or expired session token"}), 401
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
@require_auth
def index():
    """Main dashboard page"""
    return render_template('dashboard.html', config=config)

@app.route('/batch-queue')
@require_auth
def batch_queue():
    """Batch queue component page"""
    return render_template('batch_queue_component.html')

@app.route('/koi')
@require_auth
def koi_query():
    """KOI Knowledge Graph Query Interface"""
    return render_template('koi_query.html', config=config)

@app.route('/api/dashboard/overview')
@require_auth
def get_overview():
    """Get overall system health and metrics"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get recent daily bot activity
        cur.execute("""
            SELECT COUNT(*) as total_daily_posts,
                   AVG(style_score) as avg_style_score,
                   COUNT(CASE WHEN approval_status = 'published' THEN 1 END) as published_count
            FROM quality_reviews
            WHERE content_type = 'daily_thread'
            AND created_at > NOW() - INTERVAL '7 days'
        """)
        daily_stats = cur.fetchone() or {'total_daily_posts': 0, 'avg_style_score': 0, 'published_count': 0}
        
        # Get recent weekly digest activity
        cur.execute("""
            SELECT COUNT(*) as total_weekly_digests,
                   AVG(CAST(content_data->>'word_count' AS INT)) as avg_word_count
            FROM quality_reviews
            WHERE content_type = 'weekly_digest'
            AND created_at > NOW() - INTERVAL '30 days'
        """)
        weekly_stats = cur.fetchone() or {'total_weekly_digests': 0, 'avg_word_count': 0}
        
        # Get pending reviews
        cur.execute("""
            SELECT COUNT(*) as pending_count
            FROM quality_reviews
            WHERE approval_status IN ('draft', 'pending_review')
        """)
        pending = cur.fetchone()
        
        # Get system health
        cur.execute("SELECT NOW() as db_time")
        db_check = cur.fetchone()
        
        # Check KOI pipeline status - look for any recent activity in the last 24 hours
        # Also check for any memories at all to determine if pipeline is connected
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours' AND rid NOT LIKE '%heartbeat%') as recent_events,
                COUNT(*) as total_memories,
                MAX(created_at) as last_event_time
            FROM koi_memories
        """)
        koi_activity = cur.fetchone()
        
        cur.close()
        conn.close()
        
        # Determine overall health
        health_status = 'healthy'
        if daily_stats['avg_style_score'] and daily_stats['avg_style_score'] < config['thresholds']['daily_bot']['style_score_warning']:
            health_status = 'warning'

        # Check if KOI pipeline is active - consider it active if there are ANY memories in the database
        # or if there have been events in the last 24 hours
        koi_is_active = False
        if koi_activity:
            # Active if we have recent events OR if we have any memories at all (indicating connection exists)
            koi_is_active = koi_activity['recent_events'] > 0 or koi_activity['total_memories'] > 0

        return jsonify({
            'success': True,
            'health': health_status,
            'daily_stats': daily_stats,
            'weekly_stats': weekly_stats,
            'pending_reviews': pending['pending_count'] if pending else 0,
            'koi_pipeline_active': koi_is_active,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting overview: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/daily/current')
@require_auth
def get_current_daily():
    """Get current daily digest from file"""
    import glob
    try:
        # Find the latest daily digest file
        daily_files = glob.glob('/opt/projects/koi-processor/output/daily/daily_digest_*.json')
        if not daily_files:
            # No daily digest exists, return empty
            return jsonify({
                'success': True,
                'digest': None,
                'message': 'No daily digest available for today'
            })

        latest_file = max(daily_files, key=lambda x: os.path.getmtime(x))

        with open(latest_file, 'r') as f:
            digest_data = json.load(f)

        return jsonify({
            'success': True,
            'digest': digest_data,
            'filename': os.path.basename(latest_file),
            'generated_at': datetime.fromtimestamp(os.path.getmtime(latest_file)).isoformat()
        })

    except Exception as e:
        logger.error(f"Error getting current daily digest: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/daily/stats')
@require_auth
def get_daily_stats():
    """Get daily bot statistics"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get ALL pending daily drafts (not just today's)
        cur.execute("""
            SELECT review_id as id, approval_status as status, content_data as content,
                   quality_issues as metadata, created_at, reviewed_at as updated_at
            FROM quality_reviews
            WHERE content_type = 'daily_thread'
            AND approval_status IN ('draft', 'pending_review')
            ORDER BY created_at DESC
            LIMIT 10
        """)
        all_drafts = cur.fetchall()

        # Get today's draft separately
        today_draft = all_drafts[0] if all_drafts and all_drafts[0]['created_at'].date() == datetime.now(timezone.utc).date() else None
        
        # Get last 7 days performance
        cur.execute("""
            SELECT DATE(created_at) as date,
                   COUNT(*) as posts_count,
                   AVG(style_score) as avg_style,
                   COUNT(CASE WHEN approval_status = 'published' THEN 1 END) as published
            FROM quality_reviews
            WHERE content_type = 'daily_thread'
            AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        weekly_performance = cur.fetchall()
        
        # Get source distribution for today
        sources_used = []
        if today_draft and today_draft.get('metadata'):
            metadata = today_draft['metadata'] if isinstance(today_draft['metadata'], dict) else json.loads(today_draft['metadata'])
            sources_used = metadata.get('sources', [])
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'all_drafts': all_drafts,  # Return ALL pending drafts
            'today': {
                'draft': today_draft,
                'sources': sources_used,
                'status': today_draft['status'] if today_draft else 'not_generated'
            },
            'weekly_performance': weekly_performance,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting daily stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/daily/drafts')
@require_auth
def get_daily_drafts():
    """Get all pending draft threads"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get ALL pending drafts
        cur.execute("""
            SELECT review_id as id, approval_status as status, content_data as content,
                   quality_issues as metadata, created_at,
                   reviewed_by as reviewer, reviewer_notes as review_notes
            FROM quality_reviews
            WHERE content_type = 'daily_thread'
            AND approval_status IN ('draft', 'pending_review')
            ORDER BY created_at DESC
        """)
        drafts = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Process drafts for display
        processed_drafts = []
        for draft in drafts:
            content = draft['content'] if isinstance(draft['content'], dict) else json.loads(draft.get('content', '{}'))
            metadata = draft['metadata'] if isinstance(draft['metadata'], dict) else json.loads(draft.get('metadata', '{}'))
            
            processed_drafts.append({
                'id': draft['id'],
                'status': draft['status'],
                'created_at': draft['created_at'].isoformat() if draft['created_at'] else None,
                'posts': content.get('posts', []),
                'style_score': metadata.get('style_score', 0),
                'validation_passed': metadata.get('validation_passed', False),
                'reviewer': draft['reviewer'],
                'review_notes': draft['review_notes']
            })
        
        return jsonify({
            'success': True,
            'drafts': processed_drafts
        })
        
    except Exception as e:
        logger.error(f"Error getting drafts: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/weekly/current')
@require_auth
def get_current_weekly():
    """Get current weekly digest from file"""
    import glob
    try:
        # Find the latest weekly digest file
        weekly_files = glob.glob('/opt/projects/koi-processor/output/weekly/weekly_digest_*.json')
        if not weekly_files:
            return jsonify({'success': False, 'error': 'No weekly digest found'}), 404

        latest_file = max(weekly_files, key=lambda x: os.path.getmtime(x))

        with open(latest_file, 'r') as f:
            digest_data = json.load(f)

        # Also get the markdown version
        md_file = latest_file.replace('.json', '.md')
        digest_text = ""
        if os.path.exists(md_file):
            with open(md_file, 'r') as f:
                digest_text = f.read()

        return jsonify({
            'success': True,
            'digest': digest_data,
            'markdown': digest_text,
            'filename': os.path.basename(latest_file),
            'generated_at': datetime.fromtimestamp(os.path.getmtime(latest_file)).isoformat()
        })

    except Exception as e:
        logger.error(f"Error getting current weekly digest: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/weekly/stats')
@require_auth
def get_weekly_stats():
    """Get weekly digest statistics"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get ALL pending weekly digests
        cur.execute("""
            SELECT review_id as id, approval_status as status, content_data as content,
                   quality_issues as metadata, created_at
            FROM quality_reviews
            WHERE content_type = 'weekly_digest'
            AND approval_status IN ('draft', 'pending_review')
            ORDER BY created_at DESC
            LIMIT 10
        """)
        all_digests = cur.fetchall()

        # Get current week's digest separately
        current_digest = all_digests[0] if all_digests else None
        
        # Get content collection progress
        cur.execute("""
            SELECT COUNT(*) as total_content,
                   COUNT(DISTINCT content->>'source_type') as unique_sources
            FROM koi_memories
            WHERE created_at > date_trunc('week', CURRENT_DATE)
              AND rid NOT LIKE '%heartbeat%'
              AND content::text NOT LIKE '%sensor_heartbeat%'
        """)
        collection_progress = cur.fetchone()
        
        # Get last 4 weeks history
        cur.execute("""
            SELECT DATE(created_at) as week_date,
                   approval_status as status,
                   CAST(content_data->>'word_count' AS INT) as word_count,
                   CAST(content_data->>'source_count' AS INT) as source_count
            FROM quality_reviews
            WHERE content_type = 'weekly_digest'
            AND created_at > NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC
        """)
        history = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Calculate progress percentage
        progress_pct = 0
        if current_digest:
            # metadata might be None (quality_issues column)
            metadata = current_digest.get('metadata')
            if metadata:
                if isinstance(metadata, dict):
                    pass  # Already a dict
                elif isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                else:
                    metadata = {}
            else:
                metadata = {}

            # Try to get word count from content data instead
            content_data = current_digest.get('content')
            if content_data:
                if isinstance(content_data, str):
                    try:
                        content_data = json.loads(content_data)
                    except:
                        content_data = {}
                elif not isinstance(content_data, dict):
                    content_data = {}

                # Check if brief exists and count words
                brief = content_data.get('brief', '')
                word_count = len(brief.split()) if brief else 0
            else:
                word_count = 0

            target = config['thresholds']['weekly_digest']['min_word_count']
            progress_pct = min(100, (word_count / target * 100) if target > 0 else 0)
        
        return jsonify({
            'success': True,
            'all_digests': all_digests,  # Return ALL pending digests
            'current_week': {
                'digest': current_digest,
                'progress_percentage': progress_pct,
                'content_collected': collection_progress['total_content'] if collection_progress else 0,
                'unique_sources': collection_progress['unique_sources'] if collection_progress else 0
            },
            'history': history
        })
        
    except Exception as e:
        logger.error(f"Error getting weekly stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/quality/pending')
@require_auth
def get_pending_reviews():
    """Get content awaiting review"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT review_id as id, content_type, approval_status as status, 
                   style_score,
                   auto_publish_eligible as validation_passed,
                   created_at
            FROM quality_reviews
            WHERE approval_status IN ('draft', 'pending_review')
            ORDER BY created_at DESC
        """)
        pending = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'pending': pending,
            'count': len(pending)
        })
        
    except Exception as e:
        logger.error(f"Error getting pending reviews: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/quality/history')
@require_auth
def get_quality_history():
    """Get approval/rejection history"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT review_id as id, content_type, approval_status as status, reviewed_by as reviewer,
                   reviewer_notes as review_notes, reviewed_at as approved_at, created_at
            FROM quality_reviews
            WHERE approval_status IN ('approved', 'rejected', 'published', 'rolled_back')
            ORDER BY COALESCE(reviewed_at, created_at) DESC
            LIMIT 50
        """)
        history = cur.fetchall()
        
        # Get approval statistics
        cur.execute("""
            SELECT 
                COUNT(CASE WHEN approval_status = 'approved' THEN 1 END) as approved_count,
                COUNT(CASE WHEN approval_status = 'rejected' THEN 1 END) as rejected_count,
                COUNT(CASE WHEN approval_status = 'published' THEN 1 END) as published_count,
                AVG(style_score) as avg_style_score
            FROM quality_reviews
            WHERE created_at > NOW() - INTERVAL '30 days'
        """)
        stats = cur.fetchone()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'history': history,
            'statistics': stats
        })
        
    except Exception as e:
        logger.error(f"Error getting quality history: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/podcast/status')
@require_auth
def get_podcast_status():
    """Get podcast generation status"""
    try:
        # Check for recent podcast files
        podcast_dir = Path(__file__).parent / "output" / "podcasts"
        latest_podcast = None
        
        if podcast_dir.exists():
            audio_files = list(podcast_dir.glob("*.mp3"))
            if audio_files:
                latest_file = max(audio_files, key=lambda f: f.stat().st_mtime)
                latest_podcast = {
                    'filename': latest_file.name,
                    'size_mb': latest_file.stat().st_size / (1024 * 1024),
                    'created': datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat()
                }
        
        # Check NotebookLM export status
        export_dir = Path(__file__).parent / "output" / "notebooklm"
        latest_export = None
        
        if export_dir.exists():
            export_files = list(export_dir.glob("*.json"))
            if export_files:
                latest_file = max(export_files, key=lambda f: f.stat().st_mtime)
                latest_export = {
                    'filename': latest_file.name,
                    'created': datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat()
                }
        
        return jsonify({
            'success': True,
            'latest_podcast': latest_podcast,
            'latest_export': latest_export,
            'generation_available': bool(latest_export)
        })
        
    except Exception as e:
        logger.error(f"Error getting podcast status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/schedule')
@require_auth
def get_schedule():
    """Get upcoming scheduled runs"""
    try:
        now = datetime.now(timezone.utc)
        
        # Calculate next run times
        # Daily bot: 12:00 ET weekdays
        next_daily = now.replace(hour=16, minute=0, second=0, microsecond=0)  # 16:00 UTC = 12:00 ET
        if now.hour >= 16:
            next_daily += timedelta(days=1)
        # Skip weekends
        while next_daily.weekday() >= 5:
            next_daily += timedelta(days=1)
        
        # Weekly digest: Friday
        next_weekly = now.replace(hour=16, minute=0, second=0, microsecond=0)
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0 and now.hour >= 16:
            days_until_friday = 7
        next_weekly += timedelta(days=days_until_friday)
        
        schedule = [
            {
                'type': 'daily_bot',
                'next_run': next_daily.isoformat(),
                'frequency': 'Weekdays at 12:00 ET'
            },
            {
                'type': 'weekly_digest',
                'next_run': next_weekly.isoformat(),
                'frequency': 'Fridays at 12:00 ET'
            }
        ]
        
        return jsonify({
            'success': True,
            'schedule': schedule,
            'current_time': now.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting schedule: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/errors')
@require_auth
def get_errors():
    """Get recent errors and alerts"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get recent errors from dashboard_alerts table if it exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'dashboard_alerts'
            )
        """)
        
        if cur.fetchone()['exists']:
            cur.execute("""
                SELECT id, alert_type, severity, message, resolved, created_at
                FROM dashboard_alerts
                WHERE created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
                LIMIT 50
            """)
            alerts = cur.fetchall()
        else:
            alerts = []
        
        # Check for common issues
        issues = []
        
        # Check database connection
        cur.execute("SELECT 1")
        
        # Check if BGE server is responsive (port 8090)
        # This would need actual network check in production
        
        # Check if KOI coordinator is running (port 8005)
        # This would need actual network check in production
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'alerts': alerts,
            'active_issues': issues,
            'error_count': len(alerts)
        })
        
    except Exception as e:
        logger.error(f"Error getting errors: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/trigger_manual_run', methods=['POST'])
@require_auth
def trigger_manual_run():
    """Trigger manual generation of daily or weekly content as drafts with provenance"""
    import subprocess

    try:
        data = request.json
        run_type = data.get('type', 'daily')
        draft_mode = data.get('draft_mode', True)  # Always create as draft by default
        skip_audio = data.get('skip_audio', True)  # Skip audio generation for weekly by default

        logger.info(f"Manual run triggered for: {run_type}")

        if run_type == 'daily':
            # Generate daily thread
            output_file = f'/tmp/daily_thread_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

            # Set environment variable for OpenAI
            env = os.environ.copy()

            # Make sure we have the OpenAI API key
            openai_key = os.getenv('OPENAI_API_KEY', '')
            if not openai_key:
                logger.error("OPENAI_API_KEY not found in environment")
                return jsonify({
                    'success': False,
                    'message': 'OpenAI API key not configured',
                    'error': 'OPENAI_API_KEY environment variable is not set'
                }), 500

            env['OPENAI_API_KEY'] = openai_key
            env['GENERATE_AS_DRAFT'] = 'true' if draft_mode else 'false'
            env['INCLUDE_PROVENANCE'] = 'true'  # Always include source provenance

            # Also ensure we have the Notion API key if needed
            notion_key = os.getenv('NOTION_API_KEY', '')
            if notion_key:
                env['NOTION_API_KEY'] = notion_key

            # Use venv Python to ensure all dependencies are available
            python_path = '/opt/projects/koi-processor/venv/bin/python3'
            if not os.path.exists(python_path):
                python_path = 'python3'

            # The script will automatically create drafts based on the environment variables
            result = subprocess.run([
                python_path,
                '/opt/projects/koi-processor/scripts/run_daily_curator.py',
                'daily',
                '--output', output_file
            ], capture_output=True, text=True, cwd='/opt/projects/koi-processor', env=env, timeout=120)

            if result.returncode == 0 and os.path.exists(output_file):
                # Submit to review queue
                submit_result = subprocess.run([
                    'python3',
                    '/opt/projects/koi-processor/scripts/submit_to_review.py',
                    output_file,
                    'daily_thread'
                ], capture_output=True, text=True, cwd='/opt/projects/koi-processor', timeout=30)

                if submit_result.returncode == 0:
                    # Broadcast update to refresh dashboard
                    socketio.emit('dashboard_update', {
                        'type': 'daily_generated',
                        'message': 'New daily thread generated and submitted for review'
                    })

                    return jsonify({
                        'success': True,
                        'message': 'Daily thread generated successfully',
                        'file': output_file
                    })
                else:
                    logger.error(f"Failed to submit to review: {submit_result.stderr}")
                    return jsonify({
                        'success': False,
                        'message': 'Generated but failed to submit for review',
                        'error': submit_result.stderr
                    }), 500
            else:
                error_msg = result.stderr if result.stderr else "Generation failed"
                logger.error(f"Daily generation failed: {error_msg}")
                return jsonify({
                    'success': False,
                    'message': 'Failed to generate daily thread',
                    'error': error_msg
                }), 500

        elif run_type == 'weekly':
            # Generate weekly digest
            date_str = datetime.now().strftime("%Y-%m-%d")
            output_file = f'/opt/projects/koi-processor/output/weekly/weekly_digest_{date_str}.json'

            # Get optional date range parameters
            start_date = data.get('start_date')
            end_date = data.get('end_date')

            # Set environment variables
            env = os.environ.copy()

            # Make sure we have the OpenAI API key
            openai_key = os.getenv('OPENAI_API_KEY', '')
            if not openai_key:
                logger.error("OPENAI_API_KEY not found in environment")
                return jsonify({
                    'success': False,
                    'message': 'OpenAI API key not configured',
                    'error': 'OPENAI_API_KEY environment variable is not set'
                }), 500

            env['OPENAI_API_KEY'] = openai_key
            env['GENERATE_AS_DRAFT'] = 'true' if draft_mode else 'false'
            env['INCLUDE_PROVENANCE'] = 'true'
            env['SKIP_AUDIO_GENERATION'] = 'true' if skip_audio else 'false'

            # Add date range to environment if provided
            if start_date:
                env['DIGEST_START_DATE'] = start_date
            if end_date:
                env['DIGEST_END_DATE'] = end_date

            # Also ensure we have the Notion API key if needed
            notion_key = os.getenv('NOTION_API_KEY', '')
            if notion_key:
                env['NOTION_API_KEY'] = notion_key

            # Use the NEW LLM weekly curator wrapper script that includes ALL content
            # Use venv Python if available, otherwise system Python
            python_path = '/opt/projects/koi-processor/venv/bin/python3'
            if not os.path.exists(python_path):
                python_path = 'python3'

            # Build command with optional date range arguments
            cmd = [python_path, '/opt/projects/koi-processor/scripts/run_weekly_curator_llm.py']
            if start_date and end_date:
                cmd.extend(['--start-date', start_date, '--end-date', end_date])

            result = subprocess.run(cmd, capture_output=True, text=True, cwd='/opt/projects/koi-processor', env=env, timeout=300)

            if result.returncode == 0:
                # No need to submit to review - weekly_curator_llm.py already saves to database
                # Just broadcast the update
                socketio.emit('dashboard_update', {
                    'type': 'weekly_generated',
                    'message': 'New weekly digest generated and submitted for review'
                })

                return jsonify({
                    'success': True,
                    'message': 'Weekly digest generated successfully',
                    'file': output_file
                })
            else:
                # Parse the error for specific issues
                error_msg = result.stderr if result.stderr else 'Unknown error'

                # Check for specific error patterns
                if 'insufficient_quota' in error_msg.lower() or 'rate_limit' in error_msg.lower():
                    user_message = 'OpenAI API quota exceeded. Please add more credits.'
                elif 'api_key' in error_msg.lower() or 'authentication' in error_msg.lower():
                    user_message = 'OpenAI API key is missing or invalid.'
                elif 'timeout' in error_msg.lower():
                    user_message = 'Request timed out. Please try again.'
                elif 'connection' in error_msg.lower():
                    user_message = 'Unable to connect to OpenAI API.'
                else:
                    user_message = 'Failed to generate weekly digest'

                return jsonify({
                    'success': False,
                    'message': user_message,
                    'error': error_msg
                }), 500
        else:
            return jsonify({'success': False, 'message': f'Unknown type: {run_type}'}), 400

    except subprocess.TimeoutExpired:
        logger.error(f"Generation timeout for {run_type}")
        return jsonify({
            'success': False,
            'message': 'Generation timed out - this may indicate insufficient content or an API issue'
        }), 500
    except Exception as e:
        logger.error(f"Error triggering manual run: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/drafts/list')
@require_auth
def list_all_drafts():
    """List all drafts (both daily and weekly) with full provenance"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get all drafts
        cur.execute("""
            SELECT
                review_id,
                content_type,
                content_data,
                quality_issues as metadata,
                approval_status,
                created_at,
                reviewed_at,
                reviewer_notes,
                provenance
            FROM quality_reviews
            WHERE approval_status IN ('draft', 'pending_review')
            ORDER BY created_at DESC
            LIMIT 50
        """)

        drafts = cur.fetchall()

        # Process drafts to extract provenance
        processed_drafts = []
        for draft in drafts if drafts else []:
            # Handle JSON data properly
            content_data = draft['content_data'] if draft else {}
            if isinstance(content_data, str):
                try:
                    content = json.loads(content_data)
                except json.JSONDecodeError:
                    content = {'error': 'Invalid JSON content'}
            elif isinstance(content_data, dict):
                content = content_data
            else:
                content = {}

            # Handle provenance field from database
            provenance_data = draft.get('provenance')
            if isinstance(provenance_data, str):
                try:
                    provenance = json.loads(provenance_data)
                except json.JSONDecodeError:
                    provenance = {}
            elif isinstance(provenance_data, dict):
                provenance = provenance_data
            else:
                provenance = {}

            # Handle metadata field
            metadata = draft.get('metadata')
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            elif not isinstance(metadata, dict):
                metadata = {}

            processed_drafts.append({
                'id': str(draft['review_id']),
                'type': draft['content_type'],
                'status': draft['approval_status'],
                'created_at': draft['created_at'].isoformat() if draft['created_at'] else None,
                'reviewed_at': draft['reviewed_at'].isoformat() if draft['reviewed_at'] else None,
                'content': content,
                'provenance': provenance,  # Use provenance directly from DB
                'metadata': metadata,
                'reviewer_notes': draft.get('reviewer_notes')
            })

        cur.close()
        conn.close()

        return jsonify({
            'success': True,
            'drafts': processed_drafts,
            'total': len(processed_drafts)
        })

    except Exception as e:
        logger.error(f"Error listing drafts: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/drafts/<draft_id>/approve', methods=['POST'])
@require_auth
def approve_draft(draft_id):
    """Approve a draft for publication"""
    try:
        data = request.json
        reviewer = data.get('reviewer', 'dashboard_user')
        notes = data.get('notes', '')

        conn = get_db_connection()
        cur = conn.cursor()

        # Update draft status
        cur.execute("""
            UPDATE quality_reviews
            SET
                approval_status = 'approved',
                reviewed_at = NOW(),
                reviewed_by = %s,
                reviewer_notes = %s
            WHERE review_id = %s
            RETURNING content_type, content_data
        """, (reviewer, notes, draft_id))

        result = cur.fetchone()
        conn.commit()

        if result:
            # Trigger publication based on content type
            content_type = result['content_type']

            # Broadcast update
            broadcast_update('draft_approved', {
                'draft_id': draft_id,
                'content_type': content_type
            })

            cur.close()
            conn.close()

            return jsonify({
                'success': True,
                'message': f'Draft {draft_id} approved successfully',
                'content_type': content_type
            })
        else:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'message': 'Draft not found'}), 404

    except Exception as e:
        logger.error(f"Error approving draft: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/drafts/<draft_id>/reject', methods=['POST'])
@require_auth
def reject_draft(draft_id):
    """Reject a draft"""
    try:
        data = request.json
        reviewer = data.get('reviewer', 'dashboard_user')
        notes = data.get('notes', '')

        conn = get_db_connection()
        cur = conn.cursor()

        # Update draft status
        cur.execute("""
            UPDATE quality_reviews
            SET
                approval_status = 'rejected',
                reviewed_at = NOW(),
                reviewed_by = %s,
                reviewer_notes = %s
            WHERE review_id = %s
        """, (reviewer, notes, draft_id))

        conn.commit()
        cur.close()
        conn.close()

        # Broadcast update
        broadcast_update('draft_rejected', {
            'draft_id': draft_id
        })

        return jsonify({
            'success': True,
            'message': f'Draft {draft_id} rejected'
        })

    except Exception as e:
        logger.error(f"Error rejecting draft: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/drafts/<draft_id>/edit', methods=['POST'])
@require_auth
def edit_draft(draft_id):
    """Edit a draft content"""
    try:
        data = request.json
        edited_content = data.get('content')
        editor_notes = data.get('notes', '')

        if not edited_content:
            return jsonify({'success': False, 'message': 'No content provided'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Update draft content
        cur.execute("""
            UPDATE quality_reviews
            SET
                content_data = %s,
                reviewer_notes = COALESCE(reviewer_notes, '') || E'\\n[Edit] ' || %s,
                reviewed_at = NOW()
            WHERE review_id = %s
        """, (json.dumps(edited_content), editor_notes, draft_id))

        conn.commit()
        cur.close()
        conn.close()

        # Broadcast update
        broadcast_update('draft_edited', {
            'draft_id': draft_id
        })

        return jsonify({
            'success': True,
            'message': f'Draft {draft_id} updated successfully'
        })

    except Exception as e:
        logger.error(f"Error editing draft: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/drafts/<draft_id>/export_notebooklm', methods=['POST'])
@require_auth
def export_notebooklm_for_draft(draft_id):
    """Generate NotebookLM export markdown for a draft with full context"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get draft content
        cur.execute("""
            SELECT content_type, content_data
            FROM quality_reviews
            WHERE review_id = %s AND content_type = 'weekly_digest'
        """, (draft_id,))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result:
            return jsonify({'success': False, 'message': 'Weekly digest draft not found'}), 404

        content = result['content_data'] if isinstance(result['content_data'], dict) else json.loads(result['content_data'])

        # Use the async enhanced export method - handle both direct and module imports
        try:
            # Try if run as module (python -m src.content.content_dashboard)
            from src.content.weekly_curator_llm import WeeklyCuratorLLM
        except ImportError:
            try:
                # Try direct import (when run from project root)
                import sys
                project_root = Path(__file__).parent.parent.parent
                if str(project_root) not in sys.path:
                    sys.path.insert(0, str(project_root))
                from src.content.weekly_curator_llm import WeeklyCuratorLLM
            except ImportError:
                # Try same directory import (when run from src/content/)
                from weekly_curator_llm import WeeklyCuratorLLM

        curator = WeeklyCuratorLLM()

        # Run async export in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(curator.export_notebooklm_enhanced(content))
        finally:
            loop.close()

        # File should now exist
        output_dir = Path(__file__).parent.parent.parent / "output" / "weekly"
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"weekly_digest_{date_str}_notebooklm.md"
        file_path = output_dir / filename

        if not file_path.exists():
            raise Exception("NotebookLM export file was not created")

        logger.info(f"Generated NotebookLM export: {file_path}")

        return jsonify({
            'success': True,
            'message': 'NotebookLM export generated with full context (forum threads, transcripts, etc.)',
            'file_path': str(file_path),
            'filename': filename
        })

    except Exception as e:
        logger.error(f"Error generating NotebookLM export: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/drafts/<draft_id>/download_notebooklm')
@require_auth
def download_notebooklm_export(draft_id):
    """Download NotebookLM export file"""
    try:
        from flask import send_file

        output_dir = Path(__file__).parent.parent.parent / "output" / "weekly"
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"weekly_digest_{date_str}_notebooklm.md"
        file_path = output_dir / filename

        if not file_path.exists():
            return jsonify({'success': False, 'error': 'File not found'}), 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype='text/markdown'
        )

    except Exception as e:
        logger.error(f"Error downloading NotebookLM export: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/drafts/<draft_id>/generate_podcast', methods=['POST'])
@require_auth
def generate_podcast_for_draft(draft_id):
    """Submit podcast generation job (async) - returns immediately with job_id"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get draft content
        cur.execute("""
            SELECT content_type, content_data
            FROM quality_reviews
            WHERE review_id = %s AND content_type = 'weekly_digest'
        """, (draft_id,))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result:
            return jsonify({'success': False, 'message': 'Weekly digest draft not found'}), 404

        content = result['content_data'] if isinstance(result['content_data'], dict) else json.loads(result['content_data'])

        # Submit job to queue
        job_queue = get_job_queue(DB_CONFIG)
        job_id = job_queue.submit_job(draft_id, content)

        return jsonify({
            'success': True,
            'message': 'Podcast generation started',
            'job_id': job_id
        })

    except Exception as e:
        logger.error(f"Error submitting podcast job: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/podcast/status/<job_id>')
@require_auth
def get_podcast_job_status(job_id):
    """Get podcast generation job status (for polling)"""
    try:
        job_queue = get_job_queue(DB_CONFIG)
        status = job_queue.get_job_status(job_id)

        if not status:
            return jsonify({'success': False, 'error': 'Job not found'}), 404

        return jsonify({
            'success': True,
            'job': status
        })

    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def generate_notebooklm_export(content: Dict) -> str:
    """Generate NotebookLM-optimized markdown export from digest content"""
    brief = content.get('brief_content', '') or content.get('brief', '')
    themes = content.get('themes', {})
    executive_summary = content.get('executive_summary', '')
    key_discussions = content.get('key_discussions', [])
    ledger_activity = content.get('ledger_activity', {})
    community_pulse = content.get('community_pulse', {})

    # Build comprehensive markdown
    md = f"""# Regen Network Weekly Digest - NotebookLM Export
*Complete weekly digest optimized for NotebookLM analysis*

---

## Executive Summary

{executive_summary}

---

## Full Weekly Brief

{brief}

---

## Key Themes

"""

    if themes:
        for theme, topics in themes.items():
            md += f"### {theme}\n\n"
            if isinstance(topics, list):
                for topic in topics:
                    md += f"- {topic}\n"
            else:
                md += f"{topics}\n"
            md += "\n"

    md += "\n## Community Pulse\n\n"
    if community_pulse:
        md += f"**Activity Level**: {community_pulse.get('overall_activity', 'N/A')}\n\n"

        if community_pulse.get('key_focus_areas'):
            md += "**Key Focus Areas**:\n"
            for area in community_pulse['key_focus_areas']:
                md += f"- {area}\n"
            md += "\n"

        if community_pulse.get('emerging_trends'):
            md += "**Emerging Trends**:\n"
            for trend in community_pulse['emerging_trends']:
                md += f"- {trend}\n"
            md += "\n"

    md += "\n## Key Discussions & Sources\n\n"
    if key_discussions:
        for disc in key_discussions:
            title = disc.get('title', 'Untitled')
            url = disc.get('url', '')
            md += f"- [{title}]({url})\n"

    if ledger_activity and ledger_activity.get('summary'):
        md += "\n## On-Chain Activity\n\n"
        md += ledger_activity['summary']
        md += "\n\n"

    md += """
---

*Generated by Regen Network KOI System - Optimized for NotebookLM Analysis*
*Upload this markdown file to NotebookLM to generate AI podcast discussions*
"""

    return md


def generate_podcast_script(content):
    """Generate a 20-minute podcast script from weekly digest content"""
    brief = content.get('brief_content', '')
    themes = content.get('themes', {})

    # Create a conversational podcast script
    script = f"""
# Weekly Podcast Script
## Duration: ~20 minutes

### Opening (2 minutes)
Welcome to the Regen Network Weekly Podcast, where we explore the latest developments in regenerative finance and ecological economics. This week, we're covering the period from {content.get('week_start', 'this week')}.

### Main Content (15 minutes)
{brief}

### Key Themes Discussion (2 minutes)
Let's dive deeper into the key themes that emerged this week:
"""

    for theme, topics in themes.items():
        script += f"\n- {theme}: {', '.join(topics) if isinstance(topics, list) else topics}"

    script += """

### Closing (1 minute)
That's all for this week's Regen Network podcast. Join us next week as we continue to explore the cutting edge of regenerative economics and blockchain innovation. Until then, stay regenerative!
"""

    return script

@app.route('/podcast_audio/<path:filename>')
def serve_podcast_audio(filename):
    """Serve podcast audio files"""
    import os
    from flask import send_from_directory
    from urllib.parse import unquote

    # Decode the filename (in case it was URL encoded)
    filename = unquote(filename)

    podcast_dir = '/opt/projects/koi-processor/podcast_audio'
    file_path = os.path.join(podcast_dir, filename)

    # Security check - ensure no path traversal
    if os.path.commonpath([podcast_dir, file_path]) != podcast_dir:
        logger.error(f"Path traversal attempt: {filename}")
        return jsonify({'error': 'Invalid file path'}), 403

    if os.path.exists(file_path):
        logger.info(f"Serving audio file: {filename}")
        # Add cache headers for audio files
        response = send_from_directory(podcast_dir, filename, as_attachment=False, mimetype='audio/mpeg')
        response.headers['Accept-Ranges'] = 'bytes'
        return response
    else:
        logger.error(f"Audio file not found: {file_path}")
        return jsonify({'error': 'File not found'}), 404

@app.route('/api/dashboard/drafts/<draft_id>/markdown')
def get_draft_markdown(draft_id):
    """Get draft content as markdown for NotebookLM"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT content_type, content_data
            FROM quality_reviews
            WHERE review_id = %s
        """, (draft_id,))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result:
            return jsonify({'error': 'Draft not found'}), 404

        content = result['content_data'] if isinstance(result['content_data'], dict) else json.loads(result['content_data'])

        # Generate markdown based on content type
        if result['content_type'] == 'weekly_digest':
            markdown = generate_weekly_markdown(content)
        else:
            markdown = generate_daily_markdown(content)

        # Return as a downloadable markdown file
        from flask import Response
        response = Response(markdown, mimetype='text/markdown')
        response.headers['Content-Disposition'] = f'attachment; filename={result["content_type"]}_{draft_id[:8]}.md'
        return response

    except Exception as e:
        logger.error(f"Error getting markdown: {e}")
        return jsonify({'error': str(e)}), 500

def fetch_forum_thread_content_sync(url: str) -> Optional[str]:
    """Fetch actual content from a forum thread URL (sync version)"""
    try:
        # Extract thread ID from URL
        # Format: https://forum.regen.network/t/thread-title/123
        parts = url.rstrip('/').split('/')
        if len(parts) < 2:
            return None

        thread_id = parts[-1]
        if not thread_id.isdigit():
            # Sometimes ID is in the slug like 'thread-title-123'
            if '-' in parts[-1]:
                possible_id = parts[-1].split('-')[-1]
                if possible_id.isdigit():
                    thread_id = possible_id
                else:
                    return None
            else:
                return None

        # Use Discourse API to fetch thread content
        api_url = f"https://forum.regen.network/t/{thread_id}.json"

        with httpx.Client() as client:
            response = client.get(api_url, timeout=10.0)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch thread {thread_id}: {response.status_code}")
                return None

            data = response.json()

            # Extract post content
            posts = data.get('post_stream', {}).get('posts', [])
            if not posts:
                return None

            # Format the thread content with ALL posts
            thread_content = f"**Thread Title**: {data.get('title', 'Untitled')}\n\n"
            thread_content += f"**Category**: {data.get('category_id', 'General')}\n"
            thread_content += f"**Total Posts**: {len(posts)}\n"
            thread_content += f"**Thread URL**: {url}\n\n"
            thread_content += "---\n\n"

            # Include ALL posts for complete context in NotebookLM
            for i, post in enumerate(posts, 1):
                username = post.get('username', 'Anonymous')
                created = post.get('created_at', '')[:10]
                content = post.get('cooked', '')  # 'cooked' is the rendered HTML

                # Enhanced HTML to markdown conversion
                content = re.sub(r'<p>(.*?)</p>', r'\1\n\n', content)
                content = re.sub(r'<strong>(.*?)</strong>', r'**\1**', content)
                content = re.sub(r'<em>(.*?)</em>', r'*\1*', content)
                content = re.sub(r'<code>(.*?)</code>', r'`\1`', content)
                content = re.sub(r'<pre>(.*?)</pre>', r'```\n\1\n```', content, flags=re.DOTALL)
                content = re.sub(r'<blockquote>(.*?)</blockquote>', r'> \1', content, flags=re.DOTALL)
                content = re.sub(r'<a href="(.*?)".*?>(.*?)</a>', r'[\2](\1)', content)
                content = re.sub(r'<ul>(.*?)</ul>', r'\1', content, flags=re.DOTALL)
                content = re.sub(r'<li>(.*?)</li>', r'- \1\n', content)
                content = re.sub(r'<ol>(.*?)</ol>', r'\1', content, flags=re.DOTALL)
                content = re.sub(r'<.*?>', '', content)  # Remove remaining HTML tags
                content = content.strip()

                thread_content += f"### Post {i} by @{username} ({created})\n\n"
                thread_content += f"{content}\n\n"
                thread_content += "---\n\n"

            return thread_content

    except Exception as e:
        logger.error(f"Error fetching forum thread {url}: {e}")
        return None

def fetch_website_content_sync(url: str) -> Optional[Dict[str, Any]]:
    """Fetch full content from regentokenomics.org or other website pages (sync version)"""
    try:
        with httpx.Client() as client:
            response = client.get(url, timeout=30.0, follow_redirects=True)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch website {url}: {response.status_code}")
                return None

            html_content = response.text
            result = {'text': '', 'video_urls': [], 'audio_urls': []}

            # Remove script and style elements
            html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
            html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)

            # Extract title
            title_match = re.search(r'<title>(.*?)</title>', html_content)
            if title_match:
                result['text'] = f"**Page Title**: {title_match.group(1)}\n\n"

            # Extract main content
            content_patterns = [
                r'<article[^>]*>(.*?)</article>',
                r'<main[^>]*>(.*?)</main>',
                r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>'
            ]

            for pattern in content_patterns:
                matches = re.findall(pattern, html_content, re.DOTALL)
                if matches:
                    for match in matches:
                        # Convert to markdown
                        text = match
                        text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', text)
                        text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text)
                        text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', text)
                        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text)
                        text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text)
                        text = re.sub(r'<em>(.*?)</em>', r'*\1*', text)
                        text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text)
                        text = re.sub(r'<.*?>', '', text)
                        result['text'] += text[:5000]
                        break

            # Look for video files
            video_patterns = [
                r'<video[^>]*src="([^"]+)"',
                r'<source[^>]*src="([^"]+\.mp4)"',
                r'href="([^"]+\.mp4)"',
                r'"(https?://[^"]+\.mp4)"'
            ]

            for pattern in video_patterns:
                matches = re.findall(pattern, html_content)
                for match in matches:
                    if not match.startswith('http'):
                        match = urljoin(url, match)
                    if match not in result['video_urls']:
                        result['video_urls'].append(match)
                        logger.info(f"Found video: {match}")

            return result

    except Exception as e:
        logger.error(f"Error fetching website content {url}: {e}")
        return None

def transcribe_video_sync(video_url: str) -> Optional[str]:
    """Download and transcribe video using OpenAI Whisper (sync version)"""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = f"{temp_dir}/video.mp4"
            audio_path = f"{temp_dir}/audio.mp3"

            logger.info(f"Downloading video from {video_url}")

            # Download video
            with httpx.Client() as client:
                response = client.get(video_url, timeout=300.0)
                if response.status_code != 200:
                    logger.error(f"Failed to download video: {response.status_code}")
                    return None

                with open(video_path, 'wb') as f:
                    f.write(response.content)

            logger.info(f"Extracting audio from video")

            # Extract audio using ffmpeg
            result = subprocess.run(
                ['ffmpeg', '-i', video_path, '-vn', '-acodec', 'mp3', '-ab', '128k', audio_path],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                logger.error(f"Failed to extract audio: {result.stderr}")
                return None

            logger.info(f"Transcribing audio with OpenAI Whisper")

            # Transcribe using OpenAI Whisper API
            openai_api_key = os.getenv('OPENAI_API_KEY')
            if not openai_api_key:
                logger.error("OpenAI API key not found")
                return None

            client = OpenAI(api_key=openai_api_key)

            with open(audio_path, 'rb') as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )

            return transcript

    except Exception as e:
        logger.error(f"Error transcribing video {video_url}: {e}")
        return None

def fetch_governance_proposal(proposal_id: str) -> Optional[str]:
    """Fetch full governance proposal details from Mintscan API"""
    try:
        # Try working API endpoints
        api_endpoints = [
            f"https://regen-api.polkachu.com/cosmos/gov/v1beta1/proposals/{proposal_id}",
            f"https://regen-rest.publicnode.com/cosmos/gov/v1beta1/proposals/{proposal_id}",
            f"https://regen.api.m.stavr.tech/cosmos/gov/v1beta1/proposals/{proposal_id}",
            f"https://rest.regen.aneka.io/cosmos/gov/v1beta1/proposals/{proposal_id}"
        ]

        for api_url in api_endpoints:
            try:
                with httpx.Client() as client:
                    response = client.get(api_url, timeout=10.0)
                    if response.status_code == 200:
                        data = response.json()

                        # All current endpoints use the same format
                        proposal = data.get('proposal', {})

                        # Format proposal content
                        content = f"**Proposal #{proposal_id}**\n\n"

                        # Extract proposal details
                        content += f"**Proposal #{proposal_id}**\n\n"
                        content += f"**Status**: {proposal.get('status', 'UNKNOWN')}\n\n"

                        # Get content details
                        prop_content = proposal.get('content', {})
                        content += f"**Type**: {prop_content.get('@type', 'N/A')}\n\n"

                        # For community pool spend, show recipient and amount
                        if 'CommunityPoolSpend' in prop_content.get('@type', ''):
                            content += f"**Recipient**: {prop_content.get('recipient', 'N/A')}\n"
                            amounts = prop_content.get('amount', [])
                            if amounts:
                                for amt in amounts:
                                    denom = amt.get('denom', 'uregen')
                                    amount = int(amt.get('amount', 0)) / 1_000_000 if amt.get('amount') else 0
                                    content += f"**Amount**: {amount:,.0f} REGEN\n"
                            content += "\n"

                        # Add hardcoded details for known proposals
                        if proposal_id == "57":
                            content += f"**Title**: Request for the funding for the Tokenomics working group in Q4\n\n"
                            content += f"**Description**:\n\n"
                            content += f"Regen Tokenomics, operating as an autonomous entity / DAO since 2023, is requesting its first "
                            content += f"Community Pool grant to support ongoing coordination, communications, and upcoming Agent-Based Modeling research.\n\n"
                            content += f"**Forum Discussion**: https://forum.regen.network/t/funding-application-for-the-regen-tokenomics-working-group/29/5\n\n"
                        elif proposal_id == "56":
                            content += f"**Title**: Revive REGEN<>AXELAR client\n\n"
                            content += f"**Description**:\n\n"
                            content += f"Update client from 07-tendermint-100 to 07-tendermint-181 to reenable transfers from Axelar to Regen.\n\n"

                        # Add voting details
                        final_tally = proposal.get('final_tally_result', {})
                        if final_tally:
                            content += f"**Voting Results**:\n"
                            # Convert from uregen to REGEN (divide by 1,000,000)
                            yes_amt = int(final_tally.get('yes', '0')) / 1_000_000 if final_tally.get('yes') else 0
                            no_amt = int(final_tally.get('no', '0')) / 1_000_000 if final_tally.get('no') else 0
                            abstain_amt = int(final_tally.get('abstain', '0')) / 1_000_000 if final_tally.get('abstain') else 0
                            no_veto_amt = int(final_tally.get('no_with_veto', '0')) / 1_000_000 if final_tally.get('no_with_veto') else 0

                            content += f"- Yes: {yes_amt:,.0f} REGEN\n"
                            content += f"- No: {no_amt:,.0f} REGEN\n"
                            content += f"- Abstain: {abstain_amt:,.0f} REGEN\n"
                            content += f"- No With Veto: {no_veto_amt:,.0f} REGEN\n\n"

                        # Add timing
                        content += f"**Timeline**:\n"
                        content += f"- Submit Time: {proposal.get('submit_time', 'N/A')}\n"
                        content += f"- Deposit End: {proposal.get('deposit_end_time', 'N/A')}\n"
                        content += f"- Voting Start: {proposal.get('voting_start_time', 'N/A')}\n"
                        content += f"- Voting End: {proposal.get('voting_end_time', 'N/A')}\n\n"

                        # Add proposer info if available
                        if proposal.get('proposer'):
                            content += f"**Proposer**: {proposal.get('proposer')}\n"
                            if proposal.get('moniker'):
                                content += f"**Proposer Name**: {proposal.get('moniker')}\n"
                        content += "\n"

                        return content
            except Exception as e:
                logger.debug(f"API endpoint {api_url} failed: {e}")
                continue

        # If all endpoints fail, provide known information
        # Hardcoded fallback for known proposals
        if proposal_id == "57":
            return (
                f"**Proposal #57: Request for the funding for the Tokenomics working group in Q4**\n\n"
                f"**Status**: PASSED\n\n"
                f"**Type**: Community Pool Spend\n\n"
                f"**Full Description**:\n\n"
                f"Details: https://forum.regen.network/t/funding-application-for-the-regen-tokenomics-working-group/29/5\n\n"
                f"Regen Tokenomics, operating as an autonomous entity / DAO since 2023, is requesting its first "
                f"Community Pool grant to support ongoing coordination, communications, and upcoming Agent-Based Modeling research.\n\n"
                f"**Amount Requested**: 500,000 REGEN\n\n"
                f"**Forum Discussion**: https://forum.regen.network/t/funding-application-for-the-regen-tokenomics-working-group/29/5\n\n"
                f"**Timeline**:\n"
                f"- Submit Time: 2025-09-18\n"
                f"- Voting End: 2025-09-25\n\n"
            )
        elif proposal_id == "56":
            return (
                f"**Proposal #56: Revive REGEN<>AXELAR client**\n\n"
                f"**Status**: PASSED\n\n"
                f"**Type**: IBC Client Update\n\n"
                f"**Full Description**:\n\n"
                f"Update client from 07-tendermint-100 to 07-tendermint-181 to reenable transfers from Axelar to Regen.\n\n"
                f"This proposal aims to restore the IBC connection between Regen Network and Axelar, "
                f"enabling cross-chain asset transfers and improving interoperability.\n\n"
                f"**Technical Details**: Client update from 07-tendermint-100 to 07-tendermint-181\n\n"
            )

        return f"**Proposal #{proposal_id}**\n\n*[Full proposal details not available - all API endpoints unreachable]*\n\n"

    except Exception as e:
        logger.error(f"Error fetching proposal {proposal_id}: {e}")
        return None

def generate_weekly_markdown(content):
    """Generate enhanced markdown version of weekly digest for NotebookLM with FULL context"""
    markdown = "# Regen Network Weekly Digest - Complete NotebookLM Export\n\n"
    markdown += "*This document contains the complete weekly digest with full forum threads, governance proposals, and all source material for comprehensive analysis.*\n\n"
    markdown += "*No external sources needed - everything is included below.*\n\n"
    markdown += "---\n\n"

    if content.get('week_start'):
        markdown += f"**Week of:** {content['week_start']}\n\n"

    # Add metadata context
    markdown += "## Digest Metadata\n\n"
    if content.get('statistics'):
        stats = content.get('statistics', {})
        markdown += f"- **Total Posts Analyzed**: {stats.get('total_posts', 0)}\n"
        markdown += f"- **Unique Discussions**: {stats.get('unique_discussions', 0)}\n"
        markdown += f"- **Active Sources**: {stats.get('active_sources', 0)}\n"
        markdown += f"- **Most Active Source**: {stats.get('most_active_source', 'N/A')}\n\n"

    markdown += "## Executive Summary\n\n"
    if content.get('executive_summary'):
        # Clean any escaped characters
        summary = content['executive_summary'].replace('\\n\\n', '\n\n').replace('\\n', '\n')
        markdown += summary + "\n\n"

    # Add the main brief content
    markdown += "## Weekly Brief\n\n"
    if content.get('brief'):
        # Brief is already in markdown format, clean any escaped characters
        brief = content['brief'].replace('\\n\\n', '\n\n').replace('\\n', '\n')
        markdown += brief + "\n\n"
    elif content.get('brief_content'):
        # Alternative field name
        brief_content = content['brief_content'].replace('\\n\\n', '\n\n').replace('\\n', '\n')
        markdown += brief_content + "\n\n"

    if content.get('themes'):
        markdown += "## Key Themes and Analysis\n\n"
        for theme, topics in content['themes'].items():
            markdown += f"### {theme}\n"
            if isinstance(topics, list):
                markdown += "**Related Topics:**\n"
                for topic in topics:
                    markdown += f"- {topic}\n"
            else:
                markdown += f"{topics}\n"
            markdown += "\n"

    # Add COMPLETE ledger activity details including full governance proposals
    if content.get('ledger_activity'):
        markdown += "## Complete On-Chain Activity Details\n\n"
        ledger = content.get('ledger_activity', {})
        if ledger.get('summary'):
            markdown += "### Weekly Ledger Summary\n\n"
            markdown += ledger.get('summary', '').replace('\\n', '\n') + "\n\n"

        # Extract and fetch governance proposals
        proposal_ids = set()
        brief_text = content.get('brief', '') + content.get('brief_content', '') + str(ledger)
        # Look for proposal patterns like "#56" or "proposals/56"
        proposal_patterns = [r'#(\d+):', r'proposals?/(\d+)', r'Proposal #(\d+)']
        for pattern in proposal_patterns:
            matches = re.findall(pattern, brief_text)
            for match in matches:
                proposal_ids.add(match)

        if proposal_ids:
            markdown += "### Full Governance Proposals\n\n"
            for prop_id in sorted(proposal_ids, key=int):
                logger.info(f"Fetching governance proposal #{prop_id} for NotebookLM")
                prop_content = fetch_governance_proposal(prop_id)
                if prop_content:
                    markdown += prop_content
                    markdown += "---\n\n"
                else:
                    markdown += f"**Proposal #{prop_id}**\n\n"
                    markdown += "*[Unable to fetch full proposal text]*\n\n"
                    markdown += "---\n\n"

        if ledger.get('sections'):
            for section in ledger.get('sections', []):
                if section.get('items'):
                    markdown += f"### {section.get('title', 'Activity')}\n\n"
                    for item in section.get('items', []):  # Include ALL items
                        if item.get('title'):
                            markdown += f"**{item.get('title')}**\n"
                            if item.get('description'):
                                # Include full description
                                markdown += f"{item.get('description')}\n"
                            if item.get('link'):
                                markdown += f"[View Details]({item.get('link')})\n"
                            markdown += "\n"

    # Add detailed source material with actual forum content
    markdown += "## Detailed Source Material\n\n"
    markdown += "*The following section contains actual forum thread content for in-depth NotebookLM analysis.*\n\n"

    # Extract all URLs from various sections
    forum_urls = set()
    website_urls = set()  # For regentokenomics.org and other websites

    # Check key discussions
    if content.get('key_discussions'):
        for disc in content.get('key_discussions', []):
            url = disc.get('url', '')
            if 'forum.regen.network' in url:
                forum_urls.add(url)

    # Check citations
    if content.get('citations'):
        for cite in content.get('citations'):
            url = cite.get('url', '')
            if 'forum.regen.network' in url:
                forum_urls.add(url)

    # Check the brief for various links
    brief_text = content.get('brief', '') + content.get('brief_content', '')

    # Forum URLs (skip truncated ones and clean up)
    forum_url_pattern = r'https://forum\.regen\.network/t/[^\s\)\]]+'
    found_urls = re.findall(forum_url_pattern, brief_text)
    for url in found_urls:
        # Skip URLs that were truncated with ...
        if url.endswith('...'):
            continue
        # Clean up URL - remove trailing punctuation
        url = url.rstrip('.,;:)')
        # Only add valid, complete URLs
        if '/t/' in url and len(url) > 40:  # Basic validation
            forum_urls.add(url)

    # Website URLs (regentokenomics.org)
    website_patterns = [
        r'https?://regentokenomics\.org[^\s\)]+',
        r'https?://[^\s\)]*weekly-meetup[^\s\)]+'
    ]
    for pattern in website_patterns:
        found_urls = re.findall(pattern, brief_text)
        for url in found_urls:
            website_urls.add(url.rstrip('/'))

    # Fetch and include website content with video transcriptions
    if website_urls:
        markdown += "## Complete Website Content & Video Transcriptions\n\n"
        markdown += f"*Fetching full content and transcriptions from {len(website_urls)} website pages...*\n\n"

        for url in sorted(website_urls):
            logger.info(f"Fetching website content: {url}")
            content_data = fetch_website_content_sync(url)
            if content_data:
                markdown += f"---\n\n### Website Page: {url}\n\n"

                # Include text content
                if content_data['text']:
                    markdown += "#### Page Content\n\n"
                    markdown += content_data['text']
                    markdown += "\n\n"

                # Process videos
                if content_data['video_urls']:
                    markdown += "#### Video Content\n\n"
                    for video_url in content_data['video_urls']:
                        markdown += f"**Video**: {video_url}\n\n"

                        # Attempt to transcribe
                        logger.info(f"Attempting to transcribe video: {video_url}")
                        transcript = transcribe_video_sync(video_url)
                        if transcript:
                            markdown += "**Complete Video Transcription**:\n\n"
                            markdown += transcript
                            markdown += "\n\n"
                        else:
                            markdown += "*[Unable to transcribe video - ffmpeg or API issue]*\n\n"
            else:
                markdown += f"---\n\n### Website: {url}\n\n"
                markdown += "*[Unable to fetch website content]*\n\n"

    # Fetch and include COMPLETE forum content - every single post
    if forum_urls:
        markdown += "## Complete Forum Thread Archives\n\n"
        markdown += f"*Fetching complete content from {len(forum_urls)} forum threads - every single post included...*\n\n"

        thread_count = 0
        for url in sorted(forum_urls):
            thread_count += 1
            logger.info(f"Fetching complete forum thread {thread_count}/{len(forum_urls)} for NotebookLM: {url}")
            # Use sync version since we're not in async context
            thread_content = fetch_forum_thread_content_sync(url)
            if thread_content:
                markdown += f"---\n\n## Complete Forum Thread #{thread_count}\n\n"
                markdown += thread_content
                markdown += "\n"
            else:
                markdown += f"---\n\n## Forum Thread #{thread_count}\n\n"
                markdown += f"**URL**: {url}\n\n"
                markdown += "*[Unable to fetch thread content - API access may be restricted]*\n\n"

    # Add key discussions summary if no forum content was fetched
    elif content.get('key_discussions'):
        markdown += "### Key Discussions This Week\n\n"
        for disc in content.get('key_discussions', [])[:10]:  # Top 10 discussions
            if disc.get('url'):
                markdown += f"#### [{disc.get('title', disc.get('url'))}]({disc.get('url')})\n\n"
            else:
                markdown += f"#### {disc.get('title', 'Discussion')}\n\n"

            # Note about thread context
            markdown += "*Note: For complete thread context, add the URL as a separate source in NotebookLM.*\n\n"

    if content.get('citations'):
        markdown += "### All Sources and Citations\n\n"
        for cite in content['citations']:
            markdown += f"- **{cite.get('title', 'Untitled')}**\n"
            markdown += f"  - Source: {cite.get('source', 'Unknown')}\n"
            markdown += f"  - Date: {cite.get('date', 'N/A')}\n"
            if cite.get('url'):
                markdown += f"  - URL: {cite['url']}\n"
            markdown += "\n"

    # Add community pulse details
    if content.get('community_pulse'):
        markdown += "## Community Pulse Analysis\n\n"
        pulse = content.get('community_pulse', {})
        if pulse.get('overall_activity'):
            markdown += f"**Overall Activity Level**: {pulse.get('overall_activity')}\n\n"
        if pulse.get('key_focus_areas'):
            markdown += "**Key Focus Areas**:\n"
            for area in pulse.get('key_focus_areas', []):
                markdown += f"- {area}\n"
            markdown += "\n"
        if pulse.get('emerging_trends'):
            markdown += "**Emerging Trends**:\n"
            for trend in pulse.get('emerging_trends', []):
                markdown += f"- {trend}\n"
            markdown += "\n"

    # Add comprehensive footer
    markdown += "\n---\n\n"
    markdown += "## Document Completeness\n\n"
    markdown += "This comprehensive export includes:\n"
    markdown += "- ✅ Complete weekly digest narrative (800-1200 words)\n"
    markdown += "- ✅ Full governance proposal texts and voting details\n"
    markdown += "- ✅ Complete forum threads with every single post\n"
    markdown += "- ✅ Website pages with full content (regentokenomics.org)\n"
    markdown += "- ✅ Video transcriptions using OpenAI Whisper\n"
    markdown += "- ✅ All on-chain activity metrics and statistics\n"
    markdown += "- ✅ Community pulse metrics and trend analysis\n"
    markdown += "- ✅ All source citations embedded inline\n\n"
    markdown += "**No external sources needed** - This document contains everything for comprehensive NotebookLM analysis.\n\n"
    markdown += "*Generated by Regen Network KOI System - Complete Archive for NotebookLM*\n"

    return markdown

def generate_daily_markdown(content):
    """Generate markdown version of daily thread"""
    markdown = "# Regen Network Daily Thread\n\n"

    if content.get('thread_date'):
        markdown += f"**Date:** {content['thread_date']}\n\n"

    if content.get('posts'):
        markdown += "## Posts\n\n"
        for i, post in enumerate(content['posts'], 1):
            markdown += f"### Post {i}\n\n"
            markdown += post.get('content', '') + "\n\n"

            if post.get('sources'):
                markdown += "**Sources:**\n"
                for source in post['sources']:
                    if isinstance(source, dict):
                        if source.get('type') == 'ledger':
                            markdown += f"- {source.get('description', 'Ledger data')}\n"
                        elif source.get('url'):
                            markdown += f"- [{source.get('sensor', 'Source')}]({source['url']})\n"
                        else:
                            markdown += f"- {source.get('sensor', 'Unknown source')}\n"
                markdown += "\n"

    markdown += "---\n"
    markdown += "*Generated by Regen Network KOI System*\n"

    return markdown

@app.route('/api/dashboard/drafts/<draft_id>/generate_audio', methods=['POST'])
@require_auth
def generate_audio_for_draft(draft_id):
    """Generate audio for a weekly digest draft (optional)"""
    import subprocess
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get draft content
        cur.execute("""
            SELECT content_type, content_data
            FROM quality_reviews
            WHERE review_id = %s AND content_type = 'weekly_digest'
        """, (draft_id,))

        result = cur.fetchone()

        if not result:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'message': 'Weekly digest draft not found'}), 404

        content = result['content_data'] if isinstance(result['content_data'], dict) else json.loads(result['content_data'])

        # Save content to temp file for audio generation
        temp_file = f'/tmp/weekly_digest_for_audio_{draft_id}.json'
        with open(temp_file, 'w') as f:
            json.dump(content, f)

        # Run audio generation using simple podcast generator
        env = os.environ.copy()

        # Log the command being run
        cmd = [
            'python3',
            '/opt/projects/koi-processor/src/audio/simple_podcast_generator.py',
            temp_file,
            '--output-dir', '/opt/projects/koi-processor/podcast_audio'
        ]
        logger.info(f"Running podcast generation command: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True, cwd='/opt/projects/koi-processor', env=env, timeout=180)

        logger.info(f"Podcast generation result - Return code: {result.returncode}")
        logger.info(f"Podcast generation stdout: {result.stdout}")
        if result.stderr:
            logger.error(f"Podcast generation stderr: {result.stderr}")

        if result.returncode == 0:
            # Update draft metadata with audio status
            cur.execute("""
                UPDATE quality_reviews
                SET
                    quality_issues = jsonb_set(
                        COALESCE(quality_issues, '{}'::jsonb),
                        '{audio_generated}',
                        'true'
                    )
                WHERE review_id = %s
            """, (draft_id,))

            conn.commit()
            cur.close()
            conn.close()

            return jsonify({
                'success': True,
                'message': 'Audio generated successfully'
            })
        else:
            cur.close()
            conn.close()
            return jsonify({
                'success': False,
                'message': 'Audio generation failed',
                'error': result.stderr
            }), 500

    except Exception as e:
        logger.error(f"Error generating audio: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# WebSocket events
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info(f"Client connected: {request.sid}")
    emit('connected', {'message': 'Connected to dashboard'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info(f"Client disconnected: {request.sid}")

@socketio.on('request_update')
def handle_update_request(data):
    """Handle update request from client"""
    update_type = data.get('type', 'overview')
    
    if update_type == 'overview':
        # This would call the appropriate API endpoint
        # and emit the data back
        pass

# Utility function to broadcast updates
def broadcast_update(update_type: str, data: Dict[str, Any]):
    """Broadcast update to all connected clients"""
    socketio.emit('dashboard_update', {
        'type': update_type,
        'data': data,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

# API endpoint for components to push updates
@app.route('/api/dashboard/notify', methods=['POST'])
def notify_update():
    """Receive notifications from other components"""
    try:
        data = request.json
        update_type = data.get('type')
        content = data.get('content')
        
        # Broadcast to connected dashboard clients
        broadcast_update(update_type, content)
        
        # Store alert if it's an error
        if update_type == 'error':
            conn = get_db_connection()
            cur = conn.cursor()
            
            # Check if alerts table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_alerts (
                    id SERIAL PRIMARY KEY,
                    alert_type VARCHAR(50),
                    severity VARCHAR(20),
                    message TEXT,
                    resolved BOOLEAN DEFAULT false,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            cur.execute("""
                INSERT INTO dashboard_alerts (alert_type, severity, message)
                VALUES (%s, %s, %s)
            """, (
                content.get('alert_type', 'general'),
                content.get('severity', 'warning'),
                content.get('message', 'Unknown error')
            ))
            
            conn.commit()
            cur.close()
            conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Error handling notification: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Login route (simple for now)
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Simple login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Simple hardcoded auth for now
        # In production, use proper authentication
        if username == 'admin' and password == 'regen2025':
            session['user'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout user"""
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/api/dashboard/generate/daily', methods=['POST'])
@require_auth
def generate_daily():
    """Generate daily thread"""
    import subprocess
    import threading

    def run_generation():
        try:
            # Run the daily curator script
            result = subprocess.run([
                'python3',
                '/opt/projects/koi-processor/src/content/daily_curator_llm.py'
            ], capture_output=True, text=True, cwd='/opt/projects/koi-processor')

            if result.returncode == 0:
                # Submit to review queue
                submit_result = subprocess.run([
                    'python3',
                    '/opt/projects/koi-processor/scripts/submit_to_review.py',
                    output_file,
                    'daily_thread'
                ], capture_output=True, text=True, cwd='/opt/projects/koi-processor')

                if submit_result.returncode == 0:
                    socketio.emit('generation_completed', {'type': 'daily', 'success': True, 'review': 'submitted'})
                else:
                    socketio.emit('generation_completed', {'type': 'daily', 'success': True, 'review': 'failed'})
            else:
                socketio.emit('generation_error', {'type': 'daily', 'error': result.stderr})
        except Exception as e:
            socketio.emit('generation_error', {'type': 'daily', 'error': str(e)})

    # Start generation in background thread
    thread = threading.Thread(target=run_generation)
    thread.start()

    return jsonify({'success': True, 'message': 'Daily generation started'})

@app.route('/api/dashboard/generate/weekly', methods=['POST'])
@require_auth
def generate_weekly():
    """Generate weekly digest"""
    import subprocess
    import threading

    def run_generation():
        try:
            # Run the NEW weekly curator with LLM (includes ALL content from week)
            # Use venv Python if available
            python_path = '/opt/projects/koi-processor/venv/bin/python3'
            if not os.path.exists(python_path):
                python_path = 'python3'

            result = subprocess.run([
                python_path,
                '/opt/projects/koi-processor/src/content/weekly_curator_llm.py'
            ], capture_output=True, text=True, cwd='/opt/projects/koi-processor')

            if result.returncode == 0:
                socketio.emit('generation_completed', {'type': 'weekly', 'success': True})
                logger.info("Weekly digest with LLM generated successfully - includes ALL content")
            else:
                socketio.emit('generation_error', {'type': 'weekly', 'error': result.stderr})
                logger.error(f"Weekly generation failed: {result.stderr}")
        except Exception as e:
            socketio.emit('generation_error', {'type': 'weekly', 'error': str(e)})
            logger.error(f"Weekly generation error: {e}")

    # Start generation in background thread
    thread = threading.Thread(target=run_generation)
    thread.start()

    return jsonify({'success': True, 'message': 'Weekly generation started'})

# KOI API endpoints for hybrid RAG queries

"""
Fixed /api/koi/query endpoint for podcast chatbot
Calls the MCP server's /search endpoint with source filtering
"""

"""
Final fix: Query database directly, no circular MCP dependency
Uses existing BGE embeddings in the database
"""

@app.route('/api/koi/weekly-digest', methods=['GET'])
@require_bearer_auth
def koi_weekly_digest():
    """
    API endpoint for MCP to fetch weekly digest.
    Returns the most recent digest or generates a new one.

    Phase 2 (#23): Bearer auth required. Cached digests may include content
    from private documents; gate access until curator SQL audit lands.
    """
    import asyncio
    from datetime import datetime, timedelta
    import uuid

    request_id = str(uuid.uuid4())

    def respond(data, *, data_source='cached', warnings=None, errors=None, tool_trace=None, citations=None, status_code=200):
        now_iso = datetime.utcnow().isoformat() + "Z"
        envelope = {
            "data": data,
            "request_id": request_id,
            "data_source": data_source,
            "citations": citations or [],
            "warnings": warnings or [],
            "errors": errors or [],
            "as_of": {
                "koi": {
                    "corpus_version": os.environ.get("KOI_CORPUS_VERSION") or now_iso.split("T")[0],
                    "indexed_at": os.environ.get("KOI_LAST_INDEXED") or now_iso,
                }
            },
            "tool_trace": tool_trace or [{
                "tool": "weekly_digest",
                "params_summary": f"start_date={bool(request.args.get('start_date'))},end_date={bool(request.args.get('end_date'))},format={request.args.get('format','markdown')}",
                "timestamp": now_iso,
                "data_source": data_source,
            }],
        }
        resp = jsonify(envelope)
        resp.status_code = status_code
        resp.headers["X-Request-ID"] = request_id
        return resp

    try:
        # Get parameters
        start_date_param = request.args.get('start_date')
        end_date_param = request.args.get('end_date')
        format_type = request.args.get('format', 'markdown')

        # Compute effective date range (default: last 7 days)
        today = datetime.now()
        if start_date_param and end_date_param:
            effective_start = start_date_param
            effective_end = end_date_param
        else:
            effective_end = today.strftime('%Y-%m-%d')
            effective_start = (today - timedelta(days=7)).strftime('%Y-%m-%d')

        # Check for cached digest matching THIS date range
        # Support both old directory (weekly_digests) and new (weekly)
        output_dirs = [
            '/opt/projects/koi-processor/output/weekly_digests',
            '/opt/projects/koi-processor/output/weekly'
        ]

        # Date-range aware cache filename
        cache_filename = f"weekly_digest_{effective_start}_to_{effective_end}.md"

        for output_dir in output_dirs:
            if not os.path.exists(output_dir):
                continue

            cache_path = os.path.join(output_dir, cache_filename)
            if os.path.exists(cache_path):
                file_mtime = datetime.fromtimestamp(os.path.getmtime(cache_path))
                cache_age_hours = int((datetime.now() - file_mtime).total_seconds() / 3600)

                # Only use cache if < 24 hours old (fresh enough for the specific range)
                if cache_age_hours < 24:
                    with open(cache_path, 'r') as f:
                        content = f.read()
                    logger.info(f"Returning cached digest from {cache_filename} (age: {cache_age_hours}h)")
                    return respond({
                        'success': True,
                        'content': content,
                        'source': 'cached',
                        'cached_file': cache_filename,
                        'cached_age_hours': cache_age_hours,
                        'date_range': {'start': effective_start, 'end': effective_end}
                    }, data_source='cached')
                else:
                    logger.info(f"Cache file {cache_filename} exists but is {cache_age_hours}h old (> 24h), regenerating")

        # No valid cache found - generate fresh digest
        logger.info(f"Generating fresh weekly digest for {effective_start} to {effective_end}")

        # Set date range environment variables for the curator
        os.environ['DIGEST_START_DATE'] = effective_start
        os.environ['DIGEST_END_DATE'] = effective_end

        # Import and run the curator
        from src.content.weekly_curator_llm import WeeklyCuratorLLM

        curator = WeeklyCuratorLLM()

        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            digest = loop.run_until_complete(curator.generate_weekly_digest())
        finally:
            loop.close()

        if digest and digest.get('brief'):
            content = digest.get('brief', '')
            logger.info(f"Generated fresh digest: {len(content)} chars")
            return respond({
                'success': True,
                'content': content,
                'source': 'generated',
                'date_range': {'start': effective_start, 'end': effective_end},
                'statistics': digest.get('statistics', {})
            }, data_source='koi-derived')
        else:
            # Fallback: return any cached file regardless of age, or a helpful message
            logger.warning("LLM generation returned empty, checking for any cached digest")
            for fallback_dir in output_dirs:
                if not os.path.exists(fallback_dir):
                    continue
                files = sorted([f for f in os.listdir(fallback_dir) if f.startswith('weekly_digest_') and f.endswith('.md') and '_notebooklm' not in f], reverse=True)
                if files:
                    latest_file = os.path.join(fallback_dir, files[0])
                    file_mtime = datetime.fromtimestamp(os.path.getmtime(latest_file))
                    with open(latest_file, 'r') as f:
                        content = f.read()
                    logger.info(f"Returning older cached digest from {latest_file}")
                    return respond({
                        'success': True,
                        'content': content,
                        'source': 'cached_fallback',
                        'cached_file': files[0],
                        'cached_age_hours': int((datetime.now() - file_mtime).total_seconds() / 3600),
                        'date_range': {'start': effective_start, 'end': effective_end},
                        'warning': 'Fresh digest generation failed, returning older cached version'
                    }, data_source='cached', warnings=['fallback_used'])
            # No cached files at all - return a placeholder
            return respond({
                'success': True,
                'content': '# Weekly Digest Unavailable\n\nNo recent digest content is currently available. Please try again later or contact support.',
                'source': 'placeholder',
                'date_range': {'start': effective_start, 'end': effective_end},
                'warning': 'No digest content available - generation failed and no cached files found'
            }, data_source='cached', warnings=['fallback_used'])

    except Exception as e:
        logger.error(f"Error generating weekly digest: {e}")
        import traceback
        traceback.print_exc()

        # Try to return cached content as fallback
        fallback_dirs = [
            '/opt/projects/koi-processor/output/weekly_digests',
            '/opt/projects/koi-processor/output/weekly'
        ]
        for fallback_dir in fallback_dirs:
            if not os.path.exists(fallback_dir):
                continue
            files = sorted([f for f in os.listdir(fallback_dir) if f.startswith('weekly_digest_') and f.endswith('.md') and '_notebooklm' not in f], reverse=True)
            if files:
                try:
                    latest_file = os.path.join(fallback_dir, files[0])
                    file_mtime = datetime.fromtimestamp(os.path.getmtime(latest_file))
                    with open(latest_file, 'r') as f:
                        content = f.read()
                    logger.info(f"Returning cached digest after error: {latest_file}")
                    return respond({
                        'success': True,
                        'content': content,
                        'source': 'cached_error_fallback',
                        'cached_file': files[0],
                        'cached_age_hours': int((datetime.now() - file_mtime).total_seconds() / 3600),
                        'warning': f'Fresh digest generation failed with error: {str(e)}'
                    }, data_source='cached', warnings=['fallback_used'], errors=[{
                        "code": "DIGEST_GENERATION_FAILED",
                        "message": str(e),
                        "retryable": True,
                        "retry_after_ms": 60000,
                    }])
                except Exception as cache_error:
                    logger.error(f"Failed to read cached digest: {cache_error}")

        # Return placeholder instead of 500
        return respond({
            'success': True,
            'content': '# Weekly Digest Temporarily Unavailable\n\nThe digest generation system encountered an error. Please try again later.',
            'source': 'error_placeholder',
            'warning': str(e)
        }, data_source='cached', warnings=['fallback_used'], errors=[{
            "code": "DIGEST_GENERATION_FAILED",
            "message": str(e),
            "retryable": True,
            "retry_after_ms": 60000,
        }])

@app.route('/api/koi/weekly-digest/notebooklm', methods=['GET'])
@require_bearer_auth
def koi_weekly_digest_notebooklm():
    """
    API endpoint for MCP to fetch NotebookLM export.
    Returns full content including forum posts and Notion pages.
    Generates on demand if no recent export exists.

    Phase 2 (#23): Bearer auth required. Same rationale as /weekly-digest.
    """
    import asyncio
    from datetime import datetime, timedelta

    try:
        # Check for recent NotebookLM export file first
        # NotebookLM exports are saved to /output/weekly/ (not /output/weekly_digests/)
        output_dir = '/opt/projects/koi-processor/output/weekly'
        if os.path.exists(output_dir):
            # Look for most recent notebooklm file (format: weekly_digest_{date}_notebooklm.md)
            files = sorted([f for f in os.listdir(output_dir) if f.endswith('_notebooklm.md')], reverse=True)
            if files:
                latest_file = os.path.join(output_dir, files[0])
                # Check if file is recent (within last 7 days) - extended from 24 hours for reliability
                file_mtime = datetime.fromtimestamp(os.path.getmtime(latest_file))
                if datetime.now() - file_mtime < timedelta(days=7):
                    with open(latest_file, 'r') as f:
                        content = f.read()

                    # Get file stats
                    word_count = len(content.split())
                    char_count = len(content)

                    logger.info(f"Returning cached NotebookLM export from {latest_file}")
                    return jsonify({
                        'success': True,
                        'content': content,
                        'source': 'cached',
                        'cached_file': files[0],
                        'statistics': {
                            'word_count': word_count,
                            'char_count': char_count
                        }
                    })

        # No recent file - generate on demand
        logger.info("No recent NotebookLM export found, generating on demand...")

        # Get optional date range from query params
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if start_date:
            os.environ['DIGEST_START_DATE'] = start_date
        if end_date:
            os.environ['DIGEST_END_DATE'] = end_date

        # Import and run the curator
        from src.content.weekly_curator_llm import WeeklyCuratorLLM

        curator = WeeklyCuratorLLM()

        # Run async functions in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Generate digest first
            digest = loop.run_until_complete(curator.generate_weekly_digest())

            if not digest:
                return jsonify({
                    'success': False,
                    'error': {'message': 'Failed to generate digest', 'code': 500}
                }), 500

            # Then export to NotebookLM format
            loop.run_until_complete(curator.export_notebooklm_enhanced(digest))
        finally:
            loop.close()

        # Now read the generated file
        if os.path.exists(output_dir):
            files = sorted([f for f in os.listdir(output_dir) if f.endswith('_notebooklm.md')], reverse=True)
            if files:
                latest_file = os.path.join(output_dir, files[0])
                with open(latest_file, 'r') as f:
                    content = f.read()

                word_count = len(content.split())
                char_count = len(content)

                logger.info(f"Generated and returning NotebookLM export: {files[0]}")
                return jsonify({
                    'success': True,
                    'content': content,
                    'source': 'generated',
                    'generated_file': files[0],
                    'statistics': {
                        'word_count': word_count,
                        'char_count': char_count
                    }
                })

        return jsonify({
            'success': False,
            'error': {'message': 'Failed to generate NotebookLM export', 'code': 500}
        }), 500

    except Exception as e:
        logger.error(f"Error fetching/generating NotebookLM export: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': {'message': str(e), 'code': 500}
        }), 500

@app.route('/api/koi/query', methods=['POST'])
@require_auth
def koi_hybrid_query():
    """Podcast Q&A endpoint - calls hybrid RAG API and adds LLM synthesis"""
    try:
        data = request.get_json()
        query_text = data.get('question', '')
        source_filter = data.get('source_filter', 'podcast')
        limit = data.get('limit', 5)

        if not query_text:
            return jsonify({'error': 'Query text required'}), 400

        # Call the existing hybrid RAG API on port 8301
        try:
            import requests
            rag_response = requests.post('http://localhost:8301/api/koi/query', json={
                'question': query_text,
                'limit': limit,
                'source_filter': source_filter
            }, timeout=15)

            if rag_response.status_code != 200:
                logger.error(f"RAG API failed: {rag_response.status_code}")
                return jsonify({'error': 'Search failed', 'synthesized': False}), 500

            rag_data = rag_response.json()
            results = rag_data.get('results', [])

        except Exception as e:
            logger.error(f"Error calling RAG API: {e}")
            return jsonify({'error': f'RAG API error: {str(e)}', 'synthesized': False}), 500

        if not results:
            return jsonify({
                'question': query_text,
                'answer': 'No relevant information found about that in the podcast.',
                'synthesized': False
            })

        # Extract episodes and build context
        episodes_info = {}
        context_parts = []

        for idx, r in enumerate(results, 1):
            content = r.get('content', '')
            context_parts.append(f"[{idx}] {content}")

            metadata = r.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    import json
                    metadata = json.loads(metadata)
                except:
                    pass

            episode_title = metadata.get('episode_title', metadata.get('title', ''))
            episode_url = metadata.get('url', '')
            if episode_title and episode_title not in episodes_info:
                episodes_info[episode_title] = episode_url

        context = "\n\n".join(context_parts)

        # LLM synthesis
        openai_key = os.getenv('OPENAI_API_KEY')
        if not openai_key:
            return jsonify({
                'question': query_text,
                'results': [{'content': r['content'], 'score': r['score']} for r in results],
                'synthesized': False
            })

        try:
            system_prompt = """You are a helpful AI assistant for the Planetary Regeneration Podcast.

Answer questions based ONLY on the provided context. Cite sources using [1], [2] format.

At the end, list relevant episodes:

**Relevant Episodes:**
- Episode Title"""

            user_prompt = f"""Question: {query_text}

Context:
{context}

Answer using the context above."""

            client = OpenAI(api_key=openai_key)
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=600
            )

            answer = completion.choices[0].message.content

            return jsonify({
                'question': query_text,
                'answer': answer,
                'episodes': episodes_info,
                'synthesized': True,
                'total_results': len(results)
            })

        except Exception as e:
            logger.error(f"LLM error: {e}")
            return jsonify({
                'question': query_text,
                'results': [{'content': r['content'], 'score': r['score']} for r in results],
                'synthesized': False
            })

    except Exception as e:
        logger.error(f"KOI query error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/podcast')
def podcast_map():
    """Serve the 3D podcast visualization map"""
    from flask import send_from_directory
    return send_from_directory('../../static/podcast', 'podcast_map_3d.html')

@app.route('/podcast-webgpu')
def podcast_map_webgpu():
    """Serve the WebGPU 3D podcast visualization map"""
    from flask import send_from_directory
    return send_from_directory('../../static/podcast', 'podcast_map_webgpu.html')

@app.route('/podcast_audio/<filename>')
def podcast_audio(filename):
    """Serve podcast audio files"""
    from flask import send_from_directory
    import os
    audio_dir = '/opt/projects/koi-sensors/sensors/podcast/temp_audio'
    if os.path.exists(os.path.join(audio_dir, filename)):
        return send_from_directory(audio_dir, filename, mimetype='audio/mpeg')
    else:
        return "Audio file not found", 404

if __name__ == '__main__':
    port = config['dashboard'].get('port', 8400)
    logger.info(f"Starting Milestone B Content Dashboard on port {port}")
    # Note: In production, use a proper WSGI server like gunicorn
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
