# Source-Specific Extraction Profiles for Regen KOI

**Date**: 2025-12-08
**Status**: Design Specification
**Priority Sources**: Discourse Forums, Notion Pages, Medium Articles

---

## Overview

This document defines extraction profiles tailored to each Regen data source. Each profile specifies:
- **Entity types** commonly found in that source
- **Relationship predicates** appropriate for the content structure
- **System prompt additions** for LLM extraction
- **Special handling** for source-specific patterns

---

## Unified Ontology

All profiles share a common ontology to ensure consistent entity and relationship types across sources.

### Entity Types (Unified)

| Type | Description | Sources |
|------|-------------|---------|
| `PERSON` | Named individuals, forum usernames, authors | All |
| `FORMAL_ORGANIZATION` | Companies, nonprofits, institutions, DAOs | All |
| `COMMUNITY` | Online communities, working groups, collectives | Discourse, Notion |
| `PROJECT` | Specific initiatives, methodologies, programs | All |
| `PROPOSAL` | Governance proposals, feature requests | Discourse |
| `CONCEPT` | Ideas, practices, technologies, movements | All |
| `PLACE` | Geographic locations, ecosystems, regions | All |
| `EVENT` | Conferences, votes, launches, meetings | All |
| `PRODUCT` | Software, credits, publications, tools | All |
| `RESOURCE` | Documents, links, external references | All |
| `METRIC` | Quantitative measures, statistics | Medium, GitHub |
| `API_ENDPOINT` | Technical API routes | GitHub |
| `MODULE` | Software modules, packages | GitHub |

### Relationship Predicates (Unified)

| Predicate | Description | Example |
|-----------|-------------|---------|
| `authored` | Created written content | Person authored Article |
| `proposed` | Submitted for consideration | Person proposed Proposal |
| `supports` | Expresses agreement | Person supports Proposal |
| `opposes` | Expresses disagreement | Person opposes Proposal |
| `works_for` | Employment/affiliation | Person works_for Organization |
| `leads` | Leadership role | Person leads Project |
| `participates_in` | Active involvement | Person participates_in Community |
| `develops` | Technical development | Organization develops Project |
| `implements` | Technical implementation | Module implements Concept |
| `depends_on` | Technical dependency | Module depends_on Module |
| `located_in` | Geographic location | Organization located_in Place |
| `relates_to` | General relationship | Concept relates_to Concept |
| `references` | Cites or links to | Resource references Resource |
| `partners_with` | Business partnership | Organization partners_with Organization |
| `measures` | Quantification | Metric measures Concept |
| `targets` | Goal or objective | Project targets Metric |

---

## Profile 1: Discourse Forums

**Source**: forum.regen.network
**Content Type**: Community discussions, Q&A, governance proposals
**Extraction Priority**: HIGH

### Entity Types (Discourse-Specific)

```json
{
  "entity_types": [
    {
      "type": "PERSON",
      "description": "Forum participants identified by username or full name",
      "examples": ["@ryanchristo", "Gregory Landua", "revett"]
    },
    {
      "type": "PROPOSAL",
      "description": "Governance proposals, signaling proposals, parameter changes",
      "examples": ["Proposal #42", "Carbon Pool Parameters", "Fee Reduction Signaling"]
    },
    {
      "type": "TOPIC",
      "description": "Discussion topics, questions, announcements",
      "examples": ["Validator Set Discussion", "Methodology Development", "SDK Integration"]
    },
    {
      "type": "CONCEPT",
      "description": "Technical concepts, practices, standards discussed",
      "examples": ["ecocredit module", "voluntary carbon market", "baseline methodology"]
    },
    {
      "type": "PROJECT",
      "description": "Regen projects, external projects mentioned",
      "examples": ["Regen Registry", "OpenTEAM", "Cosmos SDK"]
    },
    {
      "type": "ORGANIZATION",
      "description": "Companies, DAOs, partners mentioned in discussions",
      "examples": ["Regen Network Development", "Toucan Protocol", "Verra"]
    }
  ]
}
```

### Relationship Predicates (Discourse-Specific)

