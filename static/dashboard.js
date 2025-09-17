// Milestone B Content Operations Dashboard JavaScript
// Real-time updates and interactive features

// Global variables
let socket = null;
let charts = {};
let refreshInterval = null;

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', function() {
    initializeSocket();
    initializeCharts();
    loadDashboardData();
    startAutoRefresh();
});

// Initialize WebSocket connection
function initializeSocket() {
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
    const url = basePath + endpoint;

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
    
    // Update today's draft
    const draftContent = document.getElementById('daily-draft-content');
    const dailyActions = document.getElementById('daily-actions');
    
    if (data.today && data.today.draft) {
        const draft = data.today.draft;
        const content = typeof draft.content === 'string' ? JSON.parse(draft.content) : draft.content;
        
        if (content.posts && content.posts.length > 0) {
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
            dailyActions.style.display = draft.status === 'pending_review' ? 'block' : 'none';
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
function updateWeeklySection(data) {
    if (!data.success) return;
    
    // Update progress bar
    const progressBar = document.getElementById('weekly-progress');
    if (progressBar && data.current_week) {
        const progress = data.current_week.progress_percentage || 0;
        progressBar.style.width = `${progress}%`;
        progressBar.textContent = `${Math.round(progress)}%`;
    }
    
    // Update content statistics
    if (data.current_week) {
        const wordCount = document.getElementById('word-count');
        const sourceCount = document.getElementById('source-count');
        const contentCount = document.getElementById('content-count');
        
        if (wordCount) {
            const wc = data.current_week.digest?.metadata?.word_count || 0;
            wordCount.textContent = `${wc} / 800-1200`;
        }
        if (sourceCount) {
            sourceCount.textContent = data.current_week.unique_sources || 0;
        }
        if (contentCount) {
            contentCount.textContent = data.current_week.content_collected || 0;
        }
    }
    
    // Update digest content preview
    const digestContent = document.getElementById('weekly-digest-content');
    if (digestContent && data.current_week && data.current_week.digest) {
        const digest = data.current_week.digest;
        const content = typeof digest.content === 'string' ? JSON.parse(digest.content) : digest.content;
        
        if (content.brief) {
            let html = `
                <div class="mb-3">
                    <span class="badge bg-info me-2">Status: ${digest.status}</span>
                    <span class="badge bg-secondary">Word Count: ${digest.metadata?.word_count || 0}</span>
                </div>
                <div class="content-preview">
                    <h6>Weekly Brief Preview</h6>
                    <p>${escapeHtml(content.brief.substring(0, 500))}...</p>
                </div>
            `;
            digestContent.innerHTML = html;
        }
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
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
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

function triggerManualRun(type) {
    if (confirm(`Are you sure you want to manually trigger the ${type} bot?`)) {
        console.log('Triggering manual run:', type);

        // Show loading state
        showNotification(`Starting ${type} generation...`, 'info');

        // Determine base path based on current location
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';

        // Make API call to trigger generation
        fetch(`${basePath}/api/dashboard/generate/${type}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                draft_mode: true,
                manual_trigger: true
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification(`${type.charAt(0).toUpperCase() + type.slice(1)} generation started successfully!`, 'success');
                // Refresh dashboard after a delay
                setTimeout(() => refreshDashboard(), 5000);
            } else {
                showNotification(`Failed to start ${type} generation: ${data.error || 'Unknown error'}`, 'error');
            }
        })
        .catch(error => {
            console.error('Error triggering manual run:', error);
            showNotification(`Error: ${error.message}`, 'error');
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

// Global variable to store current drafts
let currentDrafts = [];

// Load all drafts
async function loadDrafts() {
    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/drafts/list`);
        const data = await response.json();

        if (data.success) {
            currentDrafts = data.drafts;
            updateDraftCounts(data.drafts);
            renderDraftLists(data.drafts);
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
    if (!container) return;

    if (drafts.length === 0) {
        return;
    }

    let html = '<div class="list-group">';
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

        if (draft.provenance && Object.keys(draft.provenance).length > 0) {
            sourceCount = draft.provenance.source_count || 0;
            platforms = draft.provenance.platforms || [];
            sources = draft.provenance.sources || [];

            if (draft.provenance.generation_metadata) {
                themes = draft.provenance.generation_metadata.themes || [];
                trending = draft.provenance.generation_metadata.trending || [];
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
    container.innerHTML = html;
}

function renderDraftActions(draft) {
    let html = '<div class="btn-group btn-group-sm">';

    if (draft.status === 'draft' || draft.status === 'pending_review') {
        html += `
            <button class="btn btn-success" onclick="approveDraft('${draft.id}')">
                <i class="bi bi-check"></i>
            </button>
            <button class="btn btn-danger" onclick="rejectDraft('${draft.id}')">
                <i class="bi bi-x"></i>
            </button>
        `;

        if (draft.type === 'weekly_digest' && !draft.metadata?.audio_generated) {
            html += `
                <button class="btn btn-info" onclick="generateAudio('${draft.id}')">
                    <i class="bi bi-mic"></i>
                </button>
            `;
        }
    }

    html += '</div>';
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

async function generateAudio(draftId) {
    if (!confirm('Generate audio? This may take a few minutes.')) return;

    try {
        const basePath = window.location.pathname.includes('/digests') ? '/digests' : '';
        const response = await fetch(`${basePath}/api/dashboard/drafts/${draftId}/generate_audio`, {
            method: 'POST'
        });

        const data = await response.json();
        if (data.success) {
            loadDrafts();
        }
    } catch (error) {
        console.error('Error generating audio:', error);
    }
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
