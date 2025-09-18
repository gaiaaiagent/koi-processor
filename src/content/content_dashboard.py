#!/usr/bin/env python3
"""
Milestone B Content Operations Dashboard
Web-based monitoring interface for Daily Bot and Weekly Digest operations
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import psycopg2
from psycopg2.extras import RealDictCursor
import os
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

# Initialize Flask app
app = Flask(__name__, 
            template_folder='../../templates',
            static_folder='../../static')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
CORS(app)
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
        
        # Check KOI pipeline status
        cur.execute("""
            SELECT COUNT(*) as recent_events
            FROM koi_memories
            WHERE created_at > NOW() - INTERVAL '1 hour'
              AND rid NOT LIKE '%heartbeat%'
        """)
        koi_activity = cur.fetchone()
        
        cur.close()
        conn.close()
        
        # Determine overall health
        health_status = 'healthy'
        if daily_stats['avg_style_score'] and daily_stats['avg_style_score'] < config['thresholds']['daily_bot']['style_score_warning']:
            health_status = 'warning'
        if not db_check or (koi_activity and koi_activity['recent_events'] == 0):
            health_status = 'error'
        
        return jsonify({
            'success': True,
            'health': health_status,
            'daily_stats': daily_stats,
            'weekly_stats': weekly_stats,
            'pending_reviews': pending['pending_count'] if pending else 0,
            'koi_pipeline_active': koi_activity['recent_events'] > 0 if koi_activity else False,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting overview: {e}")
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
            metadata = current_digest['metadata'] if isinstance(current_digest['metadata'], dict) else json.loads(current_digest.get('metadata', '{}'))
            word_count = metadata.get('word_count', 0)
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
            env['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY', '')
            env['GENERATE_AS_DRAFT'] = 'true' if draft_mode else 'false'
            env['INCLUDE_PROVENANCE'] = 'true'  # Always include source provenance

            # The script will automatically create drafts based on the environment variables
            result = subprocess.run([
                'python3',
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
            output_file = f'/tmp/weekly_digest_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

            # Set environment variables
            env = os.environ.copy()
            env['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY', '')
            env['GENERATE_AS_DRAFT'] = 'true' if draft_mode else 'false'
            env['INCLUDE_PROVENANCE'] = 'true'
            env['SKIP_AUDIO_GENERATION'] = 'true' if skip_audio else 'false'

            # The script will create drafts based on environment variables
            result = subprocess.run([
                'python3',
                '/opt/projects/koi-processor/scripts/run_daily_curator.py',
                'weekly',
                '--output', output_file
            ], capture_output=True, text=True, cwd='/opt/projects/koi-processor', env=env, timeout=180)

            if result.returncode == 0 and os.path.exists(output_file):
                # Submit to review queue
                submit_result = subprocess.run([
                    'python3',
                    '/opt/projects/koi-processor/scripts/submit_to_review.py',
                    output_file,
                    'weekly_digest'
                ], capture_output=True, text=True, cwd='/opt/projects/koi-processor', timeout=30)

                if submit_result.returncode == 0:
                    # Broadcast update
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
                    return jsonify({
                        'success': False,
                        'message': 'Generated but failed to submit for review'
                    }), 500
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to generate weekly digest',
                    'error': result.stderr if result.stderr else 'Unknown error'
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

        # Run audio generation
        env = os.environ.copy()
        result = subprocess.run([
            'python3',
            '/opt/projects/koi-processor/src/audio/podcast_generator.py',
            temp_file,
            '--output-dir', '/opt/projects/koi-processor/podcast_audio'
        ], capture_output=True, text=True, cwd='/opt/projects/koi-processor', timeout=180)

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
            output_file = f'/tmp/daily_thread_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            result = subprocess.run([
                'python3',
                '/opt/projects/koi-processor/scripts/run_daily_curator.py',
                'daily',
                '--output', output_file
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
            # Run the weekly aggregator script
            result = subprocess.run([
                'python3',
                '/opt/projects/koi-processor/scripts/run_weekly_aggregator.py',
                '--output', '/tmp/'
            ], capture_output=True, text=True, cwd='/opt/projects/koi-processor')

            if result.returncode == 0:
                socketio.emit('generation_completed', {'type': 'weekly', 'success': True})
            else:
                socketio.emit('generation_error', {'type': 'weekly', 'error': result.stderr})
        except Exception as e:
            socketio.emit('generation_error', {'type': 'weekly', 'error': str(e)})

    # Start generation in background thread
    thread = threading.Thread(target=run_generation)
    thread.start()

    return jsonify({'success': True, 'message': 'Weekly generation started'})

if __name__ == '__main__':
    port = config['dashboard'].get('port', 8400)
    logger.info(f"Starting Milestone B Content Dashboard on port {port}")
    # Note: In production, use a proper WSGI server like gunicorn
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)