```json
{
  "predicates": [
    {
      "predicate": "asked",
      "description": "Posted a question",
      "domain": "PERSON",
      "range": "TOPIC"
    },
    {
      "predicate": "answered",
      "description": "Provided an answer to a question",
      "domain": "PERSON",
      "range": "TOPIC"
    },
    {
      "predicate": "proposed",
      "description": "Submitted a proposal",
      "domain": "PERSON",
      "range": "PROPOSAL"
    },
    {
      "predicate": "supports",
      "description": "Expressed support (via post or reaction)",
      "domain": "PERSON",
      "range": "PROPOSAL"
    },
    {
      "predicate": "opposes",
      "description": "Expressed opposition",
      "domain": "PERSON",
      "range": "PROPOSAL"
    },
    {
      "predicate": "mentioned",
      "description": "Referenced in discussion",
      "domain": "PERSON",
      "range": "CONCEPT|PROJECT|ORGANIZATION"
    },
    {
      "predicate": "quoted",
      "description": "Quoted another user's post",
      "domain": "PERSON",
      "range": "PERSON"
    },
    {
      "predicate": "advocates_for",
      "description": "Actively promotes a concept or approach",
      "domain": "PERSON",
      "range": "CONCEPT"
    }
  ]
}
```

### System Prompt (Discourse)

```
You are extracting entities and relationships from Regen Network forum discussions.

CONTENT TYPE: Community forum post/thread
EXTRACTION FOCUS:
- PEOPLE: Identify forum participants by username (@username) or real name
- PROPOSALS: Track governance proposals and their status
- CONCEPTS: Capture technical concepts, methodologies, standards being discussed
- ORGANIZATIONS: Note companies, DAOs, and partners mentioned
- CONSENSUS SIGNALS: Identify agreement/disagreement patterns

SPECIAL PATTERNS TO DETECT:
1. Quote Attribution: When someone quotes another user, extract "PERSON quoted PERSON"
2. Proposal Support: Phrases like "I support this", "+1", "strong yes" indicate support
3. Proposal Opposition: Phrases like "I disagree", "concerns about", "against this"
4. Questions: Posts ending with "?" or starting with "How", "What", "Why"
5. Announcements: Posts with "[Announcement]", "[Update]", "[RFC]" tags

DO NOT EXTRACT:
- Generic pronouns as entities ("we", "they", "someone")
- Forum UI elements ("Reply", "Like", "Share")
- Timestamps as entities
- Generic phrases ("good idea", "makes sense")
```

### Special Handling (Discourse)

1. **Thread Structure**: Parent chunk = full thread, child chunks = individual replies
2. **Quote Resolution**: Extract quoted usernames and link to original author
3. **Category Context**: Include forum category in metadata (Governance, Development, etc.)
4. **Reaction Analysis**: Map like/heart reactions to implicit support relationships

---

## Profile 2: Notion Pages

**Source**: Regen Network internal Notion workspace
**Content Type**: Strategy documents, partnership notes, project planning
**Extraction Priority**: HIGH

### Entity Types (Notion-Specific)

```json
{
  "entity_types": [
    {
      "type": "STRATEGY",
      "description": "Strategic plans, roadmaps, initiatives",
      "examples": ["2024 Growth Strategy", "Registry Expansion Plan", "Token Economics"]
    },
    {
      "type": "PARTNER",
      "description": "Partner organizations, potential partners",
      "examples": ["Toucan Protocol", "Chainlink", "World Bank"]
    },
    {
      "type": "MILESTONE",
      "description": "Project milestones, deliverables, deadlines",
      "examples": ["Mainnet Launch", "SDK v2.0 Release", "Registry Certification"]
    },
    {
      "type": "INITIATIVE",
      "description": "Specific programs or efforts",
      "examples": ["Validator Onboarding", "Developer Relations", "Grant Program"]
    },
    {
      "type": "MEETING",
      "description": "Meeting notes, calls, workshops",
      "examples": ["Partner Call 2024-01-15", "Strategy Workshop", "Team Standup"]
    },
    {
      "type": "DECISION",
      "description": "Key decisions, outcomes",
      "examples": ["Approved: Fee Structure", "Deferred: Migration Timeline"]
    }
  ]
}
```

### Relationship Predicates (Notion-Specific)

```json
{
  "predicates": [
    {
      "predicate": "proposes",
      "description": "Document proposes an action/direction",
      "domain": "STRATEGY",
      "range": "INITIATIVE"
    },
    {
      "predicate": "partners_with",
      "description": "Partnership relationship",
      "domain": "ORGANIZATION",
      "range": "PARTNER"
    },
    {
      "predicate": "targets",
      "description": "Strategy targets a milestone/goal",
      "domain": "STRATEGY",
      "range": "MILESTONE"
    },
    {
      "predicate": "assigned_to",
      "description": "Task or initiative assigned to person/team",
      "domain": "INITIATIVE",
      "range": "PERSON"
    },
    {
      "predicate": "depends_on",
      "description": "Initiative depends on another",
      "domain": "INITIATIVE",
      "range": "INITIATIVE"
    },
    {
      "predicate": "decided",
      "description": "Meeting produced a decision",
      "domain": "MEETING",
      "range": "DECISION"
    },
    {
      "predicate": "blocks",
      "description": "Issue blocks progress",
      "domain": "CONCEPT",
      "range": "MILESTONE"
    }
  ]
}
```

