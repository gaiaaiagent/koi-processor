#!/usr/bin/env python3
"""Live tests for the deep-extract lease (gaiaaiagent/koi-processor#35).

Covers the two things #35 actually asked for, plus the safety property it warned about:

  1. CONTENTION IS DIAGNOSABLE — a contended caller can tell a healthy holder from a
     wedged one, instead of getting a bare `skipped_locked` that reads as "no work".
  2. A WEDGED HOLDER IS RECLAIMABLE — a lease whose heartbeat has gone stale past the TTL
     can be reclaimed so the lock clears without a manual pkill.
  3. RECLAIM REFUSES TO KILL A HEALTHY HOLDER. This is the important one. #35 warns that
     reclaiming by PID off a stale pg_stat_activity snapshot can terminate a HEALTHY
     backend that has since reused the PID and corrupt a legitimate extract. The check
     must be atomic and identity-verified (pid AND backend_start), and it must refuse
     whenever the holder is still beating.

Also asserts the premise correction: only a PER-DOCUMENT lock exists, so distinct
documents no longer serialize against each other.

Writes only to `deep_extract_lease` rows it creates, and cleans up.

Run:  set -a; source config/personal.env; set +a
      <venv>/bin/python tests/test_deep_extract_lease.py
"""
import asyncio
import importlib.util
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

RID_A = "document:lease-test-aaa"
RID_B = "document:lease-test-bbb"
RESULTS = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


def _load():
    spec = importlib.util.spec_from_file_location(
        "edd", str(REPO / "scripts/extract_deep_documents.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


async def main() -> int:
    import asyncpg
    edd = _load()
    dsn = os.environ["POSTGRES_URL"]
    conn = await asyncpg.connect(dsn)
    holder = await asyncpg.connect(dsn)  # stands in for a second worker process
    try:
        await conn.execute("DELETE FROM deep_extract_lease WHERE document_rid = ANY($1::text[])",
                           [RID_A, RID_B])

        print("0. premise check — the GLOBAL lock #35 describes is gone")
        src = (REPO / "scripts/extract_deep_documents.py").read_text()
        check("deep-extract-doc:global" not in src, "no global lock in the source")
        a = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('deep-extract-doc:' || $1))", RID_A)
        b = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('deep-extract-doc:' || $1))", RID_B)
        check(a and b, "two DISTINCT documents lock concurrently (no fleet serialization)")
        await conn.execute("SELECT pg_advisory_unlock_all()")

        print("\n1. a healthy holder reports as healthy")
        await edd._lease_acquire(holder, RID_A, "test-run")
        st = await edd.lease_status(conn, RID_A)
        check(st.get("holder_alive") is True, "holder seen as alive")
        check(st.get("stale") is False, f"not stale ({st.get('verdict')})")

        print("\n2. reclaim REFUSES to terminate a healthy holder (#35's corruption hazard)")
        r = await edd.reclaim_stale_lease(conn, RID_A)
        check(r.get("reclaimed") is False, f"refused: {r.get('reason')}")
        alive = await holder.fetchval("SELECT 1")
        check(alive == 1, "the healthy holder's connection is still usable (not killed)")

        print("\n3. a wedged holder (heartbeat past TTL) is detected and reclaimable")
        await conn.execute(
            "UPDATE deep_extract_lease SET last_heartbeat = now() - interval '1 hour' "
            "WHERE document_rid = $1", RID_A)
        st = await edd.lease_status(conn, RID_A)
        check(st.get("stale") is True, f"detected as wedged ({st.get('verdict')})")
        holder_pid = await holder.fetchval("SELECT pg_backend_pid()")
        r = await edd.reclaim_stale_lease(conn, RID_A)
        check(r.get("reclaimed") is True, f"reclaimed (terminated pid {r.get('terminated_pid')})")
        check(r.get("terminated_pid") == holder_pid, "terminated the RECORDED holder, not some other pid")
        left = await conn.fetchval("SELECT count(*) FROM deep_extract_lease WHERE document_rid=$1", RID_A)
        check(left == 0, "lease row cleared after reclaim")

        print("\n4. identity check survives PID reuse (backend_start mismatch => not our holder)")
        await conn.execute(
            """INSERT INTO deep_extract_lease
                 (document_rid, holder_pid, holder_backend_start, last_heartbeat)
               VALUES ($1, $2, now() - interval '10 years', now() - interval '1 hour')""",
            RID_B, holder_pid)
        st = await edd.lease_status(conn, RID_B)
        check(st.get("holder_alive") is False,
              "a pid whose backend_start does not match is NOT treated as the holder")
        r = await edd.reclaim_stale_lease(conn, RID_B)
        check(r.get("reclaimed") is True and "already gone" in (r.get("reason") or ""),
              f"cleared as orphan without terminating anything ({r.get('reason')})")

        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 0 if all(RESULTS) else 1
    finally:
        with_suppress = getattr(__import__("contextlib"), "suppress")
        with with_suppress(Exception):
            await conn.execute("DELETE FROM deep_extract_lease WHERE document_rid = ANY($1::text[])",
                               [RID_A, RID_B])
        with with_suppress(Exception):
            await conn.close()
        with with_suppress(Exception):
            await holder.close()
        print("cleaned up")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
