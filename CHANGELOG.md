# Changelog

All notable BibReview releases are documented here.

## 1.1.0 — 2026-09-21

### Bibliographic contributors

- add source-preserving `Editor` metadata alongside `Author`;
- require every canonical publication to contain at least one author or editor;
- collect CrossRef `editor` metadata without conflating editors with author identities;
- bump the canonical bibliography schema to version 2 while retaining read support
  for valid version-1 bibliographies.

### Static-site rendering

- carry editor metadata through the renderer-independent site model;
- render editor-only publication rows with an explicit `Ed.` or `Eds.` role;
- render an `Editors` section and optional `editors` front-matter field without
  adding editors to author identity pages.

### Identifier policy

- keep DOI optional in the canonical publication model: stable internal UUIDs,
  not DOI values, remain the publication identity;
- keep automated collection DOI-backed for now, leaving non-DOI acquisition and
  BibTeX provenance as an explicit future ingestion concern.

## 1.0.0 — 2026-09-18

First stable public release of BibReview as a generic bibliographic engine.

### Core workflow

- configurable OpenAlex discovery and relevance screening;
- CrossRef DOI metadata collection with optional enrichment providers;
- explicit pending, review, rejected and collected states;
- canonical JSON bibliography with stable publication UUIDs;
- refresh/recollection of incomplete existing records;
- reviewed author identity mapping with safe and ambiguous proposals;
- tracked BibTeX retrieval and correction workflow;
- dry-run planning, backups and atomic-per-file persistence.

### Static-site support

- deterministic Jekyll publication, author and year rendering;
- project-owned layouts, themes and deployment;
- optional display-only arXiv feed cache;
- documented GitHub Pages deployment and scheduled arXiv refresh;
- optional GoatCounter integration guidance.

### Data-quality safeguards

- invalid or absent DOI BibTeX is treated as unavailable data rather than a
  synthetic BibTeX file;
- refresh never overwrites a valid local BibTeX file with an empty provider
  response;
- ambiguous author identities remain manual decisions;
- project contact email is propagated to CrossRef when available.

### Documentation

- complete Linux, macOS and Windows installation guide;
- configuration and CLI reference;
- canonical data/state model;
- local maintenance workflow;
- provider-error and manual-correction procedures;
- ambiguous-author handling;
- GitHub Pages and GoatCounter guides.

Stable installations and automated sites should pin the exact tag **v1.0.0**.