### System Prompt (Notion)

```
You are extracting entities and relationships from Regen Network internal documentation.

CONTENT TYPE: Internal strategy/planning document
EXTRACTION FOCUS:
- STRATEGIES: High-level plans, roadmaps, OKRs
- PARTNERS: Organizations in partnership discussions
- MILESTONES: Key deliverables with dates
- INITIATIVES: Specific programs or workstreams
- DECISIONS: Outcomes from meetings or discussions

PRIVACY AWARENESS:
- Mark entities with is_internal=true by default
- Flag sensitive content (NDA, confidential, pre-announcement)
- Do not expose partner names in public contexts

SPECIAL PATTERNS TO DETECT:
1. Database Properties: Extract from Notion database fields (Status, Owner, Due Date)
2. Hierarchical Pages: Note parent-child page relationships
3. Action Items: "TODO", "Action:", "Owner:" patterns
4. Decision Log: "Decided:", "Approved:", "Deferred:" patterns

DO NOT EXTRACT:
- Generic pronouns
- Page navigation elements
- Template placeholders ([Insert name])
- Draft content marked as incomplete
```

### Special Handling (Notion)

1. **Privacy Tagging**: All Notion entities tagged `is_private: true` by default
2. **Database Properties**: Extract structured fields from Notion databases
3. **Page Hierarchy**: Preserve parent/child page relationships
4. **Sensitivity Keywords**: Flag "NDA", "confidential", "internal only"

---

## Profile 3: Medium Articles

**Source**: Medium publications by Regen Network
**Content Type**: Long-form explanatory content, announcements, thought leadership
**Extraction Priority**: HIGH

### Entity Types (Medium-Specific)

```json
{
  "entity_types": [
    {
      "type": "ARTICLE",
      "description": "The Medium article itself",
      "examples": ["Regen Registry Launch Announcement", "Carbon Credit Primer"]
    },
    {
      "type": "AUTHOR",
      "description": "Article author(s)",
      "examples": ["Gregory Landua", "Regen Network Team"]
    },
    {
      "type": "CONCEPT",
      "description": "Ideas, practices, technologies explained",
      "examples": ["regenerative agriculture", "nature-based solutions", "MRV"]
    },
    {
      "type": "CASE_STUDY",
      "description": "Specific examples, projects highlighted",
      "examples": ["Wilmot Cattle Co.", "CarbonPath Methodology"]
    },
    {
      "type": "STATISTIC",
      "description": "Quantitative claims with numbers",
      "examples": ["1 million tonnes CO2", "30% improvement", "$5M funding"]
    },
    {
      "type": "CITATION",
      "description": "References to external sources",
      "examples": ["IPCC Report 2023", "Verra Standard VCS"]
    }
  ]
}
```

### Relationship Predicates (Medium-Specific)

```json
{
  "predicates": [
    {
      "predicate": "authored",
      "description": "Author wrote the article",
      "domain": "PERSON",
      "range": "ARTICLE"
    },
    {
      "predicate": "explains",
      "description": "Article explains a concept",
      "domain": "ARTICLE",
      "range": "CONCEPT"
    },
    {
      "predicate": "showcases",
      "description": "Article features a case study",
      "domain": "ARTICLE",
      "range": "CASE_STUDY"
    },
    {
      "predicate": "claims",
      "description": "Article makes a quantitative claim",
      "domain": "ARTICLE",
      "range": "STATISTIC"
    },
    {
      "predicate": "cites",
      "description": "Article references external source",
      "domain": "ARTICLE",
      "range": "CITATION"
    },
    {
      "predicate": "advocates_for",
      "description": "Article promotes a concept/approach",
      "domain": "ARTICLE",
      "range": "CONCEPT"
    },
    {
      "predicate": "announces",
      "description": "Article announces project/event",
      "domain": "ARTICLE",
      "range": "PROJECT|EVENT"
    }
  ]
}
```

### System Prompt (Medium)

