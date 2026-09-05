#!/usr/bin/env bash
# Enumerate the live personal-koi-mcp processes and their parent sessions.
#
# The count is VOLATILE - measured 8, then 9, then 10, then 7, then 9 within an
# hour on 2026-09-04, as agent sessions start and stop MCP children. Never carry
# a number from a previous run; enumerate at the moment you restart.
#
# /usr/bin/ps does NOT exist on this machine; /bin/ps is the binary.
# One ps snapshot is used for everything below, so the table and the count can
# never disagree (an earlier version took two snapshots and reported 7 and 9).
set -uo pipefail
DIST="/Users/darrenzal/projects/personal-koi-mcp/dist/index.js"
SNAP=$(/bin/ps -eo pid=,ppid=,lstart=,command= | grep -F "$DIST" | grep -v ' grep ')

printf '%-8s %-8s %-25s %s\n' PID PPID STARTED "PARENT"
while IFS= read -r line; do
  [ -z "$line" ] && continue
  pid=$(awk '{print $1}' <<<"$line")
  ppid=$(awk '{print $2}' <<<"$line")
  started=$(awk '{print $3" "$4" "$5" "$6" "$7}' <<<"$line")
  parent=$(/bin/ps -p "$ppid" -o command= 2>/dev/null | cut -c1-52)
  printf '%-8s %-8s %-25s %s\n' "$pid" "$ppid" "$started" "${parent:-<parent gone>}"
done <<<"$SNAP"

n=$(grep -c . <<<"$SNAP")
[ -z "$SNAP" ] && n=0
echo
echo "count: $n   (from the SAME snapshot as the table above)"
echo "axios resident in node_modules: $(node -e "console.log(require('/Users/darrenzal/projects/personal-koi-mcp/node_modules/axios/package.json').version)" 2>/dev/null || echo '?')"
echo "lockfile pins:                  $(python3 -c "import json;print(json.load(open('/Users/darrenzal/projects/personal-koi-mcp/package-lock.json'))['packages']['node_modules/axios']['version'])" 2>/dev/null || echo '?')"
echo
echo "If those two differ, the fix is committed but NOT live: run"
echo "  cd ~/projects/personal-koi-mcp && npm install"
echo "then restart each PID above (in Claude Code: /mcp reconnect; Codex: restart the session)."
