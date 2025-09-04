# KOI Processor

🔄 **Processing Pipeline for KOI (Knowledge Organization Infrastructure)**

This repository contains the core processing pipeline that transforms raw content from sensor networks into semantically-enhanced, provenance-tracked knowledge artifacts using Regen Network's unified ontology.

## 🧬 Ontology-Enhanced Processing Pipeline

### Core Innovation: Extraction-Enhanced Chunking
Documents undergo JSON-LD extraction **first** to identify metabolic entities and discourse elements, then get chunked along semantic boundaries respecting entity relationships.

```
Raw Content → JSON-LD Extraction → Ontology-Informed Chunking → CAT Receipts → Apache Jena RDF Store
```

## 🏗️ Architecture Components

### Processing Scripts:
- **`process_all_documents_mistral.py`** - Unified ontology extraction with Mistral 7B
- **`process-documents-with-ontology.py`** - Core ontological processing framework
- **`process_all_with_deduplication.py`** - CID-based content deduplication
- **`provenance-tracking-system.py`** - Complete transformation provenance via CAT receipts

### Core Features:
- ✅ **Unified Ontology v1.0**: 36 classes, 26 properties (metabolic + discourse)
- ✅ **Apache Jena Integration**: RDF-ready JSON-LD output for semantic reasoning
- ✅ **W3C Standards Compliance**: Full SPARQL/OWL compatibility for Registry Framework
- ✅ **Ontology Provenance Tracking**: Every entity knows which ontology version created it
- ✅ **Dual Identification**: RIDs for semantic identity + CIDs for deduplication
- ✅ **CAT Receipt Generation**: Complete transformation audit trails
- ✅ **Semantic Chunking**: Respects entity boundaries and relationships
- ✅ **Cost Optimization**: Smart model selection and budget controls

## 🌐 Repository Integration

```
gaiaaiagent/koi-sensors    → Raw content ingestion (18,824 documents)
         ↓
gaiaaiagent/koi-processor  → Ontology-enhanced processing (THIS REPO)
         ↓  
gaiaaiagent/GAIA          → Agent orchestration with processed knowledge
```

**Ontological Foundation**: Built on unified ontology from [`gaiaaiagent/koi-research`](https://github.com/gaiaaiagent/koi-research)

## 🚀 Breakthrough Features

### 1. Ontology Provenance Tracking
```json
{
  "@id": "orn:regen.agent:greg-landua",
  "wasExtractedUsing": "orn:regen.ontology:unified-v1", 
  "ontologyVersion": "cid:sha256:e002e2e94b5cc9057e16fe0173854c88af1d1ba307986c0337066ddcbfdeb4a7",
  "extractedAt": "2025-09-03T22:30:00Z",
  "extractedBy": "mistral-processor-v1"
}
```

### 2. Semantic Boundary Intelligence
- **Entity Boundary Respect**: Never splits entities mid-mention
- **Relationship Preservation**: Keeps related discourse elements together  
- **Metabolic Process Grouping**: Chunks align with Anchor/Attest/Issue flows
- **Essence-Aware Chunking**: Groups content by regenerative alignments

### 3. Complete Transformation Provenance
Every processing step generates CAT (Content-Addressable Transformation) receipts with:
- Input/output RIDs and CIDs
- Processing agent and model used
- Ontology version reference
- Cost tracking and performance metrics
- Complete audit trail for governance

## 🎯 Apache Jena & RDF Integration

### RDF-Ready Output Format
All processed entities are generated as standards-compliant JSON-LD, ready for direct import into Apache Jena:

```json
{
  "@context": {
    "regen": "https://regen.network/ontology#",
    "koi": "https://regen.network/koi#"
  },
  "@id": "orn:regen.agent:greg-landau",
  "@type": "regen:Agent",
  "regen:alignsWith": ["Re-Whole Value", "Harmonize Agency"],
  "koi:cid": "cid:sha256:e002e2e94b5cc9057e16fe0173854c88af1d1ba307986c0337066ddcbfdeb4a7",
  "prov:wasGeneratedBy": "orn:regen.transform:extraction_001"
}
```

### SPARQL Query Ready
Entities can be immediately queried using standard SPARQL:
```sparql
PREFIX regen: <https://regen.network/ontology#>
SELECT ?agent ?essence WHERE {
  ?agent a regen:Agent ;
         regen:alignsWith ?essence .
}
```

### Registry Framework Integration
Direct compatibility with Regen Network's RDF-based Registry Framework for seamless "credits as claims" modeling and cross-methodology reasoning.

## 🎯 Target Performance Metrics

- **Processing Throughput**: 1000+ documents/hour through ontology pipeline
- **Cost Control**: <$100/day with intelligent model selection
- **Storage Efficiency**: 30%+ reduction via CID-based deduplication
- **Semantic Quality**: Entity-boundary-aware chunking preserving meaning
- **RDF Compatibility**: 100% standards-compliant JSON-LD output

## 🌱 Regenerative Alignment

Built on Regen Network's core essence:
- **Re-Whole Value**: Preserves complete semantic relationships
- **Nest Caring**: Respects community knowledge and context
- **Harmonize Agency**: Balances automation with human oversight

---

**Part of the world's first self-describing living knowledge organism** 🌟

Connected repositories:
- [koi-research](https://github.com/gaiaaiagent/koi-research) - Ontological foundation
- [koi-sensors](https://github.com/gaiaaiagent/koi-sensors) - Data ingestion network  
- [GAIA](https://github.com/gaiaaiagent/GAIA) - Agent orchestration