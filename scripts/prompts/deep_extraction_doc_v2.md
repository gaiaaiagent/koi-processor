# Deep Document Extraction — v2.0 Prompt (Phase 3: entities + facts + discourse)

You are analyzing a long-form document (whitepaper, paper, report, essay, or spec)
to extract structured knowledge for a knowledge graph. This is non-interactive:
return exactly one JSON object, nothing else.

You are seeing **window {WINDOW_INDEX} of {WINDOW_COUNT}** of the document. Extract
only what THIS window's text supports. The text is labeled with **global chunk
indices** `[N]` — use them verbatim (do not renumber from zero).

## OUTPUT

Return ONE JSON object matching the schema in the APPENDIX. NO prose. NO markdown
fences. NO explanation. The entire response must be valid JSON starting with `{`
and ending with `}`. If unsure about a field, emit `null` rather than omitting it.
Every required field in the schema must appear.

## EXTRACTION RULES (per layer)

### Entities (identity layer)

Extract named, knowledge-worthy entities: people, organizations, projects,
concepts/frameworks, places, protocols, and prior-example case studies.

**Type each entity as exactly one of:** `Person`, `Organization`, `Project`,
`Concept`, `Location`, `Protocol`, `CaseStudy`. Canonicalization rules — follow
these exactly; they prevent the most common mis-types:

- **A human full name is `Person`** — never `Concept` or `Project` — even when
  cited as the author, originator, reviewer, or interviewee of an idea.
  (e.g. "Ernesto van Peborgh" is a `Person`, not the name of a framework.)
- **An institution, school, initiative, programme, fund, or lab is `Organization`**
  — never type a multi-word initiative as a `Person`. Prefer the **most complete
  surface form** (e.g. `Design School for Regenerating Earth`, not `Design School`).
- **A prior real-world example / precedent site** the document cites as a model
  (e.g. Auroville, Findhorn, SEKEM, Crystal Waters) is a `CaseStudy`.
- **A geographic place** (region, city, country, bioregion, watershed) is `Location`.
- A named methodology, standard, or interoperability contract is `Protocol`;
  a named idea/framework/outcome is `Concept`; a named built initiative/site/venture
  is `Project`.

`first_seen_chunk` is the lowest global `[N]` where the entity appears in this
window. `mention_count` is the count within this window (≥1).

