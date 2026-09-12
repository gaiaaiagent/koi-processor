#!/usr/bin/env bash
# Answer "are the backups actually still happening?" -- the question nothing asked.
#
# WHY THIS EXISTS. Two markers were being written faithfully by
# koi_offsite_copy.sh and koi_archive_offsite.sh, and a 2026-09-12 review found
# that NOTHING ANYWHERE READ THEM. A grep across ~/projects, ~/.claude, ~/bin and
# ~/.config returned only the writers and their tests. So the system recorded
# when a backup last left the machine and had no way to notice that it had
# stopped.
#
# That is not theoretical either. The 2026-09-11 backup never completed: there is
# a START line in backup.log with no OK and no FAIL after it, and no dump on
# disk for that date. The machine restarted mid-run, the cleanup trap never got
# to run, and nothing said a word. It was found a day later by a human reading
# the log for an unrelated reason.
#
# Exit 0 = everything fresh. Exit 1 = something is stale or missing, with the
# specific reason named. Designed to be run by launchd and to be loud.
set -uo pipefail

DEST="${KOI_BACKUP_DEST:-${HOME}/koi-backups}"
MAX_AGE_H="${KOI_BACKUP_MAX_AGE_HOURS:-30}"     # nightly at 03:15 -> 30h covers one miss
ARCHIVE_MAX_AGE_H="${KOI_ARCHIVE_MAX_AGE_HOURS:-192}"   # weekly sensor -> 8 days
DATABASES="${KOI_BACKUP_CHECK_DATABASES:-personal_koi eliza salishsee_public indigenomics_koi infinite_regen}"

now=$(date +%s)
problems=0
say() { echo "$*"; }
bad() { say "STALE: $*"; problems=$((problems+1)); }

age_hours() { echo $(( (now - $1) / 3600 )); }

say "backup freshness check at $(date '+%F %T')"

for db in $DATABASES; do
  m="${DEST}/.last-offhost-sync-${db}"
  if [ ! -f "$m" ]; then
    bad "${db}: no off-host marker at ${m} -- this database has NEVER been copied off this machine"
    continue
  fi
  ts="$(awk '{print $1}' "$m")"
  # ISO-8601 Z -> epoch. BSD date needs the format spelled out.
  epoch="$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$ts" +%s 2>/dev/null || echo 0)"
  if [ "$epoch" = "0" ]; then
    bad "${db}: marker timestamp '${ts}' is unparseable"
    continue
  fi
  h=$(age_hours "$epoch")
  if [ "$h" -gt "$MAX_AGE_H" ]; then
    bad "${db}: last off-host copy was ${h}h ago (limit ${MAX_AGE_H}h) -- $(awk '{print $2}' "$m")"
  else
    say "  ok   ${db}: off-host ${h}h ago ($(awk '{print $2}' "$m"))"
  fi
done

am="${DEST}/.last-archive-offsite"
if [ ! -f "$am" ]; then
  bad "source archive: no marker at ${am}"
else
  ats="$(awk '{print $1}' "$am")"
  aepoch="$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$ats" +%s 2>/dev/null || echo 0)"
  ah=$(age_hours "${aepoch:-0}")
  if [ "${aepoch:-0}" = "0" ] || [ "$ah" -gt "$ARCHIVE_MAX_AGE_H" ]; then
    bad "source archive: last off-host copy was ${ah}h ago (limit ${ARCHIVE_MAX_AGE_H}h)"
  else
    say "  ok   source archive: off-host ${ah}h ago ($(awk '{print $5}' "$am"))"
  fi
fi

# A START with no OK and no FAIL after it is the 2026-09-11 signature: a run
# that died without ever reaching a guard. It leaves no other trace.
LOGF="${DEST}/backup.log"
if [ -f "$LOGF" ]; then
  dangling="$(awk '
    /START backup/ { db=$0; started++; open_line=NR; pending=1 }
    /OK: |FAIL: |ABORT/ { pending=0 }   # ABORT: and ABORTED both close a START
    END { if (pending) print "yes" }' "$LOGF")"
  [ "$dangling" = "yes" ] && bad "backup.log ends on a START with no OK/FAIL/ABORTED -- a run died without reaching any guard"
fi

if [ "$problems" -gt 0 ]; then
  say "FAIL: ${problems} problem(s)"
  exit 1
fi
say "all fresh"
