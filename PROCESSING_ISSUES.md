# KOI Document Processing - Known Issues & Solutions

## Issue #1: Process Hang on Specific Documents

**Date**: September 4, 2025  
**Severity**: Critical (blocks entire pipeline)  
**Status**: Documented, Solution Implemented

### Problem Description
The full dataset processing pipeline gets stuck on specific documents and hangs indefinitely, consuming 99%+ CPU but making no progress.

### Symptoms
- Process shows as running (high CPU usage)
- No new log entries for extended periods (24+ hours)
- Last processed document appears in log but never completes
- Checkpoint file not updated from hang point

### Specific Instance Details
- **Stuck Document**: `Token_Fee_Split_1_0_abfd0e51.md` (document #553)
- **Last Successful**: Document #550 completed at Sep 3, 8:29 AM
- **Hang Duration**: 24+ hours (Sep 3 8:29 AM → Sep 4 0:30 AM)
- **Process ID**: 55286
- **CPU Usage**: 99.4% continuous
- **Memory**: Normal (~2.8MB)

### Context When Issue Occurred
```
Progress before hang:
📦 Batch 55/112 (docs 541-550) ✅ COMPLETED
  [550/1116] 052__Kyle_Birchard___Pollinator_Eco 🧠 12e  29.9s
  💾 Checkpoint saved at 550 documents

📦 Batch 56/112 (docs 551-560) ⚠️ STARTED
  [551/1116] send_data_to_Mathew_for_wooddburn_a 🔧  1e   7.5s  ✅
  [552/1116] DeSci_ce4850e8.md                   🔧  1e  10.4s  ✅  
  [553/1116] Token_Fee_Split_1_0_abfd0e51.md     ❌ HUNG HERE
```

### Likely Root Causes
1. **Mistral Processing Loop**: Document content triggers infinite loop in Mistral AI processing
2. **Large Document Size**: Document too large causing memory/processing issues
3. **Special Characters/Format**: Document contains problematic formatting or characters
4. **API Timeout**: Mistral API call hanging without timeout handling

### Immediate Solution
```bash
# 1. Kill the stuck process
ps aux | grep full_dataset_pipeline.py
kill [PID]

# 2. Restart from last checkpoint
cd /Users/darrenzal/projects/RegenAI/koi-processor
python full_dataset_pipeline.py --resume

# 3. Monitor for progress
tail -f full_dataset_processing.log
```

### Prevention Strategies (Future Implementation)
1. **Document Timeout**: Add per-document processing timeout (5-10 minutes max)
2. **Progress Watchdog**: Monitor for stuck processing and auto-restart
3. **Skip Mechanism**: Option to skip problematic documents and continue
4. **Document Pre-filtering**: Identify potentially problematic documents before processing

### Code Changes Needed
```python
# Add timeout wrapper for document processing
import signal
from contextlib import contextmanager

@contextmanager
def timeout(seconds):
    def timeout_handler(signum, frame):
        raise TimeoutError("Document processing timeout")
    
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)

# Usage in document processor:
try:
    with timeout(300):  # 5 minute timeout
        result = process_document(doc)
except TimeoutError:
    logger.warning(f"Document {doc_id} timed out, using fallback")
    result = fallback_process_document(doc)
```

### Performance Impact
- **Documents Processed**: 550/1,116 (49.3% complete) 
- **Time Lost**: ~24 hours of processing time
- **Entities Extracted**: 1,349 (good rate maintained)
- **Restart Cost**: Minimal (resumes from checkpoint)

### Monitoring Recommendations
1. **Regular Check**: Monitor log file modification time every hour
2. **Progress Tracking**: Compare processed document count over time
3. **CPU Pattern**: Sustained 99% CPU without log updates = likely hang
4. **Automated Alerting**: Script to detect stuck processing and alert

### Resolution Outcome
- **Action Taken**: Process killed and restarted from checkpoint
- **Resume Point**: Document 551 (checkpoint at 550)
- **Expected Completion**: Continue processing remaining 566 documents
- **Data Integrity**: No data loss (checkpoint system working correctly)

### Issue #2: Second Hang on Document 816

**Date**: September 4, 2025  
**Severity**: Critical (timeout protection failed)  
**Status**: Resolved

### Problem Description
After implementing timeout protection, processing got stuck AGAIN on the same document (`Token_Fee_Split_1_0_abfd0e51.m`) at document 816, running for 13+ hours without progress.

### Root Cause Analysis
**Signal-based timeout mechanism failed**: The original implementation using `signal.SIGALRM` doesn't reliably interrupt network I/O operations in the Ollama client, causing timeouts to be ignored.

### Updated Solution
**Robust timeout + Document skip mechanism**:

1. **ThreadPoolExecutor-based timeout**: Replaced signal-based timeout with thread pool execution and `concurrent.futures.TimeoutError`
2. **Document skip list**: Added skip mechanism for known problematic documents
3. **Improved error handling**: Graceful fallback that maintains processing statistics

### Code Implementation
```python
# New robust timeout mechanism
def with_timeout(timeout_seconds, fallback_result=None):
    def decorator(func):
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)
                try:
                    return future.result(timeout=timeout_seconds)
                except concurrent.futures.TimeoutError:
                    raise TimeoutError(f"Operation timed out after {timeout_seconds} seconds")
        return wrapper
    return decorator

# Skip list for problematic documents
self.skip_list = {
    'Token_Fee_Split_1_0_abfd0e51.md',
    'Token_Fee_Split_1_0_abfd0e51.m'
}
```

### Prevention Strategy
- **Pre-processing check**: Documents in skip list are processed with minimal entity creation
- **Robust timeout**: ThreadPoolExecutor ensures network operations can be interrupted
- **Graceful handling**: Skip mechanism maintains processing continuity and statistics

## Future Issue Template

### Issue #X: [Title]
**Date**: [Date]  
**Severity**: [Low/Medium/High/Critical]  
**Status**: [Open/In Progress/Resolved]

**Problem Description**: [Brief description]  
**Symptoms**: [Observable symptoms]  
**Root Cause**: [If identified]  
**Solution**: [Steps to resolve]  
**Prevention**: [How to prevent recurrence]

---

*Document maintained by KOI Processing Team*  
*Last Updated: September 4, 2025*