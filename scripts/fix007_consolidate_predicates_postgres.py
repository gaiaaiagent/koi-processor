#!/usr/bin/env python3
"""
FIX-007: Predicate Consolidation for PostgreSQL koi_relationships
Created: 2025-12-23

Aggressive consolidation strategy to reduce ~3,300 predicates to ~100-200:
1. Canonical predicates: Core predicates (~80) that remain unchanged
2. Direct synonym mappings: Known variants to canonical forms
3. Tense normalization: Past/continuous -> present tense
4. Pattern-based mapping: Compound predicates to simpler forms
5. Semantic fallback: Rare predicates mapped to nearest canonical

Target: 100-200 canonical predicates.
"""

import os
import sys
import re
import json
from datetime import datetime
from collections import Counter
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# ============================================================================
# CANONICAL PREDICATES (~80 core predicates)
# ============================================================================
CANONICAL_PREDICATES = {
    # Core relationships (high frequency)
    "supports", "uses", "mentions", "implements", "includes", "manages",
    "enables", "part_of", "requires", "provides", "associated_with",
    "located_in", "defines", "relates_to", "works_with", "represents",
    "contains", "addresses", "hosts", "validates", "governs",
    "participates_in", "leads", "monitors", "promotes", "performs",
    "focuses_on", "affects", "queries", "updates", "aligns_with",
    "is_a", "targets", "interacts_with", "contributes_to", "improves",
    "operates", "creates", "built_on", "proposes", "authored",

    # Organization/People
    "member_of", "founded", "works_at", "employs", "advises",

    # Process/Action
    "executes", "processes", "generates", "analyzes", "evaluates",
    "measures", "deploys", "maintains", "funds", "connects",

    # Knowledge/Communication
    "discusses", "describes", "explains", "documents", "announces",

    # Regen Domain
    "anchors", "bridges", "delegates", "votes", "credits", "issues",
    "retires", "verifies", "registers", "approves", "mints", "burns",

    # Lifecycle
    "replaces", "upgrades",
}

