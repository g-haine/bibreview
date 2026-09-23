# Changelog

All notable BibReview releases are documented here.

## 1.6.5 — 2026-09-23

### Human-reviewed missing-field backfill

- add `bibreview backfill --field FIELD` to propose values only for selected
  canonical scalar fields that are currently empty;
- keep proposal generation outside `collected.json` and canonical bibliography
  state, with versioned local proposal state beside the audit files;
- add `bibreview backfill --resolve` with the same resumable accept/reject/custom/
  defer/quit interaction model used by audit resolution;
- fingerprint human decisions against the exact proposal set so stale decisions
  cannot be silently reused after proposals change;
- add `bibreview backfill --apply` to stage only accepted/custom decisions after
  every proposal has a final decision;
- revalidate canonical state at apply time and refuse to overwrite a field that
  has become non-empty since proposal generation;
- keep `bibreview merge` as the only canonical promotion boundary;
- do not rewrite tracked BibTeX during missing-field backfill.

### Backfill safety and scope

- never propose replacement values for non-empty canonical fields;
- allow repeated `--field` and optional repeated `--type` selectors during
  proposal generation;
- refuse proposal generation or application when normal `collected.json`
  staging is already occupied;
- preserve publication UUIDs, DOI identity, permalink, authors, title, and every
  other reviewed field unless that exact empty field receives an accepted/custom
  human decision;
- show full proposal text wrapped for interactive review, including abstracts.

### Documentation

- document proposal, resolution, application, stale-state protection, and the
  normal staging/merge boundary in README, command reference, workflow, data
  model, and correction guidance;
- clarify that persistent audit history may append newly added canonical UUIDs
  while preserving historical batch membership.

### Validation

- add regression coverage for proposal-only generation, protection of non-empty
  fields, resumable/fingerprinted decisions, accepted/rejected application,
  incomplete-resolution blocking, stale canonical protection, and interactive
  CLI resolution/application.

## 1.6.4 — 2026-09-23

### OpenAlex abstract enrichment

- allow enabled OpenAlex to participate in canonical abstract fallback when
  publisher and CrossRef enrichment leave the abstract empty;
- reuse one OpenAlex abstract reconstruction path for audit evidence and
  collection fallback;
- preserve the existing fallback policy of selecting the longest available
  valid abstract across Semantic Scholar, Mendeley, and OpenAlex;
- reject the observed OpenAlex non-abstract placeholders `Accepted version`
  and `International audience`;
- strip the OpenAlex `View Video Presentation` DOI prefix while retaining the
  following abstract text;
- disable only OpenAlex fallback for the remainder of a run after HTTP 429,
  while continuing to try other configured fallback providers;
- reuse the same configured OpenAlex provider across discovery and collection
  composition.

### Abstract normalization

- normalize every optional fallback candidate before comparing candidate lengths;
- trim surrounding whitespace and normalize line-break whitespace in abstracts;
- remove leading abstract labels only at the start of the text, including common
  English, French, Spanish, Portuguese, German, Italian, and Dutch forms;
- preserve occurrences of words such as `abstract` and `summary` inside the
  actual abstract body instead of deleting them globally.

### Incremental audit history

- make normal `bibreview audit` runs persistent and incremental: completed
  publication UUIDs stay visited while newly added canonical publications are
  appended as pending work;
- retain retryable provider failures and existing batch history without
  re-requesting already completed publications;
- add `bibreview audit --full` to deliberately requeue every publication
  currently present in the canonical bibliography while preserving attempt and
  report history;
- refuse a full reset while an interrupted batch remains open, so resumability
  stays explicit.

### Validation

- add regression coverage for multilingual leading-label cleanup, body-text
  preservation, cleaned-before-length fallback selection, incremental audit
  extension, full requeue behavior, and CLI `--full` forwarding;
- add regression coverage for OpenAlex inverted-index reconstruction,
  placeholder rejection, video-prefix cleanup, fallback selection, and
  run-scoped rate-limit handling;
- validate the feature with 384 passing tests before release.

## 1.6.3 — 2026-09-23

### CrossRef article-number pagination

- use CrossRef `article-number` as the bibliographic page locator when the
  ordinary `page` field is absent or blank;
- keep an explicit CrossRef `page` value authoritative when both fields exist;
- preserve article numbers exactly as strings, including leading zeroes such as
  `034312`;
- share the same CrossRef locator rule between collection and audit evidence so
  the two workflows cannot drift;
- keep the canonical schema unchanged: the existing `pages` field remains the
  generic bibliographic locator.

### Validation

- add unit coverage for page precedence, article-number fallback, blank values,
  and leading-zero preservation;
- add collection and audit regression coverage for article-number-only CrossRef
  records;
- validate the correction with 371 passing tests before release.

## 1.6.2 — 2026-09-22

### Audit page-range normalization

- treat singleton page ranges such as `261--261` and `261` as
  review-equivalent formatting variants during audit comparison;
- preserve raw canonical and provider page values unchanged in persisted audit
  evidence;
- keep genuine non-singleton ranges distinct from single-page values;
- allow existing audit reports to adopt the corrected comparison rule through
  the offline `bibreview audit --reclassify` workflow.

### Validation

- add regression coverage for the singleton page-range forms observed in the
  PHRAISE audit and for a genuine non-singleton range;
