# BibReview

BibReview is a generic bibliographic engine intended to support reproducible, human-reviewed literature databases and static scholarly websites.

The current M3 implementation includes:

- versioned `bibreview.yml` loading and path resolution independent of PHRAISE;
- a canonical publication model whose persistent internal identity is independent of DOI;
- conservative exact strong-identifier matching, currently limited to DOI;
- generic HTTP, OpenAlex, CrossRef, publisher enrichment, Semantic Scholar, and Mendeley provider layers;
- configurable discovery queries, accepted publication types, DOI exclusions, and relevance patterns;
- a side-effect-free discovery/relevance pipeline that classifies DOI candidates into collection, manual-review, or rejected state;
- a side-effect-free collection pipeline producing canonical `Publication` objects;
- canonical author-mapping analysis, bibliography merge, and JSON storage;
- staged multi-file writes with atomic replacement per destination file;
- `bibreview discover`, `collect`, `authors`, and `merge` commands;
- `--dry-run` support for mutating CLI workflows.

## Project-state handoff

Discovery, collection, and merge deliberately use separate persisted states:

```text
configured discovery source
      ↓
discover
      ├── relevant ─────────────→ data/pending.txt
      ├── uncertain ────────────→ data/review.txt
      └── rejected/unavailable ─→ data/rejected.txt
                                   ↓
                              human review

pending DOI state
      ↓
collect
      ↓
data/collected.json
      ↓
merge
      ↓
data/bibliography.json
```

`bibreview discover` retrieves candidates from the configured discovery provider (currently OpenAlex), verifies DOI-backed works through CrossRef, composes discovery enrichment, and applies the project's regular-expression relevance rules. Unicode dash punctuation is normalized before matching. `relevance.unmatched` controls whether unmatched supported works go to manual review or directly to rejected state.

Discovery policy belongs to configuration rather than engine source code. `discovery.accepted_types` controls supported metadata work types, while `discovery.exclude_doi_substrings` can skip project-specific DOI families without adding hard-coded domain assumptions to BibReview. OpenAlex can be used without an API key; a project may optionally name an API-key environment variable in its provider configuration.

`data/collected.json` is a staging bibliography, not a backup. This separation keeps backups in `archive/` and avoids using an overwritten bibliography file as an implicit data-transfer mechanism.

During `bibreview merge`, accepted staged publications are merged by persistent UUID and approved strong identifiers, accepted DOI values move from `pending` to `known`, rejected DOI values are discarded from the staged batch, the staging bibliography is emptied, and the previous bibliography is backed up before replacement. A dry run computes the same plan without mutating files.

Discovery intentionally does not perform stale-record or BibTeX refresh/recollection. Those operations are a separate project-maintenance concern rather than publication discovery.

PHRAISE remains the integration and non-regression reference during extraction.
