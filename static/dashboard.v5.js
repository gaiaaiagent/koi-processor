// Milestone B Content Operations Dashboard JavaScript
// Real-time updates and interactive features

// Global variables
let socket = null;
let charts = {};
let refreshInterval = null;

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', function() {
    // Disable websocket to prevent refresh issues
    // initializeSocket();
    initializeCharts();
    loadDashboardData();
    // Disable auto-refresh to prevent navigation issues
    // startAutoRefresh();
});

// Initialize WebSocket connection
function initializeSocket() {
    try {
        // Check if io is defined (socket.io library loaded)
        if (typeof io === 'undefined') {
            console.log('Socket.io not available, running in offline mode');
            return;
        }

        // Determine base path for socket connection
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        socket = io(basePath ? {path: basePath + '/socket.io/'} : {});

        socket.on('connect', function() {
            console.log('Connected to dashboard server');
            updateConnectionStatus(true);
        });

        socket.on('disconnect', function() {
            console.log('Disconnected from dashboard server');
            updateConnectionStatus(false);
        });

        socket.on('dashboard_update', function(data) {
            handleDashboardUpdate(data);
        });

        socket.on('connect_error', function(error) {
            console.log('Socket connection error:', error.message);
        });
    } catch (error) {
        console.log('Socket initialization skipped:', error.message);
    }
}

