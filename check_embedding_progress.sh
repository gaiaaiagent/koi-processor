#!/bin/bash
echo "=== Re-embedding Progress ==="
tail -3 regenerate_full.log | grep "Progress"
echo ""
echo "=== BGE Server Cache Status ==="
curl -s http://localhost:8090/cache/stats | python3 -m json.tool
echo ""
echo "=== Process Status ==="
ps aux | grep "regenerate_embeddings" | grep -v grep | awk '{print "PID:", $2, "CPU:", $3"%", "MEM:", $4"%", "Started:", $9}'
