# BibReview

BibReview is a generic bibliographic engine intended to support reproducible, human-reviewed literature databases and static scholarly websites.

The current M3 implementation includes:

- versioned `bibreview.yml` loading and path resolution independent of PHRAISE;
- a canonical publication model whose persistent internal identity is independent of DOI;
- conservative exact strong-identifier matching, currently limited to DOI;
- generic HTTP, CrossRef, publisher enrichment, Semantic Scholar, and Mendeley provider layers;
- a side-effect-free collection pipeline producing canonical `Publication` objects;
- canonical bibliography merge and JSON storage;
- staged multi-file writes with atomic replacement per destination file;
- a `bibreview merge` command for merging the canonical collected staging bibliography into project state;
- `--dry-run` support for mutating CLI workflows.

## Project-state handoff

Collection and merge deliberately use separate persisted files:

```text
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

`data/collected.json` is a staging bibliography, not a backup. This separation keeps backups in `archive/` and avoids using an overwritten bibliography file as an implicit data-transfer mechanism.

During `bibreview merge`, accepted staged publications are merged by persistent UUID and approved strong identifiers, accepted DOI values move from `pending` to `known`, rejected DOI values are discarded from the staged batch, the staging bibliography is emptied, and the previous bibliography is backed up before replacement. A dry run computes the same plan without mutating files.

PHRAISE remains the integration and non-regression reference during extraction.