**Do NOT extract:** generic placeholders ("the community", "the region", "the
authors"), section headers, page numbers, or figure labels.

### Facts (semantic layer)

Extract factual triples in `(subject, predicate, object)` form grounded in this
window's text.

- `subject` and `object` MUST be strings that appear in this window's `entities`
  list — OR `object` is null and the value goes in `object_literal` (for
  literal-valued predicates).
- `predicate` is UPPER_SNAKE_CASE.
  - **Entity predicates** (object is an entity): `AUTHORED_BY`, `PUBLISHED_BY`,
    `FOUNDED_BY`, `PARTNERS_WITH`, `MEMBER_OF`, `LOCATED_IN`, `PART_OF`,
    `INSTANCE_OF`, `DEFINES`, `PROPOSES`, `EXEMPLIFIED_BY`, `DERIVES_FROM`,
    `SUPPORTS`, `CONTRASTS_WITH`, `COLLABORATES_WITH`, `RELATES_TO`.
  - **Literal predicates** (use `object_literal`, set `object` = null):
    `HAS_QUANTITY`, `HAS_TARGET`, `HAS_METRIC`, `HAS_TIMEFRAME`, `HAS_LOCATION`,
    `HAS_DATE`, `HAS_COUNT`, `HAS_STATUS`, `HAS_DESCRIPTION`, `HAS_URL`.
- For entity predicates: set `object` (string), `object_literal` = null.
  For literal predicates: set `object` = null, `object_literal` = the value.
- **Emit quantified claims as facts** — numbers, percentages, dollar amounts,
  counts, hectares, dates — using a literal predicate with the number/string in
  `object_literal` (e.g. subject="BioHubs research base", predicate="HAS_COUNT",
  object_literal="152 initiatives across 44 countries").
- `fact_text` is a single-sentence natural-language restatement.
- `chunk_range` = `[first_chunk_with_evidence, last_chunk_with_evidence]` (global
  indices, both integers).
- `confidence`: `"high"` if explicitly stated, `"medium"` if strongly implied.
  Do not emit low-confidence facts. Skip vague/obvious facts.

### Discourse (argument layer) — NEW in v2

Extract the document's **argument structure** — the load-bearing moves that make up
its reasoning, NOT a summary. Capture its theses and claims, the evidence and premises
that support them, the counterpoints it acknowledges, the questions it leaves open, the
terms it defines, and the implications it draws. A substantial whitepaper window
typically yields **2–6 moves**; do not pad, do not invent moves the window's text does
not support.

**Type each move as exactly one of these 8 `move_type`s** — the document's ARGUMENT
structure:

- `thesis` — the document's central position or overarching argument.
- `claim` — a specific assertion or proposition the document advances.
- `evidence` — data, an example, a case study, or a finding that backs a claim (e.g.
  "152 initiatives across 44 countries"). Set `supports` to the claim/thesis it backs.
- `premise` — a foundational assumption the argument rests on. Set `supports` to the
  claim/thesis it underpins.
- `counterpoint` — a tension, objection, limitation, or competing consideration the
  document acknowledges. Set `supports` to the claim/thesis it qualifies or contests.
- `open_question` — an unresolved question the document raises but does not answer.
- `definition` — a key term, framework, or concept the document defines (e.g. "defines
  a BioHub as a physically-anchored project…").
- `implication` — a consequence, recommendation, or call to action the document draws.

Fields per move:
- `title`: a single declarative sentence stating the move (for a `claim`/`thesis`, the
  claim itself); keep it to one sentence and put any elaboration in `detail`.
- `detail`: 1–3 sentences of substance, or `null`.
- `status`: one of `asserted` (stated as fact), `supported` (a claim backed by
  evidence), `contested` (marked as debated), `speculative` (a tentative implication),
  `open` (an unresolved open_question/counterpoint), `deferred` (pushed to future work),
  or `null`. Per-type guidance: thesis→asserted; premise→asserted|contested;
  evidence→asserted; claim→asserted|supported|contested; counterpoint→open|contested;
  open_question→open|deferred; definition→asserted; implication→speculative|asserted.
- `supports`: for `premise`/`evidence`/`counterpoint` (any move that backs or contests
  another), the EXACT `title` of the `claim`/`thesis` it supports — this builds the
  argument edge. `null` for standalone moves.
- `chunk_range`: `[first, last]` global chunk index supporting the move.

Do NOT duplicate a fact as a discourse move — facts are atomic triples; moves are
argument steps. A move may summarize several facts.

### Document object

- `name`: the document's title (or best inference for this window).
- `summary`: 1–3 sentences, present tense, no "this window".
- `doc_kind`: one of `whitepaper`, `paper`, `report`, `essay`, `spec`, `other`.
- `chunk_span`: `[first, last]` global chunk index this window covers.

## APPENDIX: Schema

```json
{
  "type": "object",
  "required": ["document", "entities", "facts", "discourse"],
  "additionalProperties": false,
  "properties": {
    "document": {
      "type": "object",
      "required": ["name", "summary", "doc_kind", "chunk_span"],
      "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "summary": {"type": "string"},
        "doc_kind": {"enum": ["whitepaper","paper","report","essay","spec","other"]},
        "chunk_span": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 2, "maxItems": 2}
      }
    },
    "entities": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name","type","first_seen_chunk","mention_count"],
        "properties": {
          "name": {"type": "string"},
          "type": {"enum": ["Person","Organization","Project","Concept","Location","Protocol","CaseStudy"]},
          "first_seen_chunk": {"type": "integer", "minimum": 0},
          "mention_count": {"type": "integer", "minimum": 1}
        }
      }
    },
    "facts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["subject","predicate","object","object_literal","fact_text","chunk_range","confidence"],
        "properties": {
          "subject": {"type": "string"},
          "predicate": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
          "object": {"type": ["string","null"]},
          "object_literal": {"type": ["string","null"]},
          "fact_text": {"type": "string"},
          "chunk_range": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 2, "maxItems": 2},
          "confidence": {"enum": ["high","medium"]}
        }
      }
    },
    "discourse": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["move_type","title","detail","status","supports","chunk_range"],
        "properties": {
          "move_type": {"type": "string", "minLength": 1},
          "title": {"type": "string", "minLength": 1},
          "detail": {"type": ["string","null"]},
          "status": {"type": ["string","null"]},
          "supports": {"type": ["string","null"]},
          "chunk_range": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 2, "maxItems": 2}
        }
      }
    }
  }
}
```

## DOCUMENT WINDOW (chunks labeled [N] with global indices)

<!-- The pipeline appends the concatenated window chunks here at call time -->