// Initialize Chart.js charts
function initializeCharts() {
    // Daily Performance Chart
    const dailyCtx = document.getElementById('dailyPerformanceChart');
    if (dailyCtx) {
        charts.dailyPerformance = new Chart(dailyCtx.getContext('2d'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Style Score',
                    data: [],
                    borderColor: 'rgb(102, 126, 234)',
                    backgroundColor: 'rgba(102, 126, 234, 0.1)',
                    tension: 0.3
                }, {
                    label: 'Published',
                    data: [],
                    borderColor: 'rgb(40, 167, 69)',
                    backgroundColor: 'rgba(40, 167, 69, 0.1)',
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 1
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'bottom'
                    }
                }
            }
        });
    }
    
    // Quality Metrics Chart
    const qualityCtx = document.getElementById('qualityChart');
    if (qualityCtx) {
        charts.quality = new Chart(qualityCtx.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: ['Approved', 'Rejected', 'Pending'],
                datasets: [{
                    data: [0, 0, 0],
                    backgroundColor: [
                        'rgba(40, 167, 69, 0.8)',
                        'rgba(220, 53, 69, 0.8)',
                        'rgba(255, 193, 7, 0.8)'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }
}

// Load dashboard data from API
async function loadDashboardData() {
    try {
        // Load overview
        const overview = await fetchAPI('/api/dashboard/overview');
        updateOverview(overview);
        
        // Load daily stats
        const dailyStats = await fetchAPI('/api/dashboard/daily/stats');
        updateDailySection(dailyStats);
        
        // Load weekly stats
        const weeklyStats = await fetchAPI('/api/dashboard/weekly/stats');
        updateWeeklySection(weeklyStats);
        
        // Load quality metrics
        const qualityData = await fetchAPI('/api/dashboard/quality/history');
        updateQualitySection(qualityData);
        
        // Load schedule
        const schedule = await fetchAPI('/api/dashboard/schedule');
        updateSchedule(schedule);
        
        // Load alerts
        const alerts = await fetchAPI('/api/dashboard/errors');
        updateAlerts(alerts);
        
    } catch (error) {
        console.error('Error loading dashboard data:', error);
        showNotification('Error loading dashboard data', 'error');
    }
}

// Fetch data from API
async function fetchAPI(endpoint) {
    // Determine base path based on current location
    const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';

    // Build absolute URL without credentials
    const baseUrl = `${window.location.protocol}//${window.location.host}`;
    const url = `${baseUrl}${basePath}${endpoint}`;

    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
}

// Update overview section
function updateOverview(data) {
    if (!data.success) return;
    
    // Update health status
    const healthStatus = document.getElementById('health-status');
    if (healthStatus) {
        healthStatus.textContent = data.health.charAt(0).toUpperCase() + data.health.slice(1);
        healthStatus.className = `status-badge status-${data.health}`;
    }
    
    // Update pending count
    const pendingCount = document.getElementById('pending-count');
    if (pendingCount) {
        pendingCount.textContent = data.pending_reviews || 0;
    }
    
    // Update daily posts count
    const dailyPostsCount = document.getElementById('daily-posts-count');
    if (dailyPostsCount && data.daily_stats) {
        dailyPostsCount.textContent = data.daily_stats.total_daily_posts || 0;
    }
    
    // Update KOI status
    const koiStatus = document.getElementById('koi-status');
    if (koiStatus) {
        const isActive = data.koi_pipeline_active;
        koiStatus.textContent = isActive ? 'Active' : 'Inactive';
        koiStatus.className = `status-badge status-${isActive ? 'healthy' : 'error'}`;
    }
}

// Update daily bot section
function updateDailySection(data) {
    if (!data.success) return;

    // Update today's draft - check if elements exist before using them
    const draftContent = document.getElementById('daily-draft-content');
    const dailyActions = document.getElementById('daily-actions');

    if (data.today && data.today.draft) {
        const draft = data.today.draft;
        const content = typeof draft.content === 'string' ? JSON.parse(draft.content) : draft.content;

        if (content.posts && content.posts.length > 0 && draftContent) {
            let html = '<div class="mb-3">';
            html += `<span class="badge bg-info me-2">Status: ${draft.status}</span>`;
            if (draft.metadata && draft.metadata.style_score) {
                html += `<span class="badge bg-secondary">Style Score: ${(draft.metadata.style_score * 100).toFixed(0)}%</span>`;
            }
            html += '</div>';

            content.posts.forEach((post, index) => {
                html += `
                    <div class="content-preview">
                        <h6>Post ${index + 1}</h6>
                        <p>${escapeHtml(post.content || post)}</p>
                    </div>
                `;
            });

            draftContent.innerHTML = html;
            if (dailyActions) {
                dailyActions.style.display = draft.status === 'pending_review' ? 'block' : 'none';
            }
        }
    }
    
    // Update performance chart
    if (data.weekly_performance && charts.dailyPerformance) {
        const labels = data.weekly_performance.map(d => formatDate(d.date));
        const styleScores = data.weekly_performance.map(d => d.avg_style || 0);
        const published = data.weekly_performance.map(d => d.published ? 1 : 0);
        
        charts.dailyPerformance.data.labels = labels;
        charts.dailyPerformance.data.datasets[0].data = styleScores;
        charts.dailyPerformance.data.datasets[1].data = published;
        charts.dailyPerformance.update();
    }
    
    // Update sources list
    const sourcesList = document.getElementById('daily-sources');
    if (data.today && data.today.sources && sourcesList) {
        const sources = data.today.sources;
        let html = '';
        sources.forEach(source => {
            html += `<li><i class="bi bi-check-circle text-success"></i> ${source}</li>`;
        });
        sourcesList.innerHTML = html || '<li class="text-muted">No sources yet</li>';
    }
}

// Update weekly digest section
async function updateWeeklySection(data) {
    // Store weekly drafts from database
    if (data && data.all_digests) {
        currentWeeklyDrafts = data.all_digests;
    }

    // Display drafts from database
    const weeklyDraftsList = document.getElementById('weekly-drafts-list');
    if (weeklyDraftsList && currentWeeklyDrafts.length > 0) {
        let html = '<div class="list-group">';

        currentWeeklyDrafts.forEach((draft, index) => {
            // Parse the content data
            let content = draft.content;
            if (typeof content === 'string') {
                try {
                    content = JSON.parse(content);
                } catch (e) {
                    console.error('Error parsing weekly draft content:', e);
                }
            }

            const createdDate = new Date(draft.created_at);
            const weekStart = content.week_start ? new Date(content.week_start).toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) : '';
            const weekEnd = content.week_end ? new Date(content.week_end).toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) : '';

            html += `
                <div class="list-group-item">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h6 class="mb-0">Weekly Digest: ${weekStart} - ${weekEnd}</h6>
                        <span class="badge bg-${draft.status === 'draft' ? 'warning' : 'primary'}">${draft.status}</span>
                    </div>
                    <p class="mb-2 text-muted small">Created: ${createdDate.toLocaleString()}</p>
                    <button class="btn btn-sm btn-primary me-2" onclick="viewWeeklyDraft('${draft.id}')">View Details</button>
                    <button class="btn btn-sm btn-success me-2" onclick="approveWeeklyDraft('${draft.id}')">Approve</button>
                    <button class="btn btn-sm btn-danger" onclick="rejectWeeklyDraft('${draft.id}')">Reject</button>
                    <div id="weekly-draft-content-${draft.id}" class="mt-3" style="display: none;"></div>
                </div>
            `;
        });

        html += '</div>';
        weeklyDraftsList.innerHTML = html;
    } else if (weeklyDraftsList) {
        weeklyDraftsList.innerHTML = '<p class="text-muted">No weekly drafts available. Click "Run Now" to generate one.</p>';
    }

    // Also try to fetch the current weekly digest from file
    try {
        const currentWeekly = await fetchAPI('/api/dashboard/weekly/current');

        if (currentWeekly.success && currentWeekly.digest) {
            // Use real digest data
            const digest = currentWeekly.digest;

            // Update progress bar (100% if we have a digest)
            const progressBar = document.getElementById('weekly-progress');
            if (progressBar) {
                progressBar.style.width = '100%';
                progressBar.textContent = '100%';
            }

            // Update content statistics from real digest
            const wordCount = document.getElementById('word-count');
            const sourceCount = document.getElementById('source-count');
            const contentCount = document.getElementById('content-count');

            if (wordCount) {
                // Count words in markdown
                const wordCountNum = currentWeekly.markdown ? currentWeekly.markdown.split(/\s+/).length : 0;
                wordCount.textContent = `${wordCountNum} / 800-1200`;
            }
            if (sourceCount) {
                // Count unique sources from statistics
                sourceCount.textContent = digest.statistics?.active_sources || 0;
            }
            if (contentCount) {
                contentCount.textContent = digest.total_items || 0;
            }
        }
    } catch (error) {
        console.log('No current weekly digest file found');
    }
}

// Update quality control section
function updateQualitySection(data) {
    if (!data.success) return;
    
    // Update approval statistics
    if (data.statistics) {
        const stats = data.statistics;
        const total = (stats.approved_count || 0) + (stats.rejected_count || 0);
        const approvalRate = total > 0 ? ((stats.approved_count / total) * 100).toFixed(0) : 0;
        
        const approvalRateElem = document.getElementById('approval-rate');
        if (approvalRateElem) {
            approvalRateElem.textContent = `${approvalRate}%`;
        }
        
        const avgStyleScore = document.getElementById('avg-style-score');
        if (avgStyleScore) {
            avgStyleScore.textContent = (stats.avg_style_score || 0).toFixed(2);
        }
        
        // Update quality chart
        if (charts.quality) {
            charts.quality.data.datasets[0].data = [
                stats.approved_count || 0,
                stats.rejected_count || 0,
                data.pending?.length || 0
            ];
            charts.quality.update();
        }
    }
    
    // Update pending reviews list
    const pendingList = document.getElementById('pending-reviews-list');
    if (pendingList && data.history) {
        const pending = data.history.filter(item => 
            item.status === 'draft' || item.status === 'pending_review'
        );
        
        if (pending.length > 0) {
            let html = '<div class="list-group">';
            pending.forEach(item => {
                html += `
                    <div class="list-group-item">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="mb-1">${item.content_type}</h6>
                                <small class="text-muted">${formatDate(item.created_at)}</small>
                            </div>
                            <button class="btn btn-sm btn-primary" onclick="reviewContent(${item.id})">
                                Review
                            </button>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            pendingList.innerHTML = html;
        } else {
            pendingList.innerHTML = '<p class="text-muted">No pending reviews</p>';
        }
    }
}

// Update schedule section
function updateSchedule(data) {
    if (!data.success) return;
    
    if (data.schedule) {
        data.schedule.forEach(item => {
            if (item.type === 'daily_bot') {
                const nextDaily = document.getElementById('next-daily');
                if (nextDaily) {
                    nextDaily.textContent = formatDateTime(item.next_run);
                }
            } else if (item.type === 'weekly_digest') {
                const nextWeekly = document.getElementById('next-weekly');
                if (nextWeekly) {
                    nextWeekly.textContent = formatDateTime(item.next_run);
                }
            }
        });
    }
}

// Update alerts section
function updateAlerts(data) {
    if (!data.success) return;

    const alertsList = document.getElementById('alerts-list');
    const alertCount = document.getElementById('alert-count');

    // Check if elements exist before using them
    if (!alertsList || !alertCount) {
        console.warn('Alert elements not found in DOM');
        return;
    }

    if (data.alerts && data.alerts.length > 0) {
        let html = '';
        data.alerts.forEach(alert => {
            const severityClass = `alert-${alert.severity || 'info'}`;
            html += `
                <div class="alert-item ${severityClass}">
                    <div class="d-flex justify-content-between align-items-start">
                        <div>
                            <strong>${alert.alert_type}</strong>
                            <p class="mb-0">${alert.message}</p>
                            <small class="text-muted">${formatDateTime(alert.created_at)}</small>
                        </div>
                        ${!alert.resolved ? '<button class="btn btn-sm btn-outline-secondary" onclick="resolveAlert(' + alert.id + ')">Resolve</button>' : ''}
                    </div>
                </div>
            `;
        });
        alertsList.innerHTML = html;
        
        // Update alert count badge
        const unresolvedCount = data.alerts.filter(a => !a.resolved).length;
        if (alertCount && unresolvedCount > 0) {
            alertCount.textContent = unresolvedCount;
            alertCount.style.display = 'inline';
        } else if (alertCount) {
            alertCount.style.display = 'none';
        }
    } else {
        alertsList.innerHTML = '<p class="text-muted">No recent alerts</p>';
        if (alertCount) {
            alertCount.style.display = 'none';
        }
    }
}

// Handle real-time dashboard updates
function handleDashboardUpdate(data) {
    console.log('Received dashboard update:', data);
    
    // Refresh the relevant section based on update type
    switch(data.type) {
        case 'daily_draft':
            fetchAPI('/api/dashboard/daily/stats').then(updateDailySection);
            break;
        case 'weekly_progress':
            fetchAPI('/api/dashboard/weekly/stats').then(updateWeeklySection);
            break;
        case 'quality_update':
            fetchAPI('/api/dashboard/quality/history').then(updateQualitySection);
            break;
        case 'error':
            fetchAPI('/api/dashboard/errors').then(updateAlerts);
            showNotification(data.data.message || 'An error occurred', 'error');
            break;
        default:
            // Refresh overview for any other update
            fetchAPI('/api/dashboard/overview').then(updateOverview);
    }
}

// Refresh dashboard
function refreshDashboard() {
    const refreshIcon = document.getElementById('refresh-icon');
    if (refreshIcon) {
        refreshIcon.classList.add('refresh-indicator');
    }
    
    loadDashboardData().then(() => {
        setTimeout(() => {
            if (refreshIcon) {
                refreshIcon.classList.remove('refresh-indicator');
            }
        }, 1000);
    });
}

// Start auto-refresh
function startAutoRefresh() {
    // Refresh every 30 seconds
    refreshInterval = setInterval(refreshDashboard, 30000);
}

// Update connection status
function updateConnectionStatus(connected) {
    const statusElem = document.getElementById('connection-status');
    if (statusElem) {
        statusElem.textContent = connected ? 'Connected' : 'Disconnected';
        statusElem.className = connected ? 'text-success' : 'text-danger';
    }
}

// Utility functions
function escapeHtml(text) {
    if (!text) return '';
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatDateTime(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', { 
        month: 'short', 
        day: 'numeric', 
        hour: 'numeric', 
        minute: '2-digit' 
    });
}

function showNotification(message, type = 'info') {
    // Simple notification (could be enhanced with a toast library)
    console.log(`[${type.toUpperCase()}] ${message}`);
}

// Action handlers
function approveContent(type) {
    console.log('Approving content:', type);
    // TODO: Implement approval API call
}

function requestRevision(type) {
    console.log('Requesting revision:', type);
    // TODO: Implement revision request API call
}

function rejectContent(type) {
    console.log('Rejecting content:', type);
    // TODO: Implement rejection API call
}

function reviewContent(id) {
    console.log('Reviewing content:', id);
    // TODO: Implement review modal or redirect
}

// Track generation in progress to prevent duplicates
let generationInProgress = false;

function triggerManualRun(type) {
    // Prevent double-clicks
    if (generationInProgress) {
        showNotification('Generation already in progress, please wait...', 'warning');
        return;
    }

    if (confirm(`Are you sure you want to manually generate a ${type} digest?`)) {
        console.log('Triggering manual run:', type);

        // Set flag to prevent duplicate requests
        generationInProgress = true;

        // Disable the button temporarily
        const buttons = document.querySelectorAll(`[onclick*="triggerManualRun"]`);
        buttons.forEach(btn => btn.disabled = true);

        // Show loading state
        showNotification(`Starting ${type} generation...`, 'info');

        // Determine base path based on current location
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';

        // Build absolute URL without credentials
        const baseUrl = `${window.location.protocol}//${window.location.host}`;
        const apiUrl = `${baseUrl}${basePath}/api/dashboard/trigger_manual_run`;

        // Use the single trigger_manual_run endpoint to avoid duplicates
        fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                type: type,
                draft_mode: true,
                skip_audio: type === 'weekly'
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification(`${type.charAt(0).toUpperCase() + type.slice(1)} generation started successfully!`, 'success');
                // Refresh dashboard after a delay
                setTimeout(() => {
                    refreshDashboard();
                    // If it's weekly, also refresh the weekly section
                    if (type === 'weekly') {
                        fetchAPI('/api/dashboard/weekly/stats').then(updateWeeklySection);
                    }
                }, 5000);
            } else {
                showNotification(`Failed to start ${type} generation: ${data.error || 'Unknown error'}`, 'error');
            }
        })
        .catch(error => {
            console.error('Error triggering manual run:', error);
            showNotification(`Error: ${error.message}`, 'error');
        })
        .finally(() => {
            // Reset flag and re-enable buttons
            generationInProgress = false;
            const buttons = document.querySelectorAll(`[onclick*="triggerManualRun"]`);
            buttons.forEach(btn => btn.disabled = false);
        });
    }
}

function resolveAlert(id) {
    console.log('Resolving alert:', id);
    // TODO: Implement resolve alert API call
}

function clearAlerts() {
    if (confirm('Clear all resolved alerts?')) {
        console.log('Clearing resolved alerts');
        // TODO: Implement clear alerts API call
    }
}
// ==================== DRAFT MANAGEMENT FUNCTIONS ====================

// Global variables to store current drafts
let currentDrafts = [];
let currentWeeklyDrafts = [];

// Load all drafts
async function loadDrafts() {
    console.log('Loading drafts...');
    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const url = `${basePath}/api/dashboard/drafts/list`;
        console.log('Fetching from:', url);
        const response = await fetch(url);
        const data = await response.json();
        console.log('API response:', data);

        if (data.success) {
            console.log(`Loaded ${data.drafts.length} drafts`);
            currentDrafts = data.drafts;
            updateDraftCounts(data.drafts);
            renderDraftLists(data.drafts);
        } else {
            console.error('API returned success=false');
        }
    } catch (error) {
        console.error('Error loading drafts:', error);
        showNotification('Error loading drafts', 'error');
    }
}