```
You are extracting entities and relationships from Regen Network Medium articles.

CONTENT TYPE: Long-form blog article
EXTRACTION FOCUS:
- AUTHOR: Identify article author(s)
- CONCEPTS: Key ideas, technologies, practices explained
- CASE STUDIES: Specific examples, projects, partners highlighted
- STATISTICS: Quantitative claims with numbers
- CITATIONS: External sources referenced

STRUCTURAL PATTERNS:
1. Section Headings: Use to segment concept extraction
2. Bold/Italic Text: Often indicates key terms
3. Links: Extract linked resources as CITATION entities
4. Images with Captions: Extract caption entities

ARTICLE TYPES:
- Announcement: Focus on PROJECT, EVENT, MILESTONE entities
- Explainer: Focus on CONCEPT definitions and relationships
- Case Study: Focus on ORGANIZATION, PROJECT, METRIC entities
- Opinion: Focus on CONCEPT advocacy and PERSON perspectives

DO NOT EXTRACT:
- Generic pronouns
- Medium UI elements (claps, follow, share)
- Boilerplate about Regen Network (extract once, not per article)
- Generic conclusions ("In conclusion...", "To summarize...")
```

### Special Handling (Medium)

1. **Author Attribution**: Every extracted entity linked to article author
2. **Section Context**: Maintain section heading context for disambiguation
3. **Link Expansion**: Resolve Medium links to actual URLs
4. **Publish Date**: Include in provenance for temporal queries

---

## Profile 4: GitHub Documentation

**Source**: regen-network GitHub repositories
**Content Type**: Technical documentation, API specs, README files
**Extraction Priority**: MEDIUM

### Entity Types (GitHub-Specific)

```json
{
  "entity_types": [
    {
      "type": "MODULE",
      "description": "Software modules, packages",
      "examples": ["ecocredit", "data", "group"]
    },
    {
      "type": "FUNCTION",
      "description": "Functions, methods, handlers",
      "examples": ["MsgCreateBatch", "QueryClasses", "HandleMsgSend"]
    },
    {
      "type": "API_ENDPOINT",
      "description": "REST/gRPC endpoints",
      "examples": ["/regen/ecocredit/v1/classes", "/cosmos/bank/v1beta1/balances"]
    },
    {
      "type": "CONFIGURATION",
      "description": "Config parameters, environment variables",
      "examples": ["REGEN_NODE_URL", "CHAIN_ID", "GAS_PRICES"]
    },
    {
      "type": "DATA_SCHEMA",
      "description": "Protobuf messages, JSON schemas",
      "examples": ["BatchInfo", "ClassInfo", "Project"]
    }
  ]
}
```

### Relationship Predicates (GitHub-Specific)

```json
{
  "predicates": [
    {
      "predicate": "implements",
      "description": "Module implements functionality",
      "domain": "MODULE",
      "range": "CONCEPT"
    },
    {
      "predicate": "depends_on",
      "description": "Module depends on another",
      "domain": "MODULE",
      "range": "MODULE"
    },
    {
      "predicate": "exposes",
      "description": "Module exposes API endpoint",
      "domain": "MODULE",
      "range": "API_ENDPOINT"
    },
    {
      "predicate": "configures",
      "description": "Configuration affects module",
      "domain": "CONFIGURATION",
      "range": "MODULE"
    },
    {
      "predicate": "returns",
      "description": "Function returns data type",
      "domain": "FUNCTION",
      "range": "DATA_SCHEMA"
    },
    {
      "predicate": "deprecated_by",
      "description": "Deprecated in favor of newer version",
      "domain": "FUNCTION",
      "range": "FUNCTION"
    }
  ]
}
```

### System Prompt (GitHub)

```
You are extracting entities and relationships from Regen Network technical documentation.

CONTENT TYPE: Technical documentation / README
EXTRACTION FOCUS:
- MODULES: Software modules and their purposes
- FUNCTIONS: Key functions, handlers, messages
- API_ENDPOINTS: REST and gRPC endpoints
- CONFIGURATIONS: Environment variables, parameters
- DATA_SCHEMAS: Protobuf messages, response types

CODE PATTERNS:
1. Code Blocks: Extract function names, types, endpoints
2. Links: Internal links indicate module relationships
3. Tables: Often contain API documentation
4. Deprecation Notices: Track deprecated → replacement relationships

VERSION CONTEXT:
- Note version numbers for deprecated/new features
- Track breaking changes between versions
- Link features to specific releases

DO NOT EXTRACT:
- Generic pronouns
- Boilerplate README sections (Contributing, License)
- Example placeholder values
- Test fixtures
```

---

## Profile 5: Telegram/Discord Messages

**Source**: Regen Network Telegram/Discord channels
**Content Type**: Real-time chat, announcements, quick Q&A
**Extraction Priority**: LOWER

### Entity Types (Chat-Specific)

