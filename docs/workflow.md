# Local workflow

This guide describes the normal human-reviewed operating cycle for an existing
BibReview project.

BibReview intentionally separates provider evidence, review state, staging, and
canonical state. Do not skip the explicit `merge` boundary.

## 1. Validate the project

Before substantial maintenance:

~~~bash
bibreview validate
bibreview status
~~~

For provider credentials and availability:

~~~bash
bibreview providers
bibreview providers --check
~~~

Commit a clean baseline before a large campaign or migration.

## 2. Add DOI-backed publications

Discover candidates:

~~~bash
bibreview --dry-run discover
bibreview discover
~~~

BibReview writes DOI candidates into typed state files:

- `data/newID.txt` — accepted/pending automated collection;
- `data/checkID.txt` — requires human relevance review;
- `data/badID.txt` — deliberately rejected.

Review `checkID.txt` manually and move each `doi:` token to the
appropriate file.

Collect accepted DOI values:

~~~bash
bibreview --dry-run collect
bibreview collect
~~~

Inspect:

- `data/collected.json`;
- newly written or updated tracked BibTeX in `bib/`.

Then promote explicitly:

~~~bash
bibreview --dry-run merge
bibreview merge
~~~

Collection never makes provider metadata canonical by itself.

## 3. Add a DOI-less publication

DOI-less publications use the reviewed manual import path.

Create the real manifest first:

~~~bash
bibreview import --init publication.yml
~~~

Use [`publication.example.yml`](../publication.example.yml) only as a
field reference. Do not copy its illustrative UUID.

Complete and review the manifest, including:

- publication metadata;
- authors/editors;
- optional auxiliary identifiers;
- provenance;
- reviewed citation/BibTeX.

Then validate without writing project state:

~~~bash
bibreview --dry-run import publication.yml
~~~

Stage the import:

~~~bash
bibreview import publication.yml
~~~

Inspect `collected.json`, the tracked BibTeX, and the durable
`data/imports/<UUID>.yml` evidence. Promote through the same ordinary
boundary:

~~~bash
bibreview --dry-run merge
bibreview merge
~~~

A DOI in a manual import is refused because DOI remains the sole current
strong/automatable identifier.

## 4. Resolve contributor identities

After canonical additions or contributor changes:

~~~bash
bibreview authors
bibreview authors --apply-safe
bibreview authors
~~~

Safe mappings may be applied automatically. Remaining ambiguous identities are
human decisions; edit `data/author_mappings.json` as needed.

See [Author identities](authors.md).

## 5. Render the site

Preview the reconciliation:

~~~bash
bibreview --dry-run render
~~~

Apply it:

~~~bash
bibreview render
~~~

Inspect the Git diff. Rendering should only reconcile BibReview-managed
generated artifacts.

## Maintenance workflows

The following workflows are not required for every ordinary update. Use them
when the canonical bibliography needs review or enrichment.

### Audit historical metadata

`audit` compares canonical records with current provider evidence in a
resumable campaign without changing canonical state.

Start with a small batch when appropriate:

~~~bash
bibreview --dry-run audit --batch-size 25
bibreview audit --batch-size 25
~~~

Continue the campaign:

~~~bash
bibreview audit
~~~

Review:

~~~bash
bibreview audit --review
bibreview -v audit --review
~~~

Resolve actionable findings:

~~~bash
bibreview audit --resolve
~~~

Stage completed decisions:

~~~bash
bibreview --dry-run audit --apply
bibreview audit --apply
~~~

Then inspect and merge:

~~~bash
bibreview --dry-run merge
bibreview merge
~~~

Use `audit --full` only when you deliberately want fresh provider
evidence for the complete current bibliography.

### Refresh incomplete/stale records

`refresh` is review-first. Its scan does not stage changes.

~~~bash
bibreview --dry-run refresh
bibreview refresh

bibreview refresh --review
bibreview -v refresh --review
bibreview refresh --resolve

bibreview --dry-run refresh --apply
bibreview refresh --apply
~~~

Only accepted/custom fills reach `collected.json`. Inspect the staging and
tracked BibTeX changes, then merge.

### Backfill a missing field

For a known missing canonical field such as an abstract:

~~~bash
bibreview backfill --field abstract
bibreview backfill --resolve
bibreview --dry-run backfill --apply
bibreview backfill --apply
~~~

Then inspect and merge normally.

Provider values are proposals, not canonical authority. Backfill never replaces a
meaningful non-empty canonical value.

### Review reference lists

`references` rebuilds reference-list evidence from current provider data
through a resumable campaign.

Typical sequence:

~~~bash
bibreview --dry-run references
bibreview references
bibreview references --review
bibreview -v references --review
~~~

Apply deterministic safe outcomes when available:

~~~bash
bibreview --dry-run references --apply-safe
bibreview references --apply-safe
~~~

For residual human decisions:

~~~bash
bibreview references --resolve
bibreview --dry-run references --apply
bibreview references --apply
~~~

Any staged changes still require `bibreview merge`.

### Hygiene campaigns

Use `hygiene` to inspect structured-text contamination and historical
title/reference-citation artifacts.

Examples:

~~~bash
bibreview hygiene
bibreview hygiene --titles
bibreview hygiene --citations
~~~

Reviewed migrations use the corresponding `--review`, `--resolve`,
`--apply-safe`, and `--apply` surfaces. See
[Command reference](commands.md) for the exact command-specific options.

Hygiene never bypasses staging and merge.

## Optional arXiv cache

If enabled:

~~~bash
bibreview arxiv
~~~

The arXiv cache is display-only. It does not create canonical publications and
does not change canonical bibliography metadata merely because the cache refresh
time changes.

## Recommended routine

For a normal incremental update:

~~~text
validate
   ↓
discover
   ↓
human relevance review if needed
   ↓
collect
   ↓
inspect staging + BibTeX
   ↓
merge
   ↓
authors
   ↓
render
   ↓
review Git diff / open project PR
~~~

Use audit, references, refresh, backfill, and hygiene only when their specific
maintenance purpose applies.

## Safety checklist

Before every canonical merge:

- `data/collected.json` contains only changes you intend to promote;
- tracked BibTeX changes are understood;
- duplicate/permalink/identity warnings have been resolved;
- review decisions are complete for the workflow that produced staging;
- `bibreview --dry-run merge` reports the expected additions/updates;
- the final Git diff contains no unrelated generated or canonical changes.

For recovery and manual repair procedures, see
[Manual corrections](corrections.md).