// Update draft counts in badges
function updateDraftCounts(drafts) {
    const pending = drafts.filter(d => d.status === 'draft' || d.status === 'pending_review');
    const approved = drafts.filter(d => d.status === 'approved');
    const rejected = drafts.filter(d => d.status === 'rejected');

    const draftCount = document.getElementById('draft-count');
    const pendingCount = document.getElementById('pending-drafts-count');
    const approvedCount = document.getElementById('approved-drafts-count');
    const rejectedCount = document.getElementById('rejected-drafts-count');

    if (draftCount) draftCount.textContent = pending.length;
    if (pendingCount) pendingCount.textContent = pending.length;
    if (approvedCount) approvedCount.textContent = approved.length;
    if (rejectedCount) rejectedCount.textContent = rejected.length;
}

// Other draft management functions
function renderDraftLists(drafts) {
    renderDraftList(drafts.filter(d => d.status === 'draft' || d.status === 'pending_review'), 'pending-drafts-list');
    renderDraftList(drafts.filter(d => d.status === 'approved'), 'approved-drafts-list');
    renderDraftList(drafts.filter(d => d.status === 'rejected'), 'rejected-drafts-list');
}

function renderDraftList(drafts, containerId) {
    const container = document.getElementById(containerId);
    console.log(`Rendering ${drafts.length} drafts to ${containerId}`, drafts);
    if (!container) {
        console.error(`Container ${containerId} not found!`);
        return;
    }

    if (drafts.length === 0) {
        console.log(`No drafts for ${containerId}, keeping placeholder`);
        // Keep the placeholder text when no drafts
        return;
    }

    console.log(`Building simplified HTML for ${drafts.length} drafts in ${containerId}`);

    // Build a simpler HTML structure first
    let html = '';

    try {
        drafts.forEach((draft, index) => {
            console.log(`Processing draft ${index}:`, draft);
            const created = new Date(draft.created_at).toLocaleString();
            const typeLabel = draft.type === 'daily_thread' ? 'Daily Thread' : 'Weekly Digest';

            // Extract content preview
            let contentPreview = '';
            let itemCount = 0;

            if (draft.type === 'weekly_digest' && draft.content) {
                if (draft.content.brief) {
                    // Extract first few lines of brief - plain text only
                    const lines = draft.content.brief.split('\n').filter(l => l.trim());
                    contentPreview = lines.slice(0, 3).join(' | ');
                }
                if (draft.content.total_items) {
                    itemCount = draft.content.total_items;
                }
            } else if (draft.type === 'daily_thread' && draft.content) {
                if (draft.content.posts && Array.isArray(draft.content.posts)) {
                    itemCount = draft.content.posts.length;
                    if (itemCount > 0 && draft.content.posts[0].content) {
                        contentPreview = draft.content.posts[0].content.substring(0, 200) + '...';
                    }
                }
            }

            // Build a simple card for each draft with collapsible detail
            const collapseId = `draft-detail-${draft.id}`;
            html += `
                <div class="card mb-3">
                    <div class="card-header">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <strong>${typeLabel}</strong>
                                <span class="badge bg-info ms-2">${draft.status}</span>
                                ${itemCount > 0 ? `<span class="badge bg-secondary ms-2">${itemCount} items</span>` : ''}
                            </div>
                            <small class="text-muted">${created}</small>
                        </div>
                    </div>
                    <div class="card-body">
                        ${contentPreview ? `<div class="mb-3 text-muted small">${escapeHtml(contentPreview)}</div>` : ''}
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <small class="text-muted">ID: ${draft.id}</small>
                            <div>
                                <button class="btn btn-sm btn-primary" data-bs-toggle="collapse" data-bs-target="#${collapseId}">
                                    <i class="bi bi-eye"></i> View Details
                                </button>
                                ${renderDraftActions(draft)}
                            </div>
                        </div>
                        <div class="collapse" id="${collapseId}">
                            <div class="card card-body mt-3">
                                <div id="${collapseId}-content" data-draft-id="${draft.id}">
                                    Loading...
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        });

        // Set the HTML
        container.innerHTML = html;
        console.log(`Successfully rendered ${drafts.length} drafts to ${containerId}`);

        // Add event listeners for collapse events
        drafts.forEach(draft => {
            const collapseId = `draft-detail-${draft.id}`;
            const collapseEl = document.getElementById(collapseId);
            if (collapseEl) {
                collapseEl.addEventListener('shown.bs.collapse', function() {
                    renderDraftFullContent(draft.id, `${collapseId}-content`);
                });
            }
        });
    } catch (error) {
        console.error(`Error rendering drafts:`, error);
        container.innerHTML = `<div class="alert alert-danger">Error rendering drafts: ${error.message}</div>`;
    }
    return; // Early return to skip the complex rendering below

    // Original complex rendering code follows (now skipped)
    drafts.forEach((draft, index) => {
        const typeIcon = draft.type === 'daily_thread' ? 'chat-dots' : 'journal-text';
        const typeBadge = draft.type === 'daily_thread' ? 'primary' : 'info';
        const created = new Date(draft.created_at).toLocaleString();
        const draftId = `draft-${draft.id || index}`;

        // Extract provenance information
        let sourceCount = 0;
        let platforms = [];
        let sources = [];
        let themes = [];
        let trending = [];

        // First try to get from provenance field
        if (draft.provenance && Object.keys(draft.provenance).length > 0) {
            sourceCount = draft.provenance.source_count || 0;
            platforms = draft.provenance.platforms || [];
            sources = draft.provenance.sources || [];

            if (draft.provenance.generation_metadata) {
                themes = draft.provenance.generation_metadata.themes || [];
                trending = draft.provenance.generation_metadata.trending || [];
            }
        }

        // If no provenance, extract from citations (for weekly digests)
        else if (draft.content && draft.content.citations) {
            const citations = draft.content.citations;
            sourceCount = citations.length;

            // Extract unique platforms from citations
            const platformSet = new Set();
            const sourceSet = new Set();

            citations.forEach(cite => {
                // Extract platform from source or URL
                if (cite.source) {
                    const platform = cite.source.split('-')[0].replace('sensor', '').trim();
                    if (platform) platformSet.add(platform);
                    sourceSet.add(cite.source);
                }
                if (cite.url) {
                    if (cite.url.includes('forum.regen')) platformSet.add('forum');
                    if (cite.url.includes('github.com')) platformSet.add('github');
                    if (cite.url.includes('discord')) platformSet.add('discord');
                    if (cite.url.includes('telegram')) platformSet.add('telegram');
                }
            });

            platforms = Array.from(platformSet);
            sources = Array.from(sourceSet);
        }

        // Extract from content text (fallback)
        else if (draft.content && draft.content.brief) {
            // Count sources mentioned in the brief
            const sourceMatches = draft.content.brief.match(/Source: ([^|]+)/g);
            if (sourceMatches) {
                sourceCount = sourceMatches.length;
                const platformSet = new Set();
                sourceMatches.forEach(match => {
                    const source = match.replace('Source: ', '').trim();
                    if (source.includes('discourse')) platformSet.add('discourse');
                    if (source.includes('github')) platformSet.add('github');
                });
                platforms = Array.from(platformSet);
            }
        }

        // Extract content preview
        let contentPreview = '';
        let postCount = 0;
        if (draft.content) {
            if (draft.content.posts && Array.isArray(draft.content.posts)) {
                postCount = draft.content.posts.length;
                // Show first post as preview
                if (postCount > 0 && draft.content.posts[0].content) {
                    contentPreview = draft.content.posts[0].content.substring(0, 200) + '...';
                }
            } else if (draft.content.sections && Array.isArray(draft.content.sections)) {
                // Weekly digest format
                postCount = draft.content.sections.length;
                if (postCount > 0 && draft.content.sections[0].content) {
                    contentPreview = draft.content.sections[0].content.substring(0, 200) + '...';
                }
            }
        }

        // Build provenance display
        let provenanceHtml = '';
        if (sourceCount > 0) {
            provenanceHtml = `
                <small class="text-muted d-block">
                    <i class="bi bi-database me-1"></i>
                    <strong>${sourceCount} sources</strong>
                </small>`;

            if (platforms.length > 0) {
                provenanceHtml += `
                <small class="text-muted d-block">
                    <i class="bi bi-diagram-3 me-1"></i>
                    Platforms: ${platforms.join(', ')}
                </small>`;
            }
        } else {
            provenanceHtml = `
                <small class="text-muted d-block">
                    <i class="bi bi-database me-1"></i>
                    No source tracking
                </small>`;
        }

        html += `
            <div class="list-group-item">
                <div class="mb-3">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <div>
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-${typeIcon} me-2"></i>
                                <span class="badge bg-${typeBadge} me-2">
                                    ${draft.type === 'daily_thread' ? 'Daily Thread' : 'Weekly Digest'}
                                </span>
                                <small class="text-muted">${created}</small>
                                ${postCount > 0 ? `<span class="badge bg-secondary ms-2">${postCount} ${draft.type === 'daily_thread' ? 'posts' : 'sections'}</span>` : ''}
                            </div>
                            <div class="provenance-info mb-2">
                                ${provenanceHtml}
                            </div>
                        </div>
                        <div>
                            ${renderDraftActions(draft)}
                        </div>
                    </div>

                    <!-- Content Preview -->
                    ${contentPreview ? `
                        <div class="content-preview mb-2">
                            <p class="text-muted small mb-2" style="font-style: italic;">
                                ${escapeHtml(contentPreview)}
                            </p>
                        </div>
                    ` : ''}

                    <!-- Expandable Details -->
                    <div class="mt-2">
                        <button class="btn btn-sm btn-outline-secondary" type="button"
                                data-bs-toggle="collapse" data-bs-target="#${draftId}-details">
                            <i class="bi bi-chevron-down"></i> View Details
                        </button>

                        <div class="collapse mt-2" id="${draftId}-details">
                            <div class="card card-body small">
                                ${draft.provenance && draft.provenance.detailed_sources ? `
                                    <h6 class="mb-3">Detailed Source References:</h6>
                                    <div class="ms-3 mb-3">
                                        ${draft.provenance.detailed_sources.map(src => `
                                            <div class="border-start border-3 ps-3 mb-3">
                                                <div class="d-flex justify-content-between align-items-start">
                                                    <div>
                                                        <strong>${src.title}</strong>
                                                        <span class="badge bg-secondary ms-2">${src.type}</span>
                                                    </div>
                                                    <small class="text-muted">${src.platform}</small>
                                                </div>
                                                ${src.author ? `<small class="text-muted d-block">By ${src.author}</small>` : ''}
                                                ${src.channel ? `<small class="text-muted d-block">Channel: ${src.channel}</small>` : ''}
                                                ${src.url ? `<small class="d-block"><a href="${src.url}" target="_blank">${src.url}</a></small>` : ''}
                                                ${src.tx_hash ? `<small class="text-muted d-block">TX: ${src.tx_hash}</small>` : ''}
                                                ${src.amount ? `<small class="text-muted d-block">Amount: ${src.amount}</small>` : ''}
                                                ${src.timestamp ? `<small class="text-muted d-block">Time: ${new Date(src.timestamp).toLocaleString()}</small>` : ''}
                                                ${src.excerpt ? `<p class="mt-2 mb-0"><em>"${src.excerpt}"</em></p>` : ''}
                                            </div>
                                        `).join('')}
                                    </div>
                                ` : sources.length > 0 ? `
                                    <h6 class="mb-2">Source Platforms:</h6>
                                    <ul class="list-unstyled ms-3">
                                        ${sources.map(s => `<li><i class="bi bi-link-45deg"></i> ${s}</li>`).join('')}
                                    </ul>
                                ` : ''}

                                ${themes.length > 0 ? `
                                    <h6 class="mb-2">Themes:</h6>
                                    <ul class="list-unstyled ms-3">
                                        ${themes.map(t => `<li><i class="bi bi-tag"></i> ${t}</li>`).join('')}
                                    </ul>
                                ` : ''}

                                ${trending.length > 0 ? `
                                    <h6 class="mb-2">Trending Topics:</h6>
                                    <ul class="list-unstyled ms-3">
                                        ${trending.map(t => `<li><i class="bi bi-trending-up"></i> ${t}</li>`).join('')}
                                    </ul>
                                ` : ''}

                                ${draft.content && draft.content.posts ? `
                                    <h6 class="mb-2">All Posts:</h6>
                                    <div class="ms-3">
                                        ${draft.content.posts.map((post, i) => `
                                            <div class="mb-2">
                                                <strong>Post ${i + 1}:</strong>
                                                <p class="mb-1">${escapeHtml(post.content)}</p>
                                            </div>
                                        `).join('')}
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });
    html += '</div>';
    console.log(`Setting innerHTML for ${containerId}, HTML length: ${html.length}`);
    try {
        container.innerHTML = html;
        console.log(`Successfully rendered ${drafts.length} drafts to ${containerId}`);
    } catch (error) {
        console.error(`Error setting innerHTML for ${containerId}:`, error);
        console.error('HTML that failed:', html);
    }
}

function renderDraftActions(draft) {
    let html = '';

    if (draft.status === 'draft' || draft.status === 'pending_review') {
        html += `
            <button class="btn btn-sm btn-success me-1" onclick="approveDraft('${draft.id}')" title="Approve">
                <i class="bi bi-check"></i>
            </button>
            <button class="btn btn-sm btn-danger me-1" onclick="rejectDraft('${draft.id}')" title="Reject">
                <i class="bi bi-x"></i>
            </button>
        `;

        if (draft.type === 'weekly_digest' && !draft.metadata?.audio_generated) {
            html += `
                <button class="btn btn-sm btn-info" onclick="generatePodcast('${draft.id}')" title="Generate Podcast">
                    <i class="bi bi-mic"></i> Podcast
                </button>
            `;
        }
    }

    return html;
}

async function approveDraft(draftId) {
    if (!confirm('Approve this draft?')) return;

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/drafts/${draftId}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reviewer: 'dashboard_user' })
        });

        const data = await response.json();
        if (data.success) {
            loadDrafts();
        }
    } catch (error) {
        console.error('Error approving draft:', error);
    }
}

async function rejectDraft(draftId) {
    const reason = prompt('Rejection reason (optional):');
    if (reason === null) return;

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/drafts/${draftId}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reviewer: 'dashboard_user',
                notes: reason || 'Rejected via dashboard'
            })
        });

        const data = await response.json();
        if (data.success) {
            loadDrafts();
        }
    } catch (error) {
        console.error('Error rejecting draft:', error);
    }
}

async function generatePodcast(draftId) {
    if (!confirm('Generate podcast (text + audio)? This may take a few minutes.')) return;

    // Find the button that was clicked and show loading state
    const button = event.target.closest('button');
    const originalContent = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status"></span>Generating...';

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        showNotification('🎙️ Generating podcast brief and audio...', 'info');

        const response = await fetch(`${basePath}/api/dashboard/drafts/${draftId}/generate_podcast`, {
            method: 'POST'
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || `Server error: ${response.status}`);
        }

        if (data.success) {
            // Show detailed success message with file info
            let successMsg = '✅ ' + data.message;
            if (data.file_size) {
                successMsg += ` (${data.file_size})`;
            }
            showNotification(successMsg, 'success');

            // Show audio file location if available
            if (data.audio_file) {
                console.log('Podcast audio file:', data.audio_file);
            }

            // Reload drafts to show updated status
            setTimeout(() => loadDrafts(), 1000);
        } else {
            showNotification(`❌ ${data.error || data.message || 'Failed to generate podcast'}`, 'error');
        }
    } catch (error) {
        console.error('Error generating podcast:', error);
        showNotification(`❌ Error: ${error.message || 'Failed to generate podcast'}`, 'error');
    } finally {
        // Restore button state
        if (button) {
            button.disabled = false;
            button.innerHTML = originalContent;
        }
    }
}

// Keep old function for compatibility
async function generateAudio(draftId) {
    return generatePodcast(draftId);
}

async function generateNewDraft(type) {
    if (!confirm(`Generate new ${type} draft?`)) return;

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/trigger_manual_run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                type: type,
                draft_mode: true,
                skip_audio: type === 'weekly'
            })
        });

        const data = await response.json();
        if (data.success) {
            setTimeout(() => loadDrafts(), 2000);
        }
    } catch (error) {
        console.error('Error generating draft:', error);
    }
}

function refreshDrafts() {
    loadDrafts();
}

function showNotification(message, type = 'info') {
    console.log(`[${type}] ${message}`);
}

// Ensure drafts load when tab is shown
document.addEventListener('DOMContentLoaded', function() {
    // Load drafts when drafts tab is clicked
    const draftsTab = document.getElementById('drafts-tab');
    if (draftsTab) {
        draftsTab.addEventListener('shown.bs.tab', function() {
            loadDrafts();
        });
    }

    // Also handle manual refresh button
    const refreshBtn = document.querySelector('[onclick="refreshDrafts()"]');
    if (refreshBtn) {
        refreshBtn.onclick = function() {
            refreshDrafts();
        };
    }

    // Load drafts initially if the drafts tab is active
    if (draftsTab && draftsTab.classList.contains('active')) {
        loadDrafts();
    }
});

// Functions for weekly drafts
async function viewWeeklyDraft(draftId) {
    const draft = currentWeeklyDrafts.find(d => d.id === draftId);
    if (!draft) return;

    const contentDiv = document.getElementById(`weekly-draft-content-${draftId}`);
    if (!contentDiv) return;

    // Toggle visibility
    if (contentDiv.style.display === 'block') {
        contentDiv.style.display = 'none';
        return;
    }

    // Parse the content
    let content = draft.content;
    if (typeof content === 'string') {
        try {
            content = JSON.parse(content);
        } catch (e) {
            console.error('Error parsing weekly draft content:', e);
        }
    }

    // Convert markdown brief to HTML
    const briefHtml = content.brief ? convertMarkdownToHtml(content.brief) : '<p>No content available</p>';

    let html = `
        <div class="mt-3 p-3 bg-light rounded">
            <h5>Executive Summary</h5>
            <p>${escapeHtml(content.executive_summary || 'No summary available')}</p>

            <h5>Weekly Brief</h5>
            <div class="markdown-content">${briefHtml}</div>

            <h5>Statistics</h5>
            <ul>
                <li>Total Items: ${content.total_items || 0}</li>
                <li>Total Discussions: ${content.total_discussions || 0}</li>
                <li>Active Sources: ${content.statistics?.active_sources || 0}</li>
            </ul>

            <h5>Top Stories</h5>
            <ul>
                ${(content.top_stories || []).map(story =>
                    `<li><strong>${escapeHtml(story.title)}</strong>: ${escapeHtml(story.summary)}</li>`
                ).join('')}
            </ul>
        </div>
    `;

    contentDiv.innerHTML = html;
    contentDiv.style.display = 'block';
}

async function approveWeeklyDraft(draftId) {
    if (!confirm('Approve this weekly digest for publication?')) return;

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/drafts/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ draft_id: draftId })
        });

        const data = await response.json();
        if (data.success) {
            showNotification('Weekly digest approved successfully', 'success');
            // Reload the weekly section
            fetchAPI('/api/dashboard/weekly/stats').then(updateWeeklySection);
        } else {
            showNotification(data.error || 'Failed to approve digest', 'error');
        }
    } catch (error) {
        console.error('Error approving weekly draft:', error);
        showNotification('Error approving digest', 'error');
    }
}

async function rejectWeeklyDraft(draftId) {
    const reason = prompt('Please provide a reason for rejection:');
    if (!reason) return;

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/drafts/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                draft_id: draftId,
                reason: reason
            })
        });

        const data = await response.json();
        if (data.success) {
            showNotification('Weekly digest rejected', 'info');
            // Reload the weekly section
            fetchAPI('/api/dashboard/weekly/stats').then(updateWeeklySection);
        } else {
            showNotification(data.error || 'Failed to reject digest', 'error');
        }
    } catch (error) {
        console.error('Error rejecting weekly draft:', error);
        showNotification('Error rejecting digest', 'error');
    }
}

// Function to render full draft content with markdown support
function renderDraftFullContent(draftId, targetElementId) {
    const draft = currentDrafts.find(d => d.id === draftId);
    const targetElement = document.getElementById(targetElementId);

    if (!draft || !targetElement) {
        console.error('Draft or target element not found:', draftId, targetElementId);
        return;
    }

    let content = '';

    if (draft.type === 'weekly_digest' && draft.content) {
        // Check if podcast has been generated for weekly digest
        // Check both metadata (quality_issues) and content for podcast_generated flag
        const isPodcastGenerated = (draft.metadata && (draft.metadata.podcast_generated || draft.metadata.audio_generated)) ||
                                   (draft.content && draft.content.audio_file);

        if (isPodcastGenerated) {
            content += `<div class="alert alert-success mb-3">
                <h6><i class="bi bi-mic-fill"></i> Podcast Generated</h6>`;

            // Add audio player if file exists
            if (draft.content && draft.content.audio_file) {
                const filename = draft.content.audio_file.split('/').pop();
                // Get the base path for URLs
                const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
                // Ensure proper URL encoding for special characters
                const audioUrl = `${basePath}/podcast_audio/${encodeURIComponent(filename)}`;
                content += `
                    <audio controls class="w-100 mb-2" preload="metadata">
                        <source src="${audioUrl}" type="audio/mpeg">
                        <source src="${audioUrl}" type="audio/mp3">
                        Your browser does not support the audio element.
                    </audio>
                    <div class="btn-group btn-group-sm" role="group">
                        <a href="${basePath}/podcast_audio/${encodeURIComponent(filename)}" download="${filename}" class="btn btn-outline-primary">
                            <i class="bi bi-download"></i> Download MP3
                        </a>
                        <a href="${basePath}/api/dashboard/drafts/${draft.id}/markdown" download="weekly_digest.md" class="btn btn-outline-secondary">
                            <i class="bi bi-file-text"></i> Download Markdown for NotebookLM
                        </a>
                    </div>`;
            } else {
                // Fallback to find the latest audio file for this draft
                const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
                content += `
                    <div class="btn-group btn-group-sm" role="group">
                        <a href="${basePath}/podcast_audio/regen_weekly_2025-09-25T06:08:05.789145+00:00.mp3" download class="btn btn-outline-primary">
                            <i class="bi bi-download"></i> Download Latest MP3
                        </a>
                        <a href="${basePath}/api/dashboard/drafts/${draft.id}/markdown" download="weekly_digest.md" class="btn btn-outline-secondary">
                            <i class="bi bi-file-text"></i> Download Markdown for NotebookLM
                        </a>
                    </div>`;
            }
            content += `</div>`;
        }

        if (draft.content.brief) {
            // Convert markdown to HTML (basic conversion)
            const htmlContent = convertMarkdownToHtml(draft.content.brief);
            content += `<div class="digest-content">${htmlContent}</div>`;
        }

        if (draft.content.citations && draft.content.citations.length > 0) {
            content += `<h5 class="mt-4">Citations (${draft.content.citations.length})</h5><ul class="list-group list-group-flush">`;
            draft.content.citations.forEach(cite => {
                // Fix malformed GitHub URLs
                let url = cite.url || '';
                if (url.includes('github_sensor_')) {
                    url = url.replace(/\/github_sensor_[^\/]+\//, '/');
                }

                content += `<li class="list-group-item">
                    <strong>${escapeHtml(cite.title || 'Untitled')}</strong><br>
                    <small class="text-muted">Source: ${escapeHtml(cite.source || 'Unknown')} | Date: ${cite.date || 'N/A'}</small><br>
                    ${url ? `<a href="${url}" target="_blank" class="small">${url}</a>` : ''}
                </li>`;
            });
            content += '</ul>';
        }
    } else if (draft.type === 'daily_thread' && draft.content) {
        if (draft.content.posts && Array.isArray(draft.content.posts)) {
            content = `<h5>Daily Thread Posts (${draft.content.posts.length})</h5>`;
            // Check if podcast has been generated
            if (draft.metadata && draft.metadata.podcast_generated) {
                content += `<div class="alert alert-success mb-3">
                    <h6><i class="bi bi-mic-fill"></i> Podcast Generated</h6>`;

                // Add audio player if file exists
                if (draft.content.audio_file) {
                    const filename = draft.content.audio_file.split('/').pop();
                    // Get the base path for URLs
                    const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
                    // Ensure proper URL encoding for special characters
                    const audioUrl = `${basePath}/podcast_audio/${encodeURIComponent(filename)}`;
                    content += `
                        <audio controls class="w-100 mb-2">
                            <source src="${audioUrl}" type="audio/mpeg">
                            Your browser does not support the audio element.
                        </audio>
                        <div class="btn-group btn-group-sm" role="group">
                            <a href="${basePath}/podcast_audio/${encodeURIComponent(filename)}" download="${filename}" class="btn btn-outline-primary">
                                <i class="bi bi-download"></i> Download MP3
                            </a>
                            <a href="${basePath}/api/dashboard/drafts/${draft.id}/markdown" download="weekly_digest.md" class="btn btn-outline-secondary">
                                <i class="bi bi-file-text"></i> Download Markdown
                            </a>
                        </div>`;
                }
                content += `</div>`;
            }

            // Display unified sources section if available
            if (draft.content.unified_sources && draft.content.unified_sources.length > 0) {
                content += '<div class="card mb-3"><div class="card-body">';
                content += '<h5 class="card-title">Sources</h5>';
                content += '<ul class="list-group list-group-flush">';

                draft.content.unified_sources.forEach(source => {
                    let icon = '';
                    if (source.type === 'forum') icon = '💬';
                    else if (source.type === 'github') icon = '💻';
                    else if (source.type === 'ledger') icon = '📊';
                    else if (source.type === 'governance') icon = '🗳️';
                    else if (source.type === 'discord') icon = '🎮';
                    else if (source.type === 'twitter') icon = '🐦';

                    content += '<li class="list-group-item">';
                    content += `${icon} <strong>${source.type}:</strong> `;
                    if (source.url) {
                        content += `<a href="${source.url}" target="_blank">${escapeHtml(source.title || source.url)}</a>`;
                    } else {
                        content += escapeHtml(source.title || 'No title');
                    }
                    if (source.published_at) {
                        content += ` <small class="text-muted">(${new Date(source.published_at).toLocaleDateString()})</small>`;
                    }
                    content += '</li>';
                });

                content += '</ul></div></div>';
            }

            draft.content.posts.forEach((post, i) => {
                content += `<div class="card mb-2">
                    <div class="card-body">
                        <h6 class="card-subtitle mb-2 text-muted">Post ${i+1}</h6>
                        <p class="card-text">${escapeHtml(post.content || '')}</p>`;

                // Don't display individual sources anymore - they're in the unified section

                content += `</div>
                </div>`;
            });
        }
    }

    targetElement.innerHTML = content || '<p class="text-muted">No content available</p>';
}

// Basic markdown to HTML converter
function convertMarkdownToHtml(markdown) {
    if (!markdown) return '';

    let html = escapeHtml(markdown);

    // Convert headers
    html = html.replace(/^### (.*$)/gim, '<h5>$1</h5>');
    html = html.replace(/^## (.*$)/gim, '<h4>$1</h4>');
    html = html.replace(/^# (.*$)/gim, '<h3>$1</h3>');

    // Convert bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Convert italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Convert links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

    // Convert line breaks
    html = html.replace(/\n/g, '<br>');

    // Convert lists
    html = html.replace(/^\- (.+)$/gim, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');

    return html;
}

// Export functions to global scope for HTML onclick handlers
// This must be at the end after all functions are defined
window.loadDrafts = loadDrafts;
window.approveDraft = approveDraft;
window.rejectDraft = rejectDraft;
window.generateAudio = generateAudio;
window.refreshDrafts = refreshDrafts;
window.triggerManualRun = triggerManualRun;
window.updateDraftCounts = updateDraftCounts;
window.renderDraftLists = renderDraftLists;
window.renderDraftFullContent = renderDraftFullContent;
