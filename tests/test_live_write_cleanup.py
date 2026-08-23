from __future__ import annotations

from pathlib import Path
import subprocess
import uuid

import psycopg2

from tests.live_write_cleanup import append_record, cleanup


TEST_DSN = "postgresql://darrenzal:@localhost:5432/personal_koi_test"


def test_cleanup_removes_exact_recorded_entity_and_edges(tmp_path):
    run_id = uuid.uuid4().hex
    subject_uri = f"orn:test:cleanup-subject-{run_id}"
    object_uri = f"orn:test:cleanup-object-{run_id}"
    predicate = f"test_cleanup_{run_id}"
    manifest = tmp_path / "manifest.jsonl"
    with psycopg2.connect(TEST_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO allowed_predicates (predicate, description) VALUES (%s, 'cleanup test')",
            (predicate,),
        )
        for uri, role in ((subject_uri, "subject"), (object_uri, "object")):
            cur.execute(
                """
                INSERT INTO entity_registry
                    (fuseki_uri, entity_text, entity_type, normalized_text, source)
                VALUES (%s, %s, 'Concept', %s, 'test')
                """,
                (uri, f"Cleanup {role} {run_id}", f"cleanup {role} {run_id}"),
            )
        cur.execute(
            """
            INSERT INTO entity_relationships
                (subject_uri, predicate, object_uri, confidence, source)
            VALUES (%s, %s, %s, 1.0, 'test')
            """,
            (subject_uri, predicate, object_uri),
        )
    append_record(manifest, "entity_uri", subject_uri)
    append_record(manifest, "entity_uri", object_uri)
    report = cleanup(TEST_DSN, manifest, run_id)
    assert report["deleted"]["entity_registry"] == 2
    with psycopg2.connect(TEST_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM entity_registry WHERE fuseki_uri IN (%s, %s)",
            (subject_uri, object_uri),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("DELETE FROM allowed_predicates WHERE predicate = %s", (predicate,))
        cur.execute(
            """
            SELECT count(*) FROM entity_relationships
            WHERE subject_uri IN (%s, %s) OR object_uri IN (%s, %s)
            """,
            (subject_uri, object_uri, subject_uri, object_uri),
        )
        assert cur.fetchone()[0] == 0


def test_shell_guard_refuses_writes_without_explicit_opt_in():
    guard = Path(__file__).with_name("live_write_shell_guard.sh")
    result = subprocess.run(
        ["bash", "-c", f'source "{guard}"; live_write_begin'],
        text=True,
        capture_output=True,
        env={},
    )
    assert result.returncode == 2
    assert "KOI_ALLOW_LIVE_TEST_WRITES=1" in result.stderr
