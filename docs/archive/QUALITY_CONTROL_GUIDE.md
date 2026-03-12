# Quality Control System Guide

## Overview

The Quality Control System ensures all content generated for Regen Network meets high standards for accuracy, professionalism, and compliance with the David Fortson / Many Mangos style guide. This system implements comprehensive validation, review workflows, and auto-publish capabilities as specified in Milestone B.

## Components

### 1. Quality Control Module (`quality_control.py`)

**Purpose**: Core validation and approval logic

**Key Features**:
- Content validation (speculation detection, link verification, source checking)
- Style guide compliance scoring
- Approval workflow management
- Auto-publish after week 1 of manual approvals
- Rollback mechanisms for published content

### 2. Review Interface (`scripts/review_interface.py`)

**Purpose**: Interactive CLI for Gregory to review and approve content

**Features**:
- View pending reviews with quality scores
- Detailed content display with issues highlighted
- Approve/reject/request changes workflow
- Statistics dashboard
- Auto-publish management

### 3. Quality Pipeline (`scripts/quality_pipeline.py`)

**Purpose**: Orchestrates the complete content generation and review workflow

**Flow**:
1. Content generation (Daily Curator / Weekly Aggregator)
2. Quality validation and scoring
3. Review submission
4. Approval/auto-publish decision
5. Publishing or manual review queue

## Validation Rules

### No Speculation
- Detects speculative phrases like "might be", "could be", "potentially"
- Removes or flags uncertain language
- Ensures only confirmed facts are published

### Link Validation
- Verifies all URLs are accessible
- Checks HTTP status codes
- Handles redirects appropriately
- Trusted domains get priority

### Source Requirements
- Ensures claims have proper citations
- Detects source indicators ("according to", "data from")
- URLs count as implicit sources
- Flags unsourced statements

### Style Compliance
- Professional tone enforcement
- No excessive capitalization
- Limited punctuation (no "!!!" or "???")
- Clear call-to-action requirements
- Consistent voice throughout

### Private Data Protection
- Scans for email addresses
- Detects phone numbers
- Identifies API keys or tokens
- Prevents accidental data leaks

## Approval Workflow

### Manual Review Process (Week 1)

1. **Content Generation**
   - Daily Curator creates thread at 12:00 ET
   - Weekly Aggregator creates digest on Fridays

2. **Quality Validation**
   - Automatic validation checks run
   - Style and validation scores calculated
   - Issues identified and documented

3. **Review Queue**
   - Content enters pending review status
   - Gregory notified of new content

4. **Manual Review**
   ```bash
   # Run the review interface
   python scripts/review_interface.py
   ```
   - View content and quality scores
   - Review identified issues
   - Approve, reject, or request changes

5. **Publication**
   - Approved content marked for publishing
   - X Bot posts daily threads
   - Weekly digest sent to NotebookLM

### Auto-Publish Process (After Week 1)

**Activation Criteria**:
- 7 days of manual approvals completed
- Minimum 5 consecutive approvals
- Average quality score ≥ 0.85

**Process**:
1. Content automatically validated
2. If scores meet thresholds, marked as auto-publish eligible
3. Auto-publish check runs periodically
4. Qualifying content published without manual review
5. Gregory still reviews for quality assurance

## Configuration

### Quality Thresholds (`config/quality_config.yaml`)

```yaml
quality_thresholds:
  min_style_score: 0.8        # 80% style compliance required
  min_validation_score: 0.9   # 90% validation score required
  max_speculation_phrases: 0  # No speculation allowed
  required_sources: true      # Sources mandatory
  max_link_failures: 0        # All links must work
```

### Auto-Publish Settings

```yaml
auto_publish:
  enabled: false              # Enable after week 1
  after_days: 7               # Days before activation
  min_consecutive_approvals: 5  # Required approval streak
  quality_threshold: 0.85     # Minimum quality for auto-publish
```

## Usage Examples

### Running the Review Interface

```bash
# Start interactive review session
python scripts/review_interface.py

# With custom config
python scripts/review_interface.py path/to/config.yaml
```

### Testing the Pipeline

```bash
# Run quality pipeline with test data
python scripts/quality_pipeline.py

# Test all validation rules
python tests/test_quality_control.py
```

### Checking Pending Reviews

```python
from quality_control import QualityControl

qc = QualityControl()
await qc.initialize_db()

# Get pending reviews
pending = await qc.get_pending_reviews()
for review in pending:
    print(f"Review {review['review_id']}: Score {review['style_score']}")
```

### Manual Quality Check

```python
# Validate content manually
content = {
    'posts': [
        {'content': 'Your tweet content here'}
    ]
}

validation = await qc.validate_content(content, ContentType.DAILY_THREAD)
if validation['passed']:
    print("Content passed validation!")
else:
    print(f"Issues found: {validation['issues']}")
```