- validate the correction with 368 passing tests before release.

## 1.6.1 — 2026-09-22

### Explicit audit-apply no-op reporting

- report accepted/custom audit resolutions whose resolved value is already equal
  to the current canonical value instead of silently omitting them from the
  application summary;
- add a `No-op resolutions` count to human-readable `audit --apply` output;
- expose structured no-op details in JSON application plans;
- show DOI, title, field, unchanged value, and decision source in verbose
  application output;
- keep no-op resolutions out of `collected.json`, BibTeX updates, backups, and
  affected-publication counts.

### Validation

- add regression coverage for accepted and custom no-op resolutions;
- verify summary, verbose, and JSON reporting while preserving write-free no-op
  behavior;
- validate the correction with 366 passing tests before release.

## 1.6.0 — 2026-09-22

### Audited correction promotion

- add `bibreview audit --apply` as an offline, explicit promotion step for
  completed human audit resolutions;
- require a complete resolution set, revalidate the review fingerprint, and
  reject stale canonical values before staging any correction;
- reuse the normal `collected.json` staging boundary and refuse to overwrite a
  non-empty collect/refresh batch;
- stage only accepted/custom decisions while leaving rejected findings
  unchanged;
- synchronize applicable tracked BibTeX fields at apply time, with archive
  backups before replacement;
- add a conservative single-entry BibTeX field editor with no new dependency;
- abort safely when required BibTeX is missing/malformed or a correction cannot
  be represented without guessing;
- preserve contributor decisions without inferring given/family components;
- support dry-run, verbose, JSON, and quiet application modes while keeping
  `bibreview merge` as the only canonical promotion boundary.

### Validation

- add regression coverage for application planning, staging guards, stale
  canonical detection, contributor preservation, BibTeX synchronization and
  backups, JSON-only fields, no-op rejected decisions, dry-run/JSON CLI modes,
  and conservative BibTeX parsing/editing;
- validate the feature with 364 passing tests before release.

## 1.5.3 — 2026-09-22

### Interactive terminal line editing

- initialize Python's standard `readline` module before interactive
  `bibreview audit --resolve` input when available;
- restore normal terminal editing on supported terminals, including left/right
  arrows, Home/End, Backspace/Delete, and input history;
- fall back gracefully to the platform's default input behavior when
  `readline` is unavailable;
- add no runtime dependency and preserve audit, resolution, fingerprint, and
  canonical/staging semantics unchanged.

### Validation

- add regression coverage for readline initialization and unavailable-module
  fallback;
- verify that interactive audit resolution initializes line editing before
  reading decisions;
- validate the correction with 349 passing tests before release.

## 1.5.2 — 2026-09-22

### Natural tuple-valued audit corrections

- allow tuple-valued custom corrections in `bibreview audit --resolve` to use
  the same semicolon-separated representation displayed by the interactive CLI;
- accept natural contributor input such as
  `f Nguyen Thanh Sang; Tan Chee Keong; Hussain Mohd Azlan`;
- retain JSON string-array input for explicit or complex tuple corrections;
- trim surrounding whitespace, reject empty tuple items, and leave scalar
  custom-value behavior unchanged;
- preserve audit evidence, review fingerprints, resolution resumability, and
  canonical/staging state semantics.

### Validation

- add unit coverage for semicolon-separated tuples, whitespace trimming,
  empty-item rejection, single-item tuples, and retained JSON compatibility;
- add CLI regression coverage for semicolon-separated author correction input;
- validate the correction with 347 passing tests before release.

## 1.5.1 — 2026-09-22

### Audit page-range proposals

- normalize human-facing `pages` proposals in `bibreview audit --resolve` to
  BibTeX-style double hyphens;
- map provider page ranges such as `8793-8805`, `8793–8805`, and
  `8793—8805` to the proposed value `8793--8805`;
- preserve raw provider evidence unchanged in the audit report and keep
  non-page proposal semantics unchanged.

### Validation

- add regression coverage for ASCII, Unicode, mixed-provider, and already
  canonical double-hyphen page ranges;
- validate the correction with 343 passing tests before release.

## 1.5.0 — 2026-09-22

### Interactive audit resolution

- add `bibreview audit --resolve` as an offline, resumable human-review workflow
  for actionable audit findings;
- present one actionable finding at a time with canonical value, provider
  evidence, and an exact common proposal when a safe default representation
  exists;
- support explicit accept, reject, custom-value, defer, and quit decisions with
  `Y`, `n`, `f VALUE`, `s`, and `q`;
- require an explicit custom value when corroborating providers agree only after
  review normalization but retain different raw representations;
- persist decisions atomically in a versioned `resolutions.json` file beside
  the audit report and fingerprint them against the exact actionable review;
- reject stale resolution state after the underlying actionable evidence or
  review classification changes;
- keep resolution decisions separate from canonical bibliography and collection
  staging; accepted/custom decisions are not promoted automatically;
- support interactive `--dry-run` without writing resolution state.

### Validation

- add regression coverage for exact proposals, custom scalar and tuple values,
  reject/defer semantics, resumability, stale-review detection, quit/resume,
  dry-run behavior, and incompatible interactive output modes;
- validate the feature with 340 passing tests before release.

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
