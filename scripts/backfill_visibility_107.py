#!/usr/bin/env python3
"""Standalone backfill for entity_registry.visibility_scope (migration 107).

Stamps the NEW 4-value audience axis (public|team|confidential|unclassified) on
``entity_registry.visibility_scope`` per the corrected classifier rules. This is a
ONE-OFF operator tool — it is NOT wired into the running backend and touches only the
new registry column (never ``entity_rid_mappings.visibility_scope``, the pre-existing
2-value projection-privacy axis, which is a different concept and left untouched).

SAFETY MODEL
------------
  * ``--dry-run`` is the DEFAULT. With no flags the script only SELECTs, prints the
    per-scope plan + the exact rows that would change, and writes NOTHING.
  * ``--apply`` performs the UPDATEs, but ONLY after two hard preconditions pass:
      1. a fresh ``pg_dump`` of ``entity_registry`` exists (``--dump-path``), and
      2. Eve's sign-off token is supplied (``--eve-signoff``) — the Eve-candidate
         hydro utilities are NEVER auto-stamped; her review governs them separately.
    ``--apply`` is intended for LATER human execution, not for this authoring session.
  * A pre-state CSV snapshot of every row the script considers is written BEFORE any
    write, in BOTH modes.

CLASSIFIER RULES (corrected)
----------------------------
  Rule 1  CONFIDENTIAL — a fixed denylist of fuseki_uris (Hydro One + the Layer A/B
          family). Matched by exact fuseki_uri, never by fuzzy name, so an unrelated
          "two-layer architecture" concept can never be swept in.
  Rule 5  TEAM — rows whose ``source`` is an internal/team-authored pipeline
          (knowledge-add, personal-vault, extract-session-entities, obsidian-vault)
          AND currently ``visibility_scope='unclassified'``, EXCLUDING any row that
          is on the confidential denylist or the Eve-candidate list.
  EVE-CANDIDATES — BC Hydro / Bchydro (Org+Person dup) / Hydro-Québec / Manitoba
          Hydro. NEVER stamped by this script. Emitted to a review list only. They
          are held out of Rule 5 even though their source would otherwise qualify.
  CONSERVATIVE-UNCLASSIFIED — email-sensor / proton-email sources and any
          third-party-attendee transcript are left 'unclassified'. This falls out
          naturally: those sources are not in the Rule-5 team-source set, so they are
          never stamped. (Rules 2/3/4 from the spec are un-driveable from ``source``
          alone and are deliberately NOT wired.)

Precedence: confidential > eve-hold > team > unclassified. A row that appears in
both a team source and the confidential/eve set resolves to confidential/eve.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# DSN resolution — mirrors otter_notion/entity_projector.py exactly.
# --------------------------------------------------------------------------- #
DEFAULT_PG_DSN = "postgresql://darrenzal@localhost:5432/personal_koi"


def _dotenv_value(key: str) -> str | None:
    """Best-effort read of KEY from a nearby .env, without importing dotenv."""
    for candidate in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ):
        try:
            with open(candidate, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() == key:
                        return v.strip().strip("'").strip('"')
        except FileNotFoundError:
            continue
    return None


def resolve_dsn(explicit: str | None) -> str:
    return (
        explicit
        or os.environ.get("PERSONAL_KOI_PG")
        or _dotenv_value("PERSONAL_KOI_PG")
        or DEFAULT_PG_DSN
    )


# --------------------------------------------------------------------------- #
# Classifier sets — matched by EXACT fuseki_uri (never fuzzy name).
#
# These uris were resolved read-only from the live registry on 2026-07-14. If the
# registry changes, re-derive them; a missing uri is simply a no-op (0 rows).
# --------------------------------------------------------------------------- #

# Rule 1: confidential denylist (Hydro One + Layer A/B family).
CONFIDENTIAL_URIS: tuple[str, ...] = (
    "orn:personal-koi.entity:organization-hydro-one-06e352ca9fb3",   # Hydro One
    "orn:personal-koi.entity:concept-layer-a-ac555b67fb31",          # Layer A
    "orn:personal-koi.entity:concept-layer-b-1e09d73a7855",          # Layer B
    "orn:personal-koi.entity:organization-layer-b-ci-d7cbcfc264ec",  # Layer B CI
    "orn:personal-koi.entity:project-layer-b-pipeline-04ac0a419fee",  # Layer B Pipeline
    # "Layer B Expansion Opportunity" is on the projection_config denylist but has no
    # registry row today; kept here as documentation. Add its uri if one appears.
)

# Eve-review candidates — NEVER auto-stamped. Held out of Rule 5 and emitted to a
# review list. Carol Anne's consulting dataset touches Indigenous-nation hydro
# participation; whether these utilities are confidential is Eve's call, not ours.
EVE_CANDIDATE_URIS: tuple[str, ...] = (
    "orn:personal-koi.entity:person-bc-hydro-d17114da6cf9",          # BC Hydro (Person dup)
    "orn:personal-koi.entity:organization-bchydro-917b7deb1451",     # Bchydro (Org dup)
    "orn:personal-koi.entity:organization-hydro-qu-bec-50d91d3692cf",  # Hydro-Québec
    "orn:personal-koi.entity:organization-manitoba-hydro-7ea8181f2605",  # Manitoba Hydro
)

# Rule 5: team-authored source pipelines.
TEAM_SOURCES: tuple[str, ...] = (
    "knowledge-add",
    "personal-vault",
    "extract-session-entities",
    "obsidian-vault",
)

# --------------------------------------------------------------------------- #
# Static-URI DRIFT GUARD — re-derive the confidential + Eve sets BY NAME.
#
# The URI tuples above were frozen on 2026-07-14. An entity ingested AFTER that
# date (e.g. a fresh "Hydro One" mention that resolves to a NEW fuseki_uri) would
# be invisible to a URI-only match and could leak. To close that gap, --apply
# RE-DERIVES the sets by EXACT (case-insensitive) name and UNIONs them with the
# frozen tuples.
#
# Matching is EXACT name/alias EQUALITY — never a substring/fuzzy — so an unrelated
# "two-layer architecture" concept is NEVER swept in. This preserves the original
# safety property (the reason the sets were URI-pinned) while catching drift.
# --------------------------------------------------------------------------- #
CONFIDENTIAL_NAMES: tuple[str, ...] = (
    "Hydro One",
    "Layer A",
    "Layer B",
    "Layer B CI",
    "Layer B Pipeline",
    "Layer B Expansion Opportunity",
)

EVE_CANDIDATE_NAMES: tuple[str, ...] = (
    "BC Hydro",
    "Bchydro",
    "Hydro-Québec",
    "Hydro-Quebec",
    "Manitoba Hydro",
)


def _derive_uris_by_name(cur, names: tuple[str, ...]) -> set[str]:
    """Return fuseki_uris whose entity_text OR an alias EXACTLY (case-insensitively)
    equals one of ``names``. Exact equality only — no substring/fuzzy match."""
    if not names:
        return set()
    lowered = [n.strip().lower() for n in names if n and n.strip()]
    if not lowered:
        return set()
    cur.execute(
        """
        SELECT DISTINCT fuseki_uri
          FROM entity_registry
         WHERE lower(entity_text) = ANY(%s)
            OR EXISTS (
                 SELECT 1
                   FROM unnest(COALESCE(aliases, ARRAY[]::text[])) AS a
                  WHERE lower(a) = ANY(%s)
               )
        """,
        (lowered, lowered),
    )
    return {r[0] for r in cur.fetchall()}


def derive_effective_sets(cur) -> tuple[set[str], set[str], dict]:
    """RE-DERIVE (effective_confidential, effective_eve, drift_report) at run time.

    Effective sets = frozen tuples ∪ name-derived URIs. Precedence confidential >
    eve-hold, so any URI that is confidential-by-name is removed from the eve set.
    ``drift_report`` lists URIs newly caught by name that are NOT in the frozen
    tuples — i.e. entities ingested after the 2026-07-14 freeze.
    """
    conf_by_name = _derive_uris_by_name(cur, CONFIDENTIAL_NAMES)
    eve_by_name = _derive_uris_by_name(cur, EVE_CANDIDATE_NAMES)

    conf_uris = set(CONFIDENTIAL_URIS) | conf_by_name
    eve_uris = (set(EVE_CANDIDATE_URIS) | eve_by_name) - conf_uris

    drift = {
        "confidential_new": sorted(conf_by_name - set(CONFIDENTIAL_URIS)),
        "eve_new": sorted(eve_by_name - set(EVE_CANDIDATE_URIS) - conf_uris),
    }
    return conf_uris, eve_uris, drift


def _existing_uris(cur, uris: tuple[str, ...]) -> set[str]:
    """Subset of ``uris`` that still exist in entity_registry today."""
    if not uris:
        return set()
    cur.execute(
        "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = ANY(%s)",
        (list(uris),),
    )
    return {r[0] for r in cur.fetchall()}


# --------------------------------------------------------------------------- #
# DB helpers (read-only unless --apply, and even then guarded).
# --------------------------------------------------------------------------- #
def _connect(dsn: str):
    import psycopg2  # lazy import

    conn = psycopg2.connect(dsn)
    return conn


def _fetch_rows(cur, uris: tuple[str, ...]) -> list[tuple]:
    """Return (fuseki_uri, entity_text, entity_type, source, visibility_scope) for the
    given uris that are CURRENTLY 'unclassified' (the only rows we would ever change)."""
    if not uris:
        return []
    cur.execute(
        """
        SELECT fuseki_uri, entity_text, entity_type, source, visibility_scope
          FROM entity_registry
         WHERE fuseki_uri = ANY(%s)
           AND visibility_scope = 'unclassified'
         ORDER BY entity_text
        """,
        (list(uris),),
    )
    return cur.fetchall()


def _fetch_eve(cur, uris: tuple[str, ...]) -> list[tuple]:
    """Eve candidates — report their CURRENT scope regardless (should be unclassified)."""
    if not uris:
        return []
    cur.execute(
        """
        SELECT fuseki_uri, entity_text, entity_type, source, visibility_scope
          FROM entity_registry
         WHERE fuseki_uri = ANY(%s)
         ORDER BY entity_text
        """,
        (list(uris),),
    )
    return cur.fetchall()


def _fetch_team_rows(cur, conf_uris, eve_uris, limit: int | None) -> list[tuple]:
    """Rule-5 team rows: unclassified + team source, minus confidential + eve holds."""
    sql = """
        SELECT fuseki_uri, entity_text, entity_type, source, visibility_scope
          FROM entity_registry
         WHERE visibility_scope = 'unclassified'
           AND source = ANY(%s)
           AND fuseki_uri <> ALL(%s)
           AND fuseki_uri <> ALL(%s)
         ORDER BY source, entity_text
    """
    params = [
        list(TEAM_SOURCES),
        list(conf_uris),
        list(eve_uris),
    ]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def _count_team(cur, conf_uris, eve_uris) -> int:
    cur.execute(
        """
        SELECT count(*)
          FROM entity_registry
         WHERE visibility_scope = 'unclassified'
           AND source = ANY(%s)
           AND fuseki_uri <> ALL(%s)
           AND fuseki_uri <> ALL(%s)
        """,
        (list(TEAM_SOURCES), list(conf_uris), list(eve_uris)),
    )
    return cur.fetchone()[0]


def _count_scope(cur, scope: str) -> int:
    cur.execute(
        "SELECT count(*) FROM entity_registry WHERE visibility_scope = %s", (scope,)
    )
    return cur.fetchone()[0]


def _total(cur) -> int:
    cur.execute("SELECT count(*) FROM entity_registry")
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
def write_snapshot(cur, path: Path, conf_uris, eve_uris) -> int:
    """Write a pre-state CSV of every row the script considers (confidential + eve +
    all team-source-unclassified rows). Returns the row count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    considered: dict[str, tuple] = {}
    for row in _fetch_rows(cur, tuple(conf_uris)):
        considered[row[0]] = row + ("rule1_confidential",)
    for row in _fetch_eve(cur, tuple(eve_uris)):
        considered.setdefault(row[0], row + ("eve_hold",))
    for row in _fetch_team_rows(cur, conf_uris, eve_uris, limit=None):
        considered.setdefault(row[0], row + ("rule5_team",))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "fuseki_uri",
                "entity_text",
                "entity_type",
                "source",
                "visibility_scope_before",
                "planned_disposition",
            ]
        )
        for uri in sorted(considered):
            w.writerow(considered[uri])
    return len(considered)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_plan(cur, conf_uris, eve_uris, drift=None) -> None:
    conf = _fetch_rows(cur, tuple(conf_uris))
    eve = _fetch_eve(cur, tuple(eve_uris))
    team_n = _count_team(cur, conf_uris, eve_uris)
    total = _total(cur)
    unclassified_now = _count_scope(cur, "unclassified")
    # Post-plan unclassified = current unclassified - team - confidential-being-changed.
    stays_unclassified = unclassified_now - team_n - len(conf)

    print("=" * 72)
    print("VISIBILITY BACKFILL PLAN (migration 107)  — resulting scope counts")
    print("=" * 72)
    print(f"  total entity_registry rows : {total}")
    print(f"  currently 'unclassified'   : {unclassified_now}")
    print("  ---")
    print(f"  → confidential (Rule 1)    : {len(conf)}")
    print(f"  → team         (Rule 5)    : {team_n}")
    print(f"  → stays unclassified       : {stays_unclassified}")
    print(f"     (incl. {len(eve)} Eve-candidates held out of team)")
    print()

    print("CONFIDENTIAL (Rule 1) — exact rows to be stamped:")
    if not conf:
        print("    (none currently unclassified)")
    for uri, text, etype, source, scope in conf:
        print(f"    [{scope}→confidential] {text}  <{etype}>  src={source}")
        print(f"        {uri}")
    print()

    print("EVE-REVIEW CANDIDATES — NOT stamped, routed to Eve:")
    if not eve:
        print("    (none found)")
    for uri, text, etype, source, scope in eve:
        print(f"    [held @ {scope}] {text}  <{etype}>  src={source}")
        print(f"        {uri}")
    print()

    print(f"TEAM (Rule 5) — {team_n} rows; first 25 shown:")
    for uri, text, etype, source, scope in _fetch_team_rows(cur, conf_uris, eve_uris, limit=25):
        print(f"    [{scope}→team] {text}  <{etype}>  src={source}")
    if team_n > 25:
        print(f"    … and {team_n - 25} more (see snapshot CSV for the full list)")

    if drift:
        new_conf = drift.get("confidential_new") or []
        new_eve = drift.get("eve_new") or []
        print()
        print("NAME-DERIVED DRIFT (caught by name, NOT in the 2026-07-14 frozen URIs):")
        if not new_conf and not new_eve:
            print("    (none — frozen URI lists still cover every name-matched row)")
        for uri in new_conf:
            print(f"    [+confidential] {uri}")
        for uri in new_eve:
            print(f"    [+eve-hold]     {uri}")
    print("=" * 72)