## Rollback Procedures

### When to Rollback
- Factual errors discovered post-publication
- Violation of community guidelines
- Technical issues with content
- Accidental publication

### Rollback Process

```python
# Rollback published content
success = await qc.rollback_publication(
    review_id='review-id-here',
    reason='Factual error in statistics',
    rolled_back_by='Gregory'
)
```

### Rollback Effects
- Content status changed to "rolled_back"
- Audit trail created
- Content removed from publication queue
- Notification sent (if configured)

## Database Schema

### quality_reviews Table

| Column | Type | Description |
|--------|------|-------------|
| review_id | UUID | Unique review identifier |
| content_id | VARCHAR | Original content ID |
| content_type | VARCHAR | daily_thread/weekly_digest |
| content_data | JSONB | Full content data |
| style_score | FLOAT | Style compliance (0-1) |
| validation_score | FLOAT | Validation score (0-1) |
| quality_issues | JSONB | Identified issues |
| approval_status | VARCHAR | draft/pending/approved/published |
| reviewer_notes | TEXT | Review comments |
| auto_publish_eligible | BOOLEAN | Eligible for auto-publish |
| created_at | TIMESTAMPTZ | Creation time |
| reviewed_at | TIMESTAMPTZ | Review time |
| published_at | TIMESTAMPTZ | Publication time |

### approval_history Table

| Column | Type | Description |
|--------|------|-------------|
| history_id | UUID | History entry ID |
| review_id | UUID | Related review |
| previous_status | VARCHAR | Status before change |
| new_status | VARCHAR | Status after change |
| changed_by | VARCHAR | Who made the change |
| notes | TEXT | Change notes |
| changed_at | TIMESTAMPTZ | Change timestamp |

## Monitoring and Statistics

### View Statistics

```bash
# In review interface
Select option 3: View approval statistics

# Programmatically
stats = await qc.get_approval_stats(days=7)
print(f"Approval rate: {stats['approved'] / stats['total_reviews'] * 100}%")
```

### Key Metrics
- Total reviews per period
- Approval/rejection rates
- Average quality scores
- Auto-publish success rate
- Rollback frequency

## Troubleshooting

### Common Issues

**Issue**: Content failing validation repeatedly
- Check speculation phrases in content
- Verify all links are accessible
- Ensure sources are cited
- Review style guide compliance

**Issue**: Auto-publish not activating
- Verify 7 days have passed
- Check consecutive approval count
- Ensure quality thresholds met
- Confirm auto_publish.enabled = true

**Issue**: Database connection errors
- Check PostgreSQL is running
- Verify connection string
- Ensure database exists
- Check user permissions

### Debug Mode

```python
# Enable detailed logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Run with verbose output
qc = QualityControl()
qc.debug_mode = True
```

## Best Practices

### For Content Creators
1. Always cite sources for claims
2. Avoid speculative language
3. Verify links before submission
4. Follow style guide strictly
5. Review content before submission

### For Reviewers
1. Check quality scores first
2. Read all identified issues
3. Verify factual accuracy
4. Ensure brand consistency
5. Document rejection reasons

### For System Administrators
1. Monitor database growth
2. Regular backup of reviews
3. Check auto-publish logs
4. Update quality thresholds as needed
5. Review rollback history

## Integration with Other Systems

### Daily Curator Integration
- Curator output → Quality Control → X Bot
- Automatic submission after generation
- Quality scores influence thread structure

### Weekly Aggregator Integration
- Digest → Quality Control → NotebookLM
- Brief validation before podcast generation
- Source verification for citations

### Scheduler Integration
- Quality checks run after content generation
- Auto-publish checks on schedule
- Notification triggers for reviews needed

## Future Enhancements

### Planned Features
1. Machine learning for better speculation detection
2. Automated fact-checking against knowledge base
3. Multi-reviewer workflow support
4. Slack/email notifications
5. Web-based review interface
6. A/B testing for style variations
7. Sentiment analysis integration
8. Automated correction suggestions

### API Endpoints (Future)
```
POST /api/quality/submit
GET  /api/quality/review/{id}
POST /api/quality/approve/{id}
POST /api/quality/reject/{id}
GET  /api/quality/stats
POST /api/quality/rollback/{id}
```

## Support

For issues or questions:
1. Check this documentation
2. Review test results: `python tests/test_quality_control.py`
3. Check logs in `logs/quality_control.log`
4. Contact the development team

## Acceptance Criteria Checklist

- [x] Content validation checks (no speculation, link validity)
- [x] Style guide compliance scoring
- [x] Approval interface for Gregory
- [x] Auto-publish logic after week 1
- [x] Rollback mechanisms
- [x] Database persistence
- [x] Comprehensive testing
- [x] Documentation

## License

Part of the Regen Network Information Pipeline Project
Milestone B: Quality Control System