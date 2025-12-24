# Polysemy Resolution Examples - Week 6

**Generated:** 2025-12-24 05:30:40
**Database:** eliza

This report demonstrates the polysemy-aware entity resolution
system for handling ambiguous labels in query/GraphRAG contexts.

---

## `notion`

**Winner:** Notion (TECHNOLOGY)
- Occurrences: 308
- Relationships: 0
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| ORGANIZATION | 27 | 0 | 27800 |

---

## `discord`

**Winner:** Discord (TECHNOLOGY)
- Occurrences: 193
- Relationships: 0
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| ORGANIZATION | 15 | 0 | 15800 |

---

## `telegram`

**Winner:** Telegram (TECHNOLOGY)
- Occurrences: 212
- Relationships: 0
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| ORGANIZATION | 7 | 0 | 7800 |

---

## `ethereum`

**Winner:** Ethereum (TECHNOLOGY)
- Occurrences: 128
- Relationships: 2
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| PROJECT | 17 | 0 | 17900 |
| ORGANIZATION | 9 | 0 | 9800 |

---

## `ethereum` (hint: TECHNOLOGY)

**Winner:** Ethereum (TECHNOLOGY)
- Occurrences: 128
- Relationships: 2
- Resolution: type_hint_match
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| PROJECT | 17 | 0 | 17900 |
| ORGANIZATION | 9 | 0 | 9800 |

---

## `ethereum` (hint: ORGANIZATION)

**Winner:** Ethereum (TECHNOLOGY)
- Occurrences: 128
- Relationships: 2
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| ORGANIZATION | 9 | 0 | 59800 |
| PROJECT | 17 | 0 | 17900 |

---

## `sparql`

**Winner:** SPARQL (TECHNOLOGY)
- Occurrences: 186
- Relationships: 6
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| CONCEPT | 29 | 0 | 29700 |
| STANDARD | 9 | 0 | 9600 |

---

## `rdf`

**Winner:** RDF (TECHNOLOGY)
- Occurrences: 52
- Relationships: 1
- Resolution: dominant_connectivity
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| CONCEPT | 31 | 0 | 31700 |
| STANDARD | 23 | 0 | 23600 |

---

## `regen commons`

**Winner:** Regen Commons (ORGANIZATION)
- Occurrences: 151
- Relationships: 0
- Resolution: highest_combined_score
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| PROJECT | 147 | 0 | 147900 |
| CONCEPT | 19 | 0 | 19700 |

---

## `aerodrome`

**Winner:** Aerodrome (TECHNOLOGY)
- Occurrences: 100
- Relationships: 1
- Resolution: dominant_connectivity
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| PROJECT | 98 | 0 | 98900 |
| ORGANIZATION | 36 | 0 | 36800 |

---

## `koi`

**Winner:** KOI (PROJECT)
- Occurrences: 166
- Relationships: 0
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| TECHNOLOGY | 65 | 0 | 66000 |

---

## `blockchain`

**Winner:** blockchain (TECHNOLOGY)
- Occurrences: 148
- Relationships: 3
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| CONCEPT | 29 | 0 | 29700 |

---

## `usdc`

**Winner:** USDC (TECHNOLOGY)
- Occurrences: 94
- Relationships: 4
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| MATERIAL | 14 | 0 | 14350 |
| CONCEPT | 13 | 0 | 13700 |
| PROJECT | 2 | 0 | 2900 |

---

## `base`

**Winner:** Base (TECHNOLOGY)
- Occurrences: 68
- Relationships: 0
- Resolution: dominant_occurrence
- Is Polysemy: True

**Alternatives:**

| Type | Occurrences | Relationships | Score |
|------|-------------|---------------|-------|
| PROJECT | 24 | 0 | 24900 |
| ORGANIZATION | 2 | 0 | 2800 |
| MODULE | 1 | 0 | 1300 |

---

## `osmosis`

**Winner:** Osmosis (ORGANIZATION)
- Occurrences: 193
- Relationships: 87
- Resolution: dominant_occurrence
- Is Polysemy: False

---

## Usage in GraphRAG

```python
from scripts.resolve_entity_variants import resolve_entity

# Basic resolution (uses occurrence_count + connectivity)
result = resolve_entity(conn, 'notion')
winner = result.winner  # EntityVariant object

# With type hint (boost matching type)
result = resolve_entity(conn, 'ethereum', type_hint='TECHNOLOGY')

# Get all variants for multi-type queries
all_variants = [result.winner] + result.alternatives
```

---

*Report generated by `scripts/resolve_entity_variants.py`*