# --------------------------------------------------------------------------- #
# Apply (guarded; for LATER human execution)
# --------------------------------------------------------------------------- #
def apply_backfill(conn, cur, args, conf_uris, eve_uris, drift) -> None:
    # --- Precondition 1: fresh pg_dump must exist. -------------------------- #
    if not args.dump_path:
        sys.exit("REFUSED: --apply requires --dump-path pointing at a fresh "
                 "pg_dump of entity_registry (rollback safety).")
    dump = Path(args.dump_path)
    if not dump.is_file() or dump.stat().st_size == 0:
        sys.exit(f"REFUSED: --dump-path {dump} is missing or empty. Run e.g.\n"
                 f"    pg_dump -t entity_registry <db> > {dump}")

    # --- Precondition 2: Eve sign-off token. -------------------------------- #
    if args.eve_signoff != "EVE-SIGNED-OFF":
        sys.exit("REFUSED: --apply requires --eve-signoff EVE-SIGNED-OFF, confirming "
                 "Eve has reviewed the hydro-utility candidate list. She governs "
                 "whether BC Hydro / Bchydro / Hydro-Québec / Manitoba Hydro are "
                 "confidential; this script never stamps them.")

    # --- Precondition 3: name-based drift verification. --------------------- #
    # The confidential/eve sets used below were RE-DERIVED by name at run time (see
    # derive_effective_sets). This guard catches the two ways name-derivation can be
    # unsafe at apply time, and forces a human to reconcile rather than silently
    # applying a stale plan:
    #   (a) any frozen confidential URI that STILL exists in the registry but is NOT
    #       re-discovered by name → the CONFIDENTIAL_NAMES list has drifted out of
    #       sync with the data and can no longer be trusted to catch new rows;
    #   (b) any Eve-hold candidate the name pass now classifies as confidential →
    #       precedence collision that Eve must adjudicate before we stamp.
    conf_by_name = _derive_uris_by_name(cur, CONFIDENTIAL_NAMES)
    still_present_frozen = _existing_uris(cur, CONFIDENTIAL_URIS)
    unmatched_frozen = still_present_frozen - conf_by_name
    if unmatched_frozen:
        sys.exit(
            "REFUSED: name-based verification failed. These frozen confidential URIs "
            "still exist but were NOT re-discovered by CONFIDENTIAL_NAMES — the name "
            "list has drifted and can no longer be trusted to catch newly-ingested "
            "rows. Reconcile CONFIDENTIAL_NAMES before applying:\n    "
            + "\n    ".join(sorted(unmatched_frozen))
        )
    eve_now_confidential = (set(EVE_CANDIDATE_URIS) | _derive_uris_by_name(cur, EVE_CANDIDATE_NAMES)) & conf_by_name
    if eve_now_confidential:
        sys.exit(
            "REFUSED: an Eve-hold candidate is now classified confidential by name "
            "(precedence collision). Eve must adjudicate before applying:\n    "
            + "\n    ".join(sorted(eve_now_confidential))
        )

    new_conf = (drift or {}).get("confidential_new") or []
    new_eve = (drift or {}).get("eve_new") or []
    print(f"[apply] preconditions OK (dump={dump}, eve sign-off present, "
          f"name-verification passed).")
    print(f"[apply] effective sets (frozen ∪ name-derived): "
          f"{len(conf_uris)} confidential, {len(eve_uris)} eve-hold.")
    if new_conf or new_eve:
        print(f"[apply] DRIFT caught since 2026-07-14 freeze — will be stamped: "
              f"{len(new_conf)} new confidential, {len(new_eve)} new eve-hold (held).")
    print("[apply] running UPDATEs inside a single transaction …")

    # Confidential first (precedence), then team. Both scoped to 'unclassified' so a
    # re-run is idempotent and can never downgrade an already-classified row.
    # Uses the RE-DERIVED effective sets, so rows ingested after the freeze are caught.
    cur.execute(
        """
        UPDATE entity_registry
           SET visibility_scope = 'confidential', updated_at = now()
         WHERE fuseki_uri = ANY(%s)
           AND visibility_scope = 'unclassified'
        """,
        (list(conf_uris),),
    )
    conf_n = cur.rowcount
    cur.execute(
        """
        UPDATE entity_registry
           SET visibility_scope = 'team', updated_at = now()
         WHERE visibility_scope = 'unclassified'
           AND source = ANY(%s)
           AND fuseki_uri <> ALL(%s)
           AND fuseki_uri <> ALL(%s)
        """,
        (list(TEAM_SOURCES), list(conf_uris), list(eve_uris)),
    )
    team_n = cur.rowcount
    conn.commit()
    print(f"[apply] committed: {conf_n} → confidential, {team_n} → team. "
          f"Eve candidates untouched.")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill entity_registry.visibility_scope (migration 107). "
        "Dry-run by default; --apply is guarded for later human execution.",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="(default) SELECT-only; print the plan, write nothing.")
    mode.add_argument("--apply", action="store_true",
                      help="Perform the UPDATEs. Requires --dump-path + --eve-signoff.")
    ap.add_argument("--dsn", default=None,
                    help="Postgres DSN (default: PERSONAL_KOI_PG env / .env / localhost).")
    ap.add_argument("--snapshot",
                    default=f"visibility_107_prestate_"
                            f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv",
                    help="Path for the pre-state CSV snapshot (written in both modes).")
    ap.add_argument("--dump-path", default=None,
                    help="[--apply only] path to a fresh pg_dump of entity_registry.")
    ap.add_argument("--eve-signoff", default=None,
                    help="[--apply only] pass EVE-SIGNED-OFF to confirm Eve reviewed "
                         "the hydro-utility candidate list.")
    args = ap.parse_args(argv)

    applying = bool(args.apply)
    dsn = resolve_dsn(args.dsn)
    print(f"[backfill_visibility_107] DSN={dsn}  mode={'APPLY' if applying else 'DRY-RUN'}")

    conn = _connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # RE-DERIVE the confidential + eve sets by name (read-only SELECTs), so
            # both the plan/snapshot AND the apply reflect rows ingested after the
            # 2026-07-14 freeze — not just the frozen URI tuples.
            conf_uris, eve_uris, drift = derive_effective_sets(cur)
            if drift.get("confidential_new") or drift.get("eve_new"):
                print(f"[derive] name-drift caught since freeze: "
                      f"{len(drift['confidential_new'])} confidential, "
                      f"{len(drift['eve_new'])} eve-hold (not in frozen URIs).")

            # Snapshot pre-state BEFORE any possible write.
            n = write_snapshot(cur, Path(args.snapshot), conf_uris, eve_uris)
            print(f"[snapshot] wrote {n} considered rows → {args.snapshot}")

            print_plan(cur, conf_uris, eve_uris, drift)

            if applying:
                apply_backfill(conn, cur, args, conf_uris, eve_uris, drift)
            else:
                print("\nDRY-RUN: no rows changed. Re-run with --apply "
                      "(plus --dump-path and --eve-signoff) to execute.")
    finally:
        conn.rollback()  # discard anything uncommitted in dry-run
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
