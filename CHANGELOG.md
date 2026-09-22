# Changelog

All notable BibReview releases are documented here.

## 1.4.4 — 2026-09-22

### Concise audit review output

- make `bibreview audit --review` print only the aggregate review summary by
  default;
- expose the existing full publication-by-publication human-readable review
  through the global `-v/--verbose` option;
- keep `bibreview audit --review --json` complete and unchanged regardless of
  verbosity;
- preserve all audit evidence, classification, campaign, checkpoint, and
  canonical-state semantics.

### Validation

- add CLI regression coverage for default summary output, verbose detailed
  output, and complete JSON review behavior.

## 1.4.3 — 2026-09-22

### Batched CrossRef audit lookups

- add exact multi-DOI CrossRef REST lookups using repeated `doi:` filters;
- use a conservative audit chunk size of 25 DOI values per CrossRef request;
- reuse the existing generic audit batching orchestrator without changing
  comparison, checkpoint, retry, or canonical-state semantics;
- preserve the same CrossRef normalization for single and batched responses;
- keep missing CrossRef records as non-retryable provider-unavailable evidence
  while chunk-level transport failures remain retryable.

### Validation

- extend regression coverage for batched CrossRef provider responses, missing
  records, response-shape validation, provider limits, and audit normalization;
- validate the feature with 327 passing tests before release.

## 1.4.2 — 2026-09-22

### Batched audit provider requests

- batch OpenAlex audit lookups with bounded multi-DOI OR filters, up to 100 DOI
  values per provider request;
- batch Semantic Scholar audit lookups through the paper batch endpoint, up to
  500 DOI values per provider request;
- keep CrossRef DOI audit lookups individual because its REST API does not
  provide an equivalent arbitrary-DOI batch endpoint;
- retain the existing 1.1-second authenticated Semantic Scholar pacing between
  batch requests rather than between publications.

### Audit safety and compatibility

- preserve the same normalized provider evidence, comparison semantics,
  corroboration rules, campaign state, and per-publication checkpointing;
- mark only DOI values in a failed provider chunk retryable when a batch request
  fails;
- add sanitized JSON-POST transport support and retry coverage for idempotent
  provider batch lookups;
- validate the implementation with 322 passing tests.

## 1.4.1 — 2026-09-22

### Corroborated audit review

- make external metadata differences informational by default and require at
  least two independent providers to corroborate the same alternative before a
  review finding becomes actionable;
- group review-equivalent provider values before counting support, including
  harmless TeX/Unicode and spacing variants that represent the same candidate
  correction;
- keep a corroborated substantive alternative informational when another
  provider confirms the canonical value;
- keep one-day `created_date` offsets and obvious provider truncations of a
  fuller canonical abstract informational;
- preserve provider disagreements and single-provider author anomalies as
  review evidence without promoting them automatically to corrections.

### Validation and compatibility

- leave the raw pairwise comparator and offline `audit --reclassify` behavior
  unchanged;
- validate the review rules on the fixed 225-publication reference corpus,
  reducing 31 actionable findings to 4 while preserving the intended
  corroborated corrections;
- extend regression coverage for corroborated missing metadata, substantive
  alternatives, canonical confirmation, TeX/Unicode grouping, date offsets,
  truncated abstracts, and isolated contributor differences.

## 1.4.0 — 2026-09-22

### Actionable audit review

- add `bibreview audit --review` as a fully offline, read-only human-review
  surface derived from persisted audit evidence;
- hide equal, formatting-only and provider-missing pairwise noise from the
  review view while preserving every raw canonical/provider value in the report;
- group providers that corroborate the same candidate correction;
- classify provider authors on canonical editor-only records as informational
  contributor-role disagreements rather than automatic author additions;
- keep year/container disagreements out of the actionable view when another
  provider confirms the canonical value.

### Offline reclassification

- add `bibreview audit --reclassify` and dry-run support to reapply current
  comparison rules to an existing report without provider requests;
- rewrite only the audit report while preserving campaign progress, batch
  history, attempt counts and project/canonical state;
- report before/after classification counts plus actionable/informational review
  totals.

### Comparison normalization

- recognize conservative contributor-name variants including initials,
  diacritics, compound-name punctuation, surname-first provider forms and
  common surname particles while preserving author order;