# ============================================================================
# DIRECT SYNONYM MAPPINGS (variant -> canonical)
# ============================================================================
SYNONYM_MAPPINGS = {
    # === HIGH-FREQUENCY CORE MAPPINGS ===

    # uses (741 base)
    "utilizes": "uses", "employs": "uses", "applies": "uses", "leverages": "uses",
    "used_by": "uses", "used_for": "uses", "using": "uses", "used": "uses",
    "utilize": "uses", "employ": "uses", "apply": "uses", "leverage": "uses",

    # supports (1165 base)
    "helps": "supports", "assists": "supports", "aids": "supports", "backs": "supports",
    "endorses": "supports", "supported": "supports", "supporting": "supports",
    "backed_by": "supports", "assisted_by": "supports", "help": "supports",
    "assist": "supports", "aid": "supports", "back": "supports", "endorse": "supports",

    # mentions (508 base)
    "references": "mentions", "cites": "mentions", "refers_to": "mentions",
    "mentioned": "mentions", "mentioning": "mentions", "noted": "mentions",
    "cited": "mentions", "referenced": "mentions", "reference": "mentions",
    "cite": "mentions", "refer_to": "mentions", "note": "mentions",

    # implements (347 base)
    "carries_out": "implements", "implemented": "implements", "implementing": "implements",
    "realized": "implements", "realizes": "implements", "implement": "implements",
    "carry_out": "implements", "realize": "implements",

    # includes (318 base)
    "comprises": "includes", "incorporates": "includes", "encompasses": "includes",
    "included": "includes", "including": "includes", "consists_of": "includes",
    "composed_of": "includes", "has_component": "includes", "involve": "includes",
    "involves": "includes", "involving": "includes", "entails": "includes",
    "comprise": "includes", "incorporate": "includes", "encompass": "includes",

    # manages (280 base)
    "administers": "manages", "oversees": "manages", "controls": "manages",
    "handles": "manages", "managed": "manages", "managing": "manages",
    "administered": "manages", "managed_by": "manages", "run_by": "manages",
    "operated_by": "manages", "administer": "manages", "oversee": "manages",
    "control": "manages", "handle": "manages",

    # enables (228 base)
    "allows": "enables", "permits": "enables", "empowers": "enables",
    "enabled": "enables", "enabling": "enables", "facilitates": "enables",
    "facilitated": "enables", "facilitating": "enables", "allow": "enables",
    "permit": "enables", "empower": "enables", "facilitate": "enables",

    # part_of (191 base)
    "is_part_of": "part_of", "belongs_to": "part_of", "component_of": "part_of",
    "member_of": "part_of", "included_in": "part_of", "within": "part_of",
    "inside": "part_of", "contained_in": "part_of", "belong_to": "part_of",

    # requires (186 base)
    "needs": "requires", "depends_on": "requires", "required": "requires",
    "requiring": "requires", "needed": "requires", "depends": "requires",
    "dependent_on": "requires", "relies_on": "requires", "need": "requires",
    "depend_on": "requires", "rely_on": "requires",

    # provides (185 base)
    "offers": "provides", "supplies": "provides", "delivers": "provides",
    "gives": "provides", "provided": "provides", "providing": "provides",
    "furnished": "provides", "furnishes": "provides", "offer": "provides",
    "supply": "provides", "deliver": "provides", "give": "provides",

    # associated_with (169 base)
    "related_to": "associated_with", "connected_to": "associated_with",
    "linked_to": "associated_with", "connected_with": "associated_with",
    "associated": "associated_with", "ties_to": "associated_with",
    "relates_to": "associated_with", "links_to": "associated_with",
    "relate_to": "associated_with", "connect_to": "associated_with",
    "link_to": "associated_with", "tie_to": "associated_with",

    # located_in (161 base)
    "based_in": "located_in", "situated_in": "located_in", "located_at": "located_in",
    "is_located_in": "located_in", "positioned_in": "located_in",
    "resides_in": "located_in", "found_in": "located_in", "base_in": "located_in",
    "situate_in": "located_in", "position_in": "located_in", "reside_in": "located_in",

    # defines (159 base)
    "specifies": "defines", "establishes": "defines", "determined": "defines",
    "determining": "defines", "defined": "defines", "defining": "defines",
    "sets": "defines", "specify": "defines", "establish": "defines",
    "determine": "defines", "set": "defines",

    # works_with (136 base)
    "collaborates_with": "works_with", "partners_with": "works_with",
    "cooperates_with": "works_with", "teams_with": "works_with",
    "works_alongside": "works_with", "working_with": "works_with",
    "worked_with": "works_with", "collaborate_with": "works_with",
    "partner_with": "works_with", "cooperate_with": "works_with",

    # represents (111 base)
    "stands_for": "represents", "symbolizes": "represents", "embodies": "represents",
    "represented": "represents", "representing": "represents",
    "denotes": "represents", "signifies": "represents", "stand_for": "represents",
    "symbolize": "represents", "embody": "represents", "denote": "represents",

    # contains (97 base)
    "holds": "contains", "stores": "contains", "has": "contains",
    "contained": "contains", "containing": "contains", "hold": "contains",
    "store": "contains",

    # addresses (94 base)
    "tackles": "addresses", "deals_with": "addresses", "concerns": "addresses",
    "addressed": "addresses", "addressing": "addresses",
    "responds_to": "addresses", "solves": "addresses", "tackle": "addresses",
    "deal_with": "addresses", "concern": "addresses", "respond_to": "addresses",
    "solve": "addresses",

    # hosts (85 base)
    "houses": "hosts", "hosts_on": "hosts", "hosted": "hosts", "hosting": "hosts",
    "house": "hosts",

    # validates (83 base)
    "verifies": "validates", "confirms": "validates", "validated": "validates",
    "validating": "validates", "verified": "validates", "verifying": "validates",
    "checks": "validates", "certifies": "validates", "verify": "validates",
    "confirm": "validates", "check": "validates", "certify": "validates",

    # governs (80 base)
    "regulates": "governs", "rules": "governs", "governed": "governs",
    "governing": "governs", "governed_by": "governs", "regulate": "governs",
    "rule": "governs",

    # proposes (78 base)
    "suggests": "proposes", "recommends": "proposes", "advocates": "proposes",
    "proposed": "proposes", "proposing": "proposes", "advises": "proposes",
    "advised": "proposes", "suggest": "proposes", "recommend": "proposes",
    "advocate": "proposes", "advise": "proposes",

    # interacts_with (69 base)
    "communicates_with": "interacts_with", "engages_with": "interacts_with",
    "interfaces_with": "interacts_with", "interacted": "interacts_with",
    "interacting": "interacts_with", "connects_with": "interacts_with",
    "integrates_with": "interacts_with", "integrates": "interacts_with",
    "integrated": "interacts_with", "integrating": "interacts_with",
    "integrated_with": "interacts_with", "combines_with": "interacts_with",
    "merges_with": "interacts_with", "communicate_with": "interacts_with",
    "engage_with": "interacts_with", "interface_with": "interacts_with",

    # targets (69 base)
    "aimed_at": "targets", "directed_at": "targets", "targeted": "targets",
    "targeting": "targets", "geared_toward": "targets", "aim_at": "targets",
    "direct_at": "targets", "gear_toward": "targets",

    # authored (63 base)
    "wrote": "authored", "written_by": "authored", "co_authored": "authored",
    "author_of": "authored", "writer_of": "authored", "created_by": "authored",
    "write": "authored", "co_author": "authored",

    # participates_in (63 base)
    "engages_in": "participates_in", "involved_in": "participates_in",
    "is_involved_in": "participates_in", "participated_in": "participates_in",
    "takes_part_in": "participates_in", "participating_in": "participates_in",
    "involved": "participates_in", "participates": "participates_in",
    "engage_in": "participates_in", "participate": "participates_in",
    "take_part_in": "participates_in",

    # contributes_to (59 base)
    "adds_to": "contributes_to", "helps_with": "contributes_to",
    "contributed": "contributes_to", "contributing": "contributes_to",
    "contributes": "contributes_to", "add_to": "contributes_to",
    "help_with": "contributes_to", "contribute": "contributes_to",

    # improves (62 base)
    "enhances": "improves", "optimizes": "improves", "improved": "improves",
    "improving": "improves", "betters": "improves", "advances": "improves",
    "upgraded": "improves", "refined": "improves", "enhance": "improves",
    "optimize": "improves", "better": "improves", "advance": "improves",
    "upgrade": "improves", "refine": "improves",

    # leads (55 base)
    "heads": "leads", "directs": "leads", "led": "leads", "leading": "leads",
    "chairs": "leads", "steers": "leads", "guides": "leads", "head": "leads",
    "direct": "leads", "chair": "leads", "steer": "leads", "guide": "leads",

    # monitors (54 base)
    "tracks": "monitors", "observes": "monitors", "watched": "monitors",
    "watches": "monitors", "monitored": "monitors", "monitoring": "monitors",
    "track": "monitors", "observe": "monitors", "watch": "monitors",

    # promotes (54 base)
    "advertises": "promotes", "markets": "promotes", "promoted": "promotes",
    "promoting": "promotes", "advocates_for": "promotes", "pushes": "promotes",
    "advertise": "promotes", "market": "promotes", "advocate_for": "promotes",
    "push": "promotes",

    # performs (53 base)
    "executes": "performs", "does": "performs", "performed": "performs",
    "performing": "performs", "carries_out": "performs", "conducts": "performs",
    "execute": "performs", "do": "performs", "carry_out": "performs",
    "conduct": "performs",

    # focuses_on (52 base)
    "concentrates_on": "focuses_on", "emphasizes": "focuses_on",
    "focused_on": "focuses_on", "focusing_on": "focuses_on",
    "centers_on": "focuses_on", "highlights": "focuses_on",
    "concentrate_on": "focuses_on", "emphasize": "focuses_on",
    "center_on": "focuses_on", "highlight": "focuses_on",

    # affects (51 base)
    "impacts": "affects", "influences": "affects", "affected": "affects",
    "affecting": "affects", "shapes": "affects", "alters": "affects",
    "impact": "affects", "influence": "affects", "shape": "affects", "alter": "affects",

    # updates (46 base)
    "modifies": "updates", "changes": "updates", "updated": "updates",
    "updating": "updates", "amends": "updates", "revises": "updates",
    "revised": "updates", "edited": "updates", "modify": "updates",
    "change": "updates", "amend": "updates", "revise": "updates", "edit": "updates",

    # aligns_with (46 base)
    "aligns": "aligns_with", "aligned_with": "aligns_with",
    "aligning_with": "aligns_with", "conforms_to": "aligns_with",
    "complies_with": "aligns_with", "adheres_to": "aligns_with",
    "align": "aligns_with", "conform_to": "aligns_with",
    "comply_with": "aligns_with", "adhere_to": "aligns_with",

    # is_a (44 base)
    "is_an": "is_a", "is_type": "is_a", "type_of": "is_a", "a_type_of": "is_a",
    "is_of_type": "is_a", "of_type": "is_a", "is": "is_a", "are": "is_a",

    # creates
    "develops": "creates", "builds": "creates", "produces": "creates",
    "generates": "creates", "makes": "creates", "created": "creates",
    "creating": "creates", "developed": "creates", "developing": "creates",
    "built": "creates", "building": "creates", "produced": "creates",
    "made": "creates", "constructed": "creates", "designed": "creates",
    "develop": "creates", "build": "creates", "produce": "creates",
    "make": "creates", "construct": "creates", "design": "creates",

    # founded
    "established": "founded", "started": "founded", "launched": "founded",
    "initiated": "founded", "began": "founded", "originated": "founded",
    "setup": "founded", "set_up": "founded", "establish": "founded",
    "start": "founded", "launch": "founded", "initiate": "founded",
    "begin": "founded", "originate": "founded",

    # operates
    "operates_on": "operates", "operated": "operates", "operating": "operates",
    "functions": "operates", "works_on": "operates", "working_on": "operates",
    "runs": "operates", "running": "operates", "ran": "operates",
    "function": "operates", "work_on": "operates", "run": "operates",

    # built_on
    "built_on_top_of": "built_on", "built_with": "built_on",
    "built_using": "built_on", "constructed_on": "built_on",
    "based_on": "built_on", "founded_on": "built_on",

    # === MEDIUM-FREQUENCY MAPPINGS ===

    # discusses
    "discussed": "discusses", "discussing": "discusses", "talks_about": "discusses",
    "explores": "discusses", "examined": "discusses", "examines": "discusses",
    "reviewed": "discusses", "reviews": "discusses", "discuss": "discusses",
    "talk_about": "discusses", "explore": "discusses", "examine": "discusses",
    "review": "discusses",

    # describes
    "described": "describes", "describing": "describes", "explains": "describes",
    "explained": "describes", "explaining": "describes", "describe": "describes",
    "explain": "describes",

    # analyzes
    "analyzed": "analyzes", "analyzing": "analyzes", "studies": "analyzes",
    "studied": "analyzes", "investigates": "analyzes", "investigated": "analyzes",
    "analyze": "analyzes", "study": "analyzes", "investigate": "analyzes",

    # evaluates
    "evaluated": "evaluates", "evaluating": "evaluates", "assesses": "evaluates",
    "assessed": "evaluates", "judges": "evaluates", "rated": "evaluates",
    "evaluate": "evaluates", "assess": "evaluates", "judge": "evaluates",
    "rate": "evaluates",

    # funds
    "funded": "funds", "funding": "funds", "finances": "funds",
    "financed": "funds", "sponsors": "funds", "sponsored": "funds",
    "fund": "funds", "finance": "funds", "sponsor": "funds",

    # measures
    "measured": "measures", "measuring": "measures", "quantifies": "measures",
    "quantified": "measures", "calculates": "measures", "calculated": "measures",
    "measure": "measures", "quantify": "measures", "calculate": "measures",

    # deploys
    "deployed": "deploys", "deploying": "deploys", "deployed_on": "deploys",
    "deployed_to": "deploys", "runs_on": "deploys", "running_on": "deploys",
    "deploy": "deploys",

    # maintains
    "maintained": "maintains", "maintaining": "maintains", "keeps": "maintains",
    "preserves": "maintains", "preserved": "maintains", "upholds": "maintains",
    "maintain": "maintains", "keep": "maintains", "preserve": "maintains",
    "uphold": "maintains",

    # connects
    "connects_to": "connects", "connected": "connects", "connecting": "connects",
    "connects_with": "connects", "linked": "connects", "links": "connects",
    "connect_to": "connects", "connect": "connects", "link": "connects",

    # replaces
    "replaced": "replaces", "replacing": "replaces", "supersedes": "replaces",
    "superseded": "replaces", "substitutes": "replaces", "replace": "replaces",
    "supersede": "replaces", "substitute": "replaces",

    # prevents
    "prevented": "prevents", "preventing": "prevents", "blocks": "prevents",
    "blocked": "prevents", "stops": "prevents", "stopped": "prevents",
    "prevent": "prevents", "block": "prevents", "stop": "prevents",

    # informs
    "informed": "informs", "informing": "informs", "notifies": "informs",
    "notified": "informs", "tells": "informs", "told": "informs",
    "inform": "informs", "notify": "informs", "tell": "informs",

    # === REGEN DOMAIN ===

    # anchors
    "anchors_data_in": "anchors", "anchors_content_on": "anchors",
    "anchors_hash_of": "anchors", "anchored": "anchors", "anchoring": "anchors",
    "anchor": "anchors",

    # bridges
    "bridges_to": "bridges", "bridged": "bridges", "bridging": "bridges",
    "bridge_to": "bridges", "bridge": "bridges",

    # delegates
    "delegated": "delegates", "delegating": "delegates", "delegates_to": "delegates",
    "delegate_to": "delegates", "delegate": "delegates",

    # votes
    "voted": "votes", "voting": "votes", "votes_on": "votes", "votes_for": "votes",
    "vote_on": "votes", "vote_for": "votes", "vote": "votes",

    # issues
    "issued": "issues", "issuing": "issues", "issues_to": "issues",
    "issue_to": "issues", "issue": "issues",

    # retires
    "retired": "retires", "retiring": "retires", "retire": "retires",

    # registers
    "registered": "registers", "registering": "registers",
    "registered_in": "registers", "registers_with": "registers",
    "register_in": "registers", "register_with": "registers", "register": "registers",

    # approves
    "approved": "approves", "approving": "approves", "accepts": "approves",
    "accepted": "approves", "approve": "approves", "accept": "approves",
}

