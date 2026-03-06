"""Central capabilities registry for KOI runtime convergence.

Each deployment (personal, bkc_coordinator, bkc_leaf) enables a different
subset of features.  Capabilities can be set via environment variables or
by selecting a named profile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(var_name: str, default: bool = False) -> bool:
    """Read a boolean flag from an environment variable."""
    val = os.environ.get(var_name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


@dataclass(frozen=True)
class Capabilities:
    web_sensor: bool = False
    github_sensor: bool = False
    mediawiki_sensor: bool = False
    llm_enrichment: bool = False
    vault_sync: bool = False
    terminusdb: bool = False
    pipeline: bool = False
    graph_queries: bool = True          # pure SQL, no deps — always on
    coordinator_endpoints: bool = False
    assertion_history: bool = True      # ADR-001 core invariant

    deployment_profile: str = "personal"

    # -- constructors --------------------------------------------------------

    @classmethod
    def from_env(cls) -> Capabilities:
        """Build capabilities from environment variables."""
        profile = os.environ.get("DEPLOYMENT_PROFILE", "personal").strip()
        return cls(
            web_sensor=_flag("WEB_SENSOR_ENABLED"),
            github_sensor=_flag("GITHUB_SENSOR_ENABLED"),
            mediawiki_sensor=_flag("MEDIAWIKI_SENSOR_ENABLED"),
            llm_enrichment=_flag("LLM_ENRICHMENT_ENABLED"),
            vault_sync=_flag("VAULT_SYNC_ENABLED"),
            terminusdb=_flag("TERMINUSDB_ENABLED"),
            pipeline=_flag("PIPELINE_ENABLED"),
            graph_queries=_flag("GRAPH_QUERIES_ENABLED", default=True),
            coordinator_endpoints=_flag("COORDINATOR_ENDPOINTS_ENABLED"),
            assertion_history=_flag("ASSERTION_HISTORY_ENABLED", default=True),
            deployment_profile=profile,
        )

    @classmethod
    def from_profile(cls, profile: str) -> Capabilities:
        """Return sensible defaults for a named deployment profile."""
        profiles = {
            "personal": cls(
                vault_sync=True,
                terminusdb=False,
                graph_queries=True,
                assertion_history=True,
                deployment_profile="personal",
            ),
            "bkc_coordinator": cls(
                web_sensor=True,
                github_sensor=True,
                mediawiki_sensor=True,
                llm_enrichment=True,
                pipeline=True,
                coordinator_endpoints=True,
                graph_queries=True,
                assertion_history=True,
                deployment_profile="bkc_coordinator",
            ),
            "bkc_leaf": cls(
                graph_queries=True,
                assertion_history=True,
                deployment_profile="bkc_leaf",
            ),
        }
        if profile not in profiles:
            raise ValueError(
                f"Unknown profile {profile!r}. "
                f"Choose from: {', '.join(sorted(profiles))}"
            )
        return profiles[profile]