```json
{
  "entity_types": [
    {
      "type": "PERSON",
      "description": "Chat participants",
      "examples": ["@greglandua", "validator_123"]
    },
    {
      "type": "ANNOUNCEMENT",
      "description": "Official announcements",
      "examples": ["Mainnet upgrade scheduled", "New proposal live"]
    },
    {
      "type": "QUESTION",
      "description": "User questions",
      "examples": ["How do I stake?", "What's the current APR?"]
    },
    {
      "type": "RESOURCE_LINK",
      "description": "Shared links",
      "examples": ["docs.regen.network", "forum.regen.network/t/123"]
    },
    {
      "type": "EVENT",
      "description": "Scheduled events, calls",
      "examples": ["Community Call Friday", "AMA with team"]
    }
  ]
}
```

### System Prompt (Chat)

```
You are extracting entities from Regen Network chat messages.

CONTENT TYPE: Real-time chat messages
EXTRACTION FOCUS:
- PEOPLE: Usernames mentioned or posting
- ANNOUNCEMENTS: Official news, updates
- QUESTIONS: Community questions
- RESOURCE_LINKS: URLs shared
- EVENTS: Scheduled calls, AMAs

CHAT PATTERNS:
1. @Mentions: Extract as PERSON references
2. Emoji Reactions: Interpret as sentiment signals
3. Pinned Messages: Higher importance
4. Thread Replies: Maintain conversation context

NOISE FILTERING:
- Skip greetings ("gm", "hello everyone")
- Skip reactions-only messages
- Skip spam/promotional content
- Skip repeated questions (deduplicate)

DO NOT EXTRACT:
- Single emoji messages
- "Thanks" / "Got it" responses
- Bot commands
- Generic small talk
```

---

## JSON Schema: Extraction Profile

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ExtractionProfile",
  "type": "object",
  "required": ["profile_id", "source_type", "entity_types", "predicates", "system_prompt"],
  "properties": {
    "profile_id": {
      "type": "string",
      "description": "Unique identifier for this profile"
    },
    "source_type": {
      "type": "string",
      "enum": ["discourse", "notion", "medium", "github", "telegram", "twitter"]
    },
    "version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "entity_types": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type", "description"],
        "properties": {
          "type": {"type": "string"},
          "description": {"type": "string"},
          "examples": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "predicates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["predicate", "description"],
        "properties": {
          "predicate": {"type": "string"},
          "description": {"type": "string"},
          "domain": {"type": "string"},
          "range": {"type": "string"}
        }
      }
    },
    "system_prompt": {
      "type": "string",
      "description": "LLM system prompt for this source type"
    },
    "chunking_config": {
      "type": "object",
      "properties": {
        "parent_chunk_size": {"type": "integer"},
        "child_chunk_size": {"type": "integer"},
        "overlap": {"type": "integer"},
        "strategy": {"type": "string", "enum": ["fixed", "semantic", "structural"]}
      }
    },
    "special_handling": {
      "type": "array",
      "items": {"type": "string"}
    }
  }
}
```

---

## Implementation Notes

### Profile Loading

```python
from pathlib import Path
import json
from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class ExtractionProfile:
    profile_id: str
    source_type: str
    entity_types: List[Dict]
    predicates: List[Dict]
    system_prompt: str
    chunking_config: Optional[Dict] = None
    special_handling: Optional[List[str]] = None

    @classmethod
    def load(cls, profile_id: str) -> "ExtractionProfile":
        path = Path(f"data/extraction_profiles/{profile_id}.json")
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    def get_entity_type_names(self) -> List[str]:
        return [et["type"] for et in self.entity_types]

    def get_prompt_with_types(self) -> str:
        types_list = ", ".join(self.get_entity_type_names())
        return f"{self.system_prompt}\n\nALLOWED ENTITY TYPES: {types_list}"
```

### Profile Selection

```python
def get_profile_for_source(source_type: str, metadata: Dict) -> ExtractionProfile:
    """Select appropriate extraction profile based on source."""

    profile_map = {
        "discourse": "discourse_v1",
        "notion": "notion_v1",
        "medium": "medium_v1",
        "github": "github_v1",
        "telegram": "chat_v1",
        "twitter": "social_v1",
    }

    profile_id = profile_map.get(source_type, "generic_v1")
    return ExtractionProfile.load(profile_id)
```

---

## Next Steps

1. **Create JSON profile files** in `data/extraction_profiles/`
2. **Implement ProfileLoader** class
3. **Update LLM extraction** to use profile-specific prompts
4. **Test each profile** on sample data from respective sources
5. **Iterate prompts** based on extraction quality metrics