# ============================================================================
# PATTERN-BASED MAPPINGS (regex -> canonical)
# ============================================================================
PATTERN_MAPPINGS = [
    # acts_as_* -> operates
    (r"^acts_as_.*$", "operates"),
    (r"^acting_as_.*$", "operates"),

    # aims_to_* -> targets
    (r"^aims_to_.*$", "targets"),
    (r"^aim_to_.*$", "targets"),

    # is_* patterns
    (r"^is_registered_in$", "registers"),
    (r"^is_an_allowed.*$", "part_of"),
    (r"^is_a_.*$", "is_a"),
    (r"^is_the_.*$", "is_a"),

    # has_* patterns -> contains
    (r"^has_a_.*$", "contains"),
    (r"^has_.*$", "contains"),

    # can_be_* patterns -> supports
    (r"^can_be_.*$", "supports"),
    (r"^can_.*$", "supports"),

    # should_* patterns
    (r"^should_.*$", "requires"),

    # *_by patterns (passive voice) -> remap to active
    (r"^supported_by$", "supports"),
    (r"^managed_by$", "manages"),
    (r"^created_by$", "authored"),
    (r"^owned_by$", "contains"),
    (r"^used_by$", "uses"),
    (r"^.*_by$", "associated_with"),

    # *_for patterns
    (r"^validates_for$", "validates"),
    (r"^.*_for$", "supports"),

    # *_to patterns
    (r"^connects_to$", "connects"),
    (r"^links_to$", "connects"),
    (r"^.*_to$", "relates_to"),

    # *_with patterns
    (r"^works_with$", "works_with"),
    (r"^integrates_with$", "interacts_with"),
    (r"^.*_with$", "associated_with"),

    # *_in patterns
    (r"^located_in$", "located_in"),
    (r"^participates_in$", "participates_in"),
    (r"^.*_in$", "participates_in"),

    # *_on patterns
    (r"^focuses_on$", "focuses_on"),
    (r"^operates_on$", "operates"),
    (r"^.*_on$", "operates"),

    # *_from patterns
    (r"^derived_from$", "built_on"),
    (r"^.*_from$", "associated_with"),
]


