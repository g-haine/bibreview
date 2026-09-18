# BibReview

BibReview is a generic bibliographic engine intended to support reproducible, human-reviewed literature databases and static scholarly websites.

The current M3 implementation includes:

- versioned `bibreview.yml` loading, path resolution, and optional configured dotenv loading independent of PHRAISE;
- a canonical publication model whose persistent internal identity is independent of DOI;
- conservative exact strong-identifier matching, currently limited to DOI;
- generic HTTP, OpenAlex, CrossRef, publisher enrichment, Semantic Scholar, and Mendeley provider layers;
- configurable discovery queries, accepted publication types, DOI exclusions, and relevance patterns;
- a side-effect-free discovery/relevance pipeline that classifies DOI candidates into collection, manual-review, or rejected state;
- a side-effect-free collection pipeline producing canonical `Publication` objects;
- an opt-in refresh/recollect pipeline for stale existing publications;
- canonical author-mapping analysis, bibliography merge, and JSON storage;
- staged multi-file writes with atomic replacement per destination file;
- `bibreview discover`, `collect`, `refresh`, `authors`, `merge`, and `render` commands;
- `--dry-run` support for mutating CLI workflows.

M4 site extraction now has three explicit layers in `bibreview.site`. `build_site_model()` converts canonical publications plus reviewed author mappings into immutable publication, author, year, and reference-link data. Pure Jekyll renderers convert that model into immutable `RenderedArtifact(path, content)` values. `plan_rendered_artifacts()` then reconciles those artifacts with explicitly managed generated directories and returns a read-only persistence plan; `apply_rendered_artifacts()` performs atomic-per-file writes followed by deletion of obsolete generated files. The project-level `bibreview render` command composes those layers from `bibreview.yml`; the pure renderers themselves still perform no filesystem or network access.

## Runtime secrets

Projects may point BibReview at an optional dotenv file:

```yaml
environment:
  file: .env
```

The path is resolved relative to `bibreview.yml`. Values loaded from that file
act only as defaults: variables already present in the process environment take
precedence. A missing configured file does not abort the command; BibReview
warns and continues with the process environment, so optional providers retain
their normal missing-secret behavior.

## Project-state handoff

Discovery, collection, refresh, and merge use explicit persisted states:

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

existing bibliography + stored/current BibTeX
      ↓
refresh
      ↓
data/collected.json
      ↓
merge
      ↓
updated bibliography with persisted UUID retained
```

`bibreview discover` retrieves candidates from the configured discovery provider (currently OpenAlex), verifies DOI-backed works through CrossRef, composes discovery enrichment, and applies the project's regular-expression relevance rules. Unicode dash punctuation is normalized before matching. `relevance.unmatched` controls whether unmatched supported works go to manual review or directly to rejected state.

Discovery policy belongs to configuration rather than engine source code. `discovery.accepted_types` controls supported metadata work types, while `discovery.exclude_doi_substrings` can skip project-specific DOI families without adding hard-coded domain assumptions to BibReview. OpenAlex can be used without an API key; a project may optionally name an API-key environment variable in its provider configuration.

Refresh is deliberately separate from discovery. It is opt-in through `refresh.types` and `refresh.when_missing_any`. For matching existing DOI-backed publications, BibReview compares the stored BibTeX with the current DOI BibTeX. Missing or changed BibTeX triggers recollection into `data/collected.json`; the authoritative bibliography is not deleted or modified until the later merge. Existing permalinks are retained during recollection, and exact DOI merge preserves the persisted publication UUID. Changed stored BibTeX is backed up in the configured archive before replacement. DOI values recorded as known but absent from the canonical bibliography are moved back to pending state for normal collection recovery.

`data/collected.json` is a staging bibliography, not a backup. This separation keeps backups in `archive/` and avoids using an overwritten bibliography file as an implicit data-transfer mechanism. Collection and refresh both refuse to overwrite a non-empty staging batch.

During `bibreview merge`, accepted staged publications are merged by persistent UUID and approved strong identifiers, accepted DOI values move from `pending` to `known`, rejected DOI values are discarded from the staged batch, the staging bibliography is emptied, and the previous bibliography is backed up before replacement. A dry run computes the same plan without mutating files.

## Site-model boundary

The M4 transformation boundary is intentionally one-way and side-effect free:

```text
Publication + author_mappings
          ↓
  build_site_model
          ↓
       SiteModel
          ↓
     pure renderer
          ↓
 RenderedArtifact[]
          ↓
 persistence plan
          ↓
 managed site files
```

`SiteModel` preserves source-visible author names while linking them to reviewed author identities, prepares author/year membership, validates safe unique publication permalinks, and resolves DOI references to internal permalinks when the referenced work is present in the same bibliography. Project-specific category names and optional index prose live in `site.jekyll` configuration rather than engine source code. The Jekyll rendering layer covers author/year indexes and publication posts. `bibreview render` requires a tracked BibTeX file for every publication, performs no provider/network fallback, and reports unused BibTeX files without moving or deleting them. Site persistence owns only `_posts`, `authors`, and `years`: artifacts outside those roots are rejected, path traversal and symlinked managed roots are refused, unchanged files are left alone, and only obsolete files inside those generated roots may be deleted. Deployment, CSS, templates, branding, source BibTeX lifecycle, and non-generated project files remain outside the render command.

PHRAISE remains the integration and non-regression reference during extraction.