- normalize common TeX/Unicode and rendering artifacts in titles;
- treat near-identical abstracts with prefix/markup/spacing differences as
  formatting-only while preserving real truncations as substantive;
- keep provider disagreement normalization consistent with pairwise comparison
  equivalence.

## 1.3.2 — 2026-09-21

### Audit usability

- pace authenticated Semantic Scholar audit requests at a minimum interval of
  1.1 seconds to respect the provider's introductory one-request-per-second
  API-key limit and reduce HTTP 429 retries;
- keep unauthenticated Semantic Scholar audit behavior unthrottled by BibReview;
- replace the dense one-line audit completion summary with a readable multiline
  batch/campaign progress report;
- document and test that `bibreview.yml` in the current working directory is
  the default configuration, so `--config bibreview.yml` may be omitted.

## 1.3.1 — 2026-09-21

### Campaign schema clarification

- bump resumable campaign checkpoints to schema version 2;
- rename persisted `batch_size` to `default_batch_size` to reflect that
  per-invocation batch-size overrides do not change the campaign default;
- retain read support for schema-version-1 campaign files and migrate them
  losslessly on the next audit-state write;
- preserve stable item snapshots, item states, batch membership, attempt counts,
  and audit reports during migration;
- document that configuration, canonical bibliography, campaign checkpoint, and
  audit-report schemas are versioned independently.

## 1.3.0 — 2026-09-21

### Resumable audit workflow

- add a generic resumable campaign/batching core with stable item snapshots,
  checkpointed batch history, retryable failures, and deterministic JSON state;
- add a pure offline audit comparison engine that distinguishes equal,
  formatting-only, missing, substantive-difference, provider-disagreement, and
  explicit identity-problem cases without deciding which source is authoritative;
- add separate versioned audit campaign/report files, kept outside canonical
  bibliography and collection staging state;
- add `bibreview audit` with one stable batch per invocation, per-publication
  checkpointing, interruption-safe resume, JSON output, and no canonical mutation;
- support pilot-sized batches through per-invocation `--batch-size` overrides
  while preserving the configured default for later batches.

### Audit provider evidence

- add CrossRef audit normalization for bibliographic metadata;
- add DOI metadata lookup through OpenAlex for independent comparison evidence;
- add richer Semantic Scholar paper lookup for audit while preserving the
  minimal abstract-only fallback used by collection;
- reuse optional Semantic Scholar API-key authentication during audit;
- classify provider/network/rate-limit failures separately from canonical
  metadata discrepancies and keep transient failures retryable.

### Safety, documentation, and tests

- keep audit writes restricted to configured campaign/report files;
- preserve provider output as review evidence rather than merge-ready metadata;
- document the pilot/review/batch workflow and audit state model;
- add offline coverage for provider normalization, resume after interruption,
  per-item checkpointing, retry ordering, dry-run behavior, JSON output, and
  pilot-to-default batch sizing.

## 1.2.0 — 2026-09-21

### Provider diagnostics

- add `bibreview providers` for offline inspection of provider enablement,
  configured credential-variable names, and secret provenance;
- add opt-in `bibreview providers --check` live probes with sanitized
  classification of authentication, access, rate-limit, and availability
  failures;
- add JSON diagnostics suitable for tooling and future batch workflows;
- preserve secret non-disclosure across runtime diagnostics and HTTP failures.

### Provider authentication

- support optional Semantic Scholar API keys through the `x-api-key` header;
- add generic runtime provenance tracking so process-environment credentials
  continue to override configured dotenv values without exposing secrets;
- replace manual Mendeley bearer-token configuration with OAuth 2.0
  `client_credentials` using the Mendeley Application ID and Application
  Secret;
- cache Mendeley access tokens for the current process, renew them before expiry,
  and retry once after a catalog HTTP 401;
- add sanitized form-POST transport support for OAuth token exchange.

### Documentation and tests

- document provider credential semantics and the new diagnostics command;
- update the example configuration for Semantic Scholar and Mendeley;
- cover credential precedence, secret non-disclosure, provider status
  classification, Semantic Scholar authentication, and the Mendeley token
  lifecycle.

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
