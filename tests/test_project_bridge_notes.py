import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_bridge_notes.py"


def load_projector():
    spec = importlib.util.spec_from_file_location("project_bridge_notes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_note(tmp_path, body):
    path = tmp_path / "bridge-note.md"
    path.write_text(
        """---
doc_id: test.connection.note
doc_kind: research
status: draft
research_subkind: bridge_note
disposition: implementation hypothesis
concepts:
  - memory-governance
depends_on:
  - ic.intelligence-primitives
relates_to: []
---

"""
        + body
    )
    return path


def test_parse_v2_review_claims_and_stop_c_claim_body(tmp_path):
    projector = load_projector()
    path = write_note(
        tmp_path,
        """# Test

## Claim Register

**C1** [confidence: high] [anchor: section 1]
The source claim should not absorb the following review claim.

**C2** [confidence: medium] [anchor: section 2]
The second source claim supports the same review target.

- **R1**: Add a transformation-edge audit lane. [target: ic.project-learning-membrane] [concept: memory-governance] TODO: slug-deferred
  supported_by: C1, C2.
""",
    )

    note = projector.parse_bridge_note(path, "ic")
    report = projector.build_parse_report(note, path.read_text())

    assert [claim.c_id for claim in note.claims] == ["C1", "C2"]
    assert "**R1**" not in note.claims[-1].statement
    assert len(note.review_directives) == 1
    directive = note.review_directives[0]
    assert directive.r_id == "R1"
    assert directive.target_doc == "ic.project-learning-membrane"
    assert directive.concept == "memory-governance"
    assert directive.supported_by == ["C1", "C2"]
    assert directive.statement == "Add a transformation-edge audit lane."
    assert report.issues == []


def test_parse_legacy_review_claims(tmp_path):
    projector = load_projector()
    path = write_note(
        tmp_path,
        """# Test

## Claim Register

**C1** [confidence: high] [anchor: theorem]
Legacy source claim.

**R1** [review claim] [target: ic.intelligence-primitives] [concept: memory-governance]
The Memory primitive should mention freshness budgets.
*R1 is supported by C1.*
""",
    )

    note = projector.parse_bridge_note(path, "ic")
    report = projector.build_parse_report(note, path.read_text())

    assert len(note.claims) == 1
    assert len(note.review_directives) == 1
    directive = note.review_directives[0]
    assert directive.r_id == "R1"
    assert directive.target_doc == "ic.intelligence-primitives"
    assert directive.concept == "memory-governance"
    assert directive.supported_by == ["C1"]
    assert directive.statement == "The Memory primitive should mention freshness budgets."
    assert report.issues == []


def test_parse_report_flags_missing_concept(tmp_path):
    projector = load_projector()
    path = write_note(
        tmp_path,
        """# Test

## Claim Register

**C1** [confidence: high] [anchor: section]
Source claim.

- **R1**: Add an audit lane. [target: ic.project-learning-membrane]
  supported_by: C1.
""",
    )

    note = projector.parse_bridge_note(path, "ic")
    report = projector.build_parse_report(note, path.read_text())

    assert note.review_directives == []
    assert any(issue.code == "missing_concept" for issue in report.issues)
    assert any(issue.severity == "error" for issue in report.issues)


def test_parse_report_flags_unknown_support_ref(tmp_path):
    projector = load_projector()
    path = write_note(
        tmp_path,
        """# Test

## Claim Register

**C1** [confidence: high] [anchor: section]
Source claim.

- **R1**: Add an audit lane. [target: ic.project-learning-membrane] [concept: memory-governance]
  supported_by: C9.
""",
    )

    note = projector.parse_bridge_note(path, "ic")
    report = projector.build_parse_report(note, path.read_text())

    assert len(note.review_directives) == 1
    assert any(issue.code == "unknown_support_ref" for issue in report.issues)


def test_parse_report_cli_does_not_require_db(tmp_path):
    path = write_note(
        tmp_path,
        """# Test

## Claim Register

**C1** [confidence: high] [anchor: section]
Source claim.

- **R1**: Add an audit lane. [target: ic.project-learning-membrane] [concept: memory-governance]
  supported_by: C1.
""",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--parse-report", "--note", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "C-claims parsed: 1" in result.stdout
    assert "R-claims parsed: 1" in result.stdout
    assert "Issues: none" in result.stdout


def test_claims_service_token_prefers_env(monkeypatch, tmp_path):
    projector = load_projector()
    monkeypatch.setenv("KOI_CLAIMS_SERVICE_TOKEN", "env-token")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert projector._claims_service_token() == "env-token"


def test_claims_service_token_reads_state_file(monkeypatch, tmp_path):
    projector = load_projector()
    monkeypatch.delenv("KOI_CLAIMS_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    token_path = tmp_path / ".config/personal-koi/koi-state/claims_service_token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("state-token\n")

    assert projector._claims_service_token() == "state-token"


def test_projector_knows_current_intake_dispositions():
    projector = load_projector()

    assert projector.DISPOSITION_SLUG["candidate protocol"] == "propose-protocol"
    assert projector.DISPOSITION_SLUG["novel synthesis"] == "synthesize"


def test_create_review_claim_raises_on_auth_failure():
    projector = load_projector()

    class Response:
        status_code = 401
        text = "auth required"

    class Client:
        async def post(self, *args, **kwargs):
            return Response()

    async def run():
        with pytest.raises(RuntimeError, match="/claims auth failed"):
            await projector.create_review_claim(
                Client(),
                conn=object(),
                claimant_uri="org:ic-learning-field",
                concept_name="memory-governance",
                about_uri="concept:memory-governance",
                target_spec_doc="ic.memory-layers",
                disposition_slug="implementation-hypothesis",
                project_uri="project:intelligence-commons",
                projection_batch="test",
            )

    asyncio.run(run())
