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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import asyncio
from functools import wraps
from loguru import logger

# Initialize Flask app
app = Flask(__name__)
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
                   AVG(CAST(metadata->>'style_score' AS FLOAT)) as avg_style_score,
                   COUNT(CASE WHEN status = 'published' THEN 1 END) as published_count
            FROM content_reviews
            WHERE content_type = 'daily_thread'
            AND created_at > NOW() - INTERVAL '7 days'
        """)
        daily_stats = cur.fetchone() or {'total_daily_posts': 0, 'avg_style_score': 0, 'published_count': 0}
        
        # Get recent weekly digest activity
        cur.execute("""
            SELECT COUNT(*) as total_weekly_digests,
                   AVG(CAST(metadata->>'word_count' AS INT)) as avg_word_count
            FROM content_reviews
            WHERE content_type = 'weekly_digest'
            AND created_at > NOW() - INTERVAL '30 days'
        """)
        weekly_stats = cur.fetchone() or {'total_weekly_digests': 0, 'avg_word_count': 0}
        
        # Get pending reviews
        cur.execute("""
            SELECT COUNT(*) as pending_count
            FROM content_reviews
            WHERE status IN ('draft', 'pending_review')
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
        
        # Get today's draft
        cur.execute("""
            SELECT id, status, content, metadata, created_at, updated_at
            FROM content_reviews
            WHERE content_type = 'daily_thread'
            AND DATE(created_at) = CURRENT_DATE
            ORDER BY created_at DESC
            LIMIT 1
        """)
        today_draft = cur.fetchone()
        
        # Get last 7 days performance
        cur.execute("""
            SELECT DATE(created_at) as date,
                   COUNT(*) as posts_count,
                   AVG(CAST(metadata->>'style_score' AS FLOAT)) as avg_style,
                   COUNT(CASE WHEN status = 'published' THEN 1 END) as published
            FROM content_reviews
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
    """Get current draft threads"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get recent drafts
        cur.execute("""
            SELECT id, status, content, metadata, created_at, 
                   reviewer, review_notes
            FROM content_reviews
            WHERE content_type = 'daily_thread'
            AND status IN ('draft', 'pending_review')
            ORDER BY created_at DESC
            LIMIT 10
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
        
        # Get current week's digest
        cur.execute("""
            SELECT id, status, content, metadata, created_at
            FROM content_reviews
            WHERE content_type = 'weekly_digest'
            AND created_at > date_trunc('week', CURRENT_DATE)
            ORDER BY created_at DESC
            LIMIT 1
        """)
        current_digest = cur.fetchone()
        
        # Get content collection progress
        cur.execute("""
            SELECT COUNT(*) as total_content,
                   COUNT(DISTINCT content->>'source_type') as unique_sources
            FROM koi_memories
            WHERE created_at > date_trunc('week', CURRENT_DATE)
        """)
        collection_progress = cur.fetchone()
        
        # Get last 4 weeks history
        cur.execute("""
            SELECT DATE(created_at) as week_date,
                   status,
                   CAST(metadata->>'word_count' AS INT) as word_count,
                   CAST(metadata->>'source_count' AS INT) as source_count
            FROM content_reviews
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
            SELECT id, content_type, status, 
                   CAST(metadata->>'style_score' AS FLOAT) as style_score,
                   CAST(metadata->>'validation_passed' AS BOOLEAN) as validation_passed,
                   created_at
            FROM content_reviews
            WHERE status IN ('draft', 'pending_review')
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
            SELECT id, content_type, status, reviewer, 
                   review_notes, approved_at, created_at
            FROM content_reviews
            WHERE status IN ('approved', 'rejected', 'published', 'rolled_back')
            ORDER BY COALESCE(approved_at, created_at) DESC
            LIMIT 50
        """)
        history = cur.fetchall()
        
        # Get approval statistics
        cur.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved_count,
                COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected_count,
                COUNT(CASE WHEN status = 'published' THEN 1 END) as published_count,
                AVG(CAST(metadata->>'style_score' AS FLOAT)) as avg_style_score
            FROM content_reviews
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

if __name__ == '__main__':
    port = config['dashboard'].get('port', 8400)
    logger.info(f"Starting Milestone B Content Dashboard on port {port}")
    # Note: In production, use a proper WSGI server like gunicorn
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)