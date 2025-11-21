
# SPARQL Query Context for Regen Network Knowledge Graph

## Namespaces
```sparql
PREFIX regx: <https://regen.network/ontology/experimental#>
PREFIX schema: <http://schema.org/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
```

## Core Classes
- `Entity` (23273 instances)
- `Statement` (23273 instances)
- `Organization` (8192 instances)
- `Project` (6067 instances)
- `Activity` (5290 instances)
- `KGExtraction` (5184 instances)
- `Person` (3549 instances)
- `CATReceipt` (106 instances)
- `TransformationActivity` (106 instances)

## Core Properties for Statements

- `regx:subject` - The subject of a statement
- `regx:predicate` - The relationship/action
- `regx:object` - The object of the statement
- `regx:confidence` - Confidence score (0.0-1.0)

## Common Predicates (Relationships)
These are the actual relationships found in the data:
- `is` (833 uses)
- `provides` (774 uses)
- `has` (364 uses)
- `supports` (314 uses)
- `is associated with` (293 uses)
- `includes` (256 uses)
- `offers` (238 uses)
- `is involved in` (202 uses)
- `suggests` (195 uses)
- `questions` (176 uses)
- `allows` (174 uses)
- `is a` (171 uses)
- `enables` (157 uses)
- `proposes` (153 uses)
- `develops` (150 uses)
- `requires` (150 uses)
- `published` (144 uses)
- `hosts` (128 uses)
- `is part of` (119 uses)
- `focuses on` (99 uses)
- `states` (98 uses)
- `uses` (97 uses)
- `discusses` (94 uses)
- `are` (88 uses)
- `involves` (87 uses)
- `believes` (85 uses)
- `ensures` (85 uses)
- `creates` (83 uses)
- `developed` (81 uses)
- `emphasizes` (78 uses)

## Example SPARQL Patterns

### Find statements about an entity:
```sparql
SELECT ?stmt ?predicate ?object ?confidence WHERE {
  ?stmt a regx:Statement .
  ?stmt regx:subject ?subject .
  ?stmt regx:predicate ?predicate .
  ?stmt regx:object ?object .
  ?stmt regx:confidence ?confidence .
  FILTER(CONTAINS(LCASE(?subject), "regen network"))
}
```

### Find organizations:
```sparql
SELECT ?org ?label WHERE {
  ?org a schema:Organization .
  ?org rdfs:label ?label .
}
```

### Find high-confidence statements:
```sparql
SELECT ?subject ?predicate ?object WHERE {
  ?stmt regx:subject ?subject .
  ?stmt regx:predicate ?predicate .
  ?stmt regx:object ?object .
  ?stmt regx:confidence ?confidence .
  FILTER(?confidence > 0.8)
}
```