class PredicateConsolidator:
    """Consolidate predicates in PostgreSQL koi_relationships."""

    def __init__(self, dry_run: bool = True):
        self.db_config = {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", 5433)),
            "database": os.getenv("POSTGRES_DB", "eliza"),
            "user": os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        }
        self.dry_run = dry_run
        self.mapping = {}
        self.stats = {
            "original_predicates": 0,
            "consolidated_predicates": 0,
            "relationships_updated": 0,
            "unmapped_predicates": [],
        }

    def connect_db(self):
        return psycopg2.connect(**self.db_config)

    def get_predicate_distribution(self) -> List[Tuple[str, int]]:
        conn = self.connect_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT predicate, COUNT(*) as count
            FROM koi_relationships
            GROUP BY predicate
            ORDER BY count DESC
        """)
        predicates = cursor.fetchall()
        conn.close()
        return predicates

    def build_mapping(self, predicates: List[Tuple[str, int]]) -> Dict[str, str]:
        mapping = {}
        for predicate, count in predicates:
            canonical = self._get_canonical(predicate)
            if canonical != predicate:
                mapping[predicate] = canonical
        return mapping

    def _get_canonical(self, predicate: str) -> str:
        """Get canonical form using multi-tier lookup."""

        # 1. Already canonical
        if predicate in CANONICAL_PREDICATES:
            return predicate

        # 2. Direct synonym mapping
        if predicate in SYNONYM_MAPPINGS:
            return SYNONYM_MAPPINGS[predicate]

        # 3. Tense normalization
        normalized = self._normalize_tense(predicate)
        if normalized != predicate:
            if normalized in CANONICAL_PREDICATES:
                return normalized
            if normalized in SYNONYM_MAPPINGS:
                return SYNONYM_MAPPINGS[normalized]

        # 4. Pattern-based mapping
        for pattern, canonical in PATTERN_MAPPINGS:
            if re.match(pattern, predicate):
                return canonical

        # 5. Return as-is (unmapped)
        return predicate

    def _normalize_tense(self, predicate: str) -> str:
        """Normalize verb tense to present tense base form."""
        # Past tense -ed endings
        if predicate.endswith("ed") and len(predicate) > 4:
            # Double consonant
            if predicate[-3] == predicate[-4]:
                return predicate[:-3]
            # -ied
            if predicate.endswith("ied"):
                return predicate[:-3] + "ies"
            # -ated -> -ates
            if predicate.endswith("ated"):
                return predicate[:-1] + "s"
            # -ed -> base
            return predicate[:-2]

        # Present continuous -ing
        if predicate.endswith("ing") and len(predicate) > 5:
            base = predicate[:-3]
            # -ating -> -ates
            if predicate.endswith("ating"):
                return predicate[:-3] + "es"
            return base + "s"

        return predicate

    def analyze(self):
        print("\n" + "=" * 80)
        print("FIX-007: PREDICATE CONSOLIDATION ANALYSIS")
        print("=" * 80)
        print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE UPDATE'}")
        print(f"Started: {datetime.now().isoformat()}")

        predicates = self.get_predicate_distribution()
        self.stats["original_predicates"] = len(predicates)
        print(f"\n📊 Current predicate count: {len(predicates)}")

        self.mapping = self.build_mapping(predicates)

        # Count unique canonical predicates
        canonical_set = set()
        for pred, count in predicates:
            canonical = self.mapping.get(pred, pred)
            canonical_set.add(canonical)

        self.stats["consolidated_predicates"] = len(canonical_set)

        # Identify unmapped predicates
        unmapped = []
        for pred, count in predicates:
            if pred not in CANONICAL_PREDICATES and pred not in self.mapping:
                unmapped.append((pred, count))

        self.stats["unmapped_predicates"] = unmapped

        # Report
        print(f"\n📊 Consolidation Summary:")
        print(f"   Original predicates: {self.stats['original_predicates']}")
        print(f"   Consolidated predicates: {self.stats['consolidated_predicates']}")
        reduction = self.stats['original_predicates'] - self.stats['consolidated_predicates']
        pct = (1 - self.stats['consolidated_predicates']/self.stats['original_predicates'])*100
        print(f"   Reduction: {reduction} ({pct:.1f}%)")
        print(f"   Mappings created: {len(self.mapping)}")
        print(f"   Unmapped predicates: {len(unmapped)}")

        if unmapped:
            print(f"\n   Top 30 unmapped predicates (by frequency):")
            for pred, count in sorted(unmapped, key=lambda x: x[1], reverse=True)[:30]:
                print(f"      {pred}: {count}")

        return self.mapping

    def apply(self):
        if not self.mapping:
            print("No mapping to apply. Run analyze() first.")
            return

        if self.dry_run:
            print("\n⚠️  DRY RUN - No changes will be made")
            return

        print("\n" + "=" * 80)
        print("APPLYING CONSOLIDATION")
        print("=" * 80)

        conn = self.connect_db()
        cursor = conn.cursor()

        # Build reverse mapping: canonical -> list of variants
        canonical_to_variants = {}
        for old_pred, new_pred in self.mapping.items():
            if new_pred not in canonical_to_variants:
                canonical_to_variants[new_pred] = []
            canonical_to_variants[new_pred].append(old_pred)

        # Step 1: For each canonical, delete duplicates that would be created
        print("\n📋 Step 1: Removing all potential duplicates...")
        duplicates_deleted = 0

        for canonical, variants in canonical_to_variants.items():
            # Get all predicates that will become this canonical (including itself if present)
            all_preds = variants + [canonical]

            # Delete rows where any variant would create a duplicate
            # Keep the canonical version if it exists, otherwise keep first variant
            cursor.execute("""
                WITH to_delete AS (
                    SELECT r.id, r.subject_entity_id, r.predicate, r.object_entity_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY r.subject_entity_id, r.object_entity_id
                               ORDER BY CASE WHEN r.predicate = %s THEN 0 ELSE 1 END, r.id
                           ) as rn
                    FROM koi_relationships r
                    WHERE r.predicate = ANY(%s)
                )
                DELETE FROM koi_relationships
                WHERE id IN (SELECT id FROM to_delete WHERE rn > 1)
            """, (canonical, all_preds))
            deleted = cursor.rowcount
            duplicates_deleted += deleted
            if deleted > 0:
                print(f"   {canonical}: removed {deleted} duplicates from {variants}")

        print(f"✓ Total duplicates removed: {duplicates_deleted}")

        # Step 2: Update remaining rows
        print("\n📋 Step 2: Updating predicates...")
        total_updated = 0

        for old_pred, new_pred in self.mapping.items():
            cursor.execute("""
                UPDATE koi_relationships
                SET predicate = %s
                WHERE predicate = %s
            """, (new_pred, old_pred))
            updated = cursor.rowcount
            total_updated += updated
            if updated > 0:
                print(f"   {old_pred} -> {new_pred}: {updated} rows")

        conn.commit()
        conn.close()

        self.stats["relationships_updated"] = total_updated
        self.stats["duplicates_deleted"] = duplicates_deleted
        print(f"\n✓ Total relationships updated: {total_updated}")
        print(f"✓ Total duplicates removed: {duplicates_deleted}")

    def save_mapping(self, output_path: str):
        output = {
            "generated_at": datetime.now().isoformat(),
            "stats": {
                "original_predicates": self.stats["original_predicates"],
                "consolidated_predicates": self.stats["consolidated_predicates"],
                "mappings_count": len(self.mapping),
            },
            "mapping": self.mapping,
            "unmapped": [{"predicate": p, "count": c} for p, c in self.stats["unmapped_predicates"][:100]],
        }

        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)

        print(f"\n💾 Mapping saved to: {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FIX-007: Predicate Consolidation")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry run)")
    parser.add_argument("--output", default="predicate_consolidation_mapping.json", help="Output file")
    args = parser.parse_args()

    consolidator = PredicateConsolidator(dry_run=not args.apply)
    consolidator.analyze()
    consolidator.save_mapping(args.output)

    if args.apply:
        consolidator.apply()
        print("\n" + "=" * 80)
        print("VERIFICATION")
        print("=" * 80)
        new_predicates = consolidator.get_predicate_distribution()
        print(f"✓ New predicate count: {len(new_predicates)}")

    print(f"\nCompleted: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
