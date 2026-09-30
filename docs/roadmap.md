# Roadmap

This roadmap describes the current development direction for BibReview after the
v1.7.0 stabilization milestone. GitHub issues remain the authoritative place for
feature-specific design and acceptance criteria.

## Current state: v1.7.0

The v1.7 line marks the point where the core architecture is considered stable
enough for broader use.

Completed foundations include:

- persistent UUID identity for every canonical publication;
- typed project-facing identifier state;
- DOI as the sole current strong/automatable identifier;
- reviewed DOI-backed discovery and collection;
- reviewed DOI-less import through a versioned YAML contract;
- deterministic `ID.txt` projection with one token per canonical publication;
- explicit staging and merge boundaries;
- tracked canonical BibTeX;
- author identity review;
- deterministic Jekyll rendering;
- resumable audit campaigns;
- reviewed refresh and backfill;
- resumable reference-list review and reconciliation;
- historical abstract/title/reference hygiene workflows;
- provider diagnostics and rate-limit-aware transport;
- real-world end-to-end validation on PHRAISE.

The main architectural principle remains unchanged:

> Automation may gather and normalize evidence, but canonical decisions remain
> explicit and reviewable.

## Near-term priorities

### 1. Better onboarding for new bibliographies — #31

A mature existing project can now be maintained safely, but bootstrapping a new
large bibliography is still too manual.

Issue #31 proposes a resumable `bibreview init` campaign that reuses existing
discovery, review, collect, merge, author, and render semantics instead of
creating a second ingestion system.

This is the most important next user-facing capability because it determines how
easily BibReview can be adopted outside PHRAISE.

Target direction:

- bounded initialization batches;
- resumable campaign state;
- stable candidate identity;
- progress reporting;
- explicit human review;
- no automatic canonical merge;
- validation on a second real bibliography.

A successful second deployment should become the main proof that BibReview is a
generic engine rather than a PHRAISE-specific extraction.

### 2. Manual reviewed backfill — #98

Some canonical metadata cannot be recovered from configured providers.

Issue #98 adds a provider-free manual mode, initially:

~~~bash
bibreview backfill --field abstract --manual
~~~

The feature should reuse the existing backfill resolution, fingerprint,
staleness, staging, and merge boundaries. It is a small but important gap in the
current maintenance story.

This is suitable for the v1.7.x line.

## Performance and architecture work

Correctness and review boundaries take priority over throughput. Once onboarding
and remaining maintenance gaps are addressed, provider traffic can be reduced
without changing canonical semantics.

### Shared batch enrichment — #80

Issue #80 should factor a reusable exact batch-enrichment layer for workflows
such as backfill and refresh.

Priorities:

- preserve DOI order;
- preserve per-provider failure isolation;
- retain project-configured pacing;
- support partial provider records;
- keep proposal generation read-only;
- prove sequential/batched semantic equivalence.

### Batched collection — #52

Issue #52 extends batching to `collect`.

Collection has stricter requirements than audit/backfill because it also
constructs new publications, allocates permalinks, retrieves references and
BibTeX, and writes canonical staging.

Implement this only after the shared batching abstractions are sufficiently
clear. Performance must not weaken staging, duplicate, ordering, or provider
failure semantics.

## Optional authoring assistance

### PDF-assisted DOI-less drafting — #137

Issue #137 may add an optional helper such as:

~~~bash
bibreview import --from-pdf article.pdf --output publication.yml
~~~

This must remain an authoring aid only.

It must never:

- create a second canonical ingestion path;
- stage or merge automatically;
- invent missing metadata;
- bypass DOI detection;
- enlarge the mandatory dependency set without an explicit decision.

The reviewed YAML manifest remains authoritative.

This feature is useful, but it is not required for the v1.7 core.

## Longer-term data products

### Deterministic corpus export — #99

A future `bibreview corpus` command may export the information actually present
in the canonical bibliography to a machine-friendly corpus.

The first version should remain deterministic and non-AI:

- publication UUID;
- bibliographic metadata;
- abstracts when present;
- keywords;
- references/citations where appropriate;
- provenance back to canonical records.

It must not imply access to article full text when BibReview does not have it.

### Optional local question answering — #100

Only after #99, BibReview may offer optional local retrieval/question answering
over an exported corpus.

This layer should:

- remain optional;
- keep AI dependencies outside the core;
- cite the BibReview records used as evidence;
- make the metadata/abstract knowledge boundary explicit;
- never write generated answers into canonical bibliography state.

This is deliberately a late roadmap item.

## Release direction

The exact version numbers after v1.7.0 are not fixed in advance, but the current
direction is:

~~~text
v1.7.x
  documentation stabilization
  manual maintenance gaps
  small compatibility / correctness improvements

next feature release
  resumable new-project initialization (#31)
  validation on a second real bibliography

later
  provider batching / performance (#80, #52)
  optional PDF-assisted drafting (#137)

long term
  deterministic corpus export (#99)
  optional local QA over that corpus (#100)
~~~

Large version changes should correspond to a stable public contract or a
meaningful new workflow, not merely accumulated internal refactoring.

## What is intentionally not on the roadmap

BibReview should not become:

- an unattended system that silently rewrites canonical bibliography data;
- a fuzzy publication-identity merger;
- a provider-specific mirror;
- a mandatory AI application;
- a full-text scholarly database unless a separate explicit architecture is
  designed for that purpose.

Keeping those boundaries explicit is part of the project's maintainability.
