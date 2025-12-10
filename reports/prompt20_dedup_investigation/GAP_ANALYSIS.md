# Gap Analysis (Beyond yonearth-gaia-chatbot)

## Domain & Corpus Gaps
- Regen-specific aliases are absent: tokens/modules (“$REGEN”, “Regen Ledger v4”, “ecocredit module”), org variants (“Regen Network Development, PBC”, “RND”), validator/DAO roles.
- Blockchain role noise (“validator”, “delegator”) not blocked or typed distinctly; could be mistaken for people/entities.
- Scientific/standards entities (Verra, Gold Standard, methodologies) and datasets/schemas are not explicitly normalized or deduped.
- Multi-language content (ES/FR) likely in forums/docs; current pipeline is English-only and string-matching only.

## Dedup/Resolution Gaps
- No persistent cross-source canonical registry; fuzzy matching is per-batch/per-type, risking drift across runs/checkpoints.
- Abbreviation/acronym expansion is limited to merge validator lists; no general acronym resolver (e.g., IBC, CRU, NCT).
- Temporal/version handling missing (e.g., “SDK 0.45” vs “SDK 0.47”; rebrands).
- Possessive/compound entities not fully resolved for domain phrases (“Regen’s token”, “IBC module”).
- Relationship-level dedup is semantic but not conflict-aware (no contradiction detection, no temporal scoping).
- Pronoun resolver lacks gender/number/type constraints for domain-specific entities (organizations vs networks) and does not leverage graph context.

## Quality/Filtering Gaps
- Stop-word/generic lists miss Regen-specific placeholders (“unknown validator”, “community member”, “team”) and crypto spam tokens.
- VagueEntityBlocker/ContextEnricher patterns tuned to books; GitHub/issue text patterns (usernames, handles, code identifiers) not covered.
- Confidence thresholds tuned to philosophical/phrasal content, not to code/docs where confidence distribution differs.

## Operational/Performance Gaps
- Embedding-based semantic dedup optional; missing model leads to weaker string-only dedup.
- Quadratic matching within large type buckets may be heavy for 4k+ GitHub docs without blocking/partitioning.
- No explicit rollback/trace of cross-run canonical decisions; alias maps are in-memory per run.

## Testing/Validation Gaps
- No Regen-specific evaluation sets; existing tests cover generic quality filters, not blockchain/domain cases.
- Lack of automated regression around merge validator blocklists for new domain aliases.
