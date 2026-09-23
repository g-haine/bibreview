# Changelog

All notable BibReview releases are documented here.

## 1.6.17 — 2026-09-23

### Semantic script-markup hygiene detection

- classify inline subscript/superscript markup separately from ordinary
  structural HTML/XML, including the IEEE-style `<inf>` tag and standard
  `<sub>` / `<sup>` tags;
- require human review for these records because structural unwrapping would
  preserve the characters but lose their mathematical position semantics;
- add regression coverage modelled on PHRAISE DOI
  `10.1109/cdc.2005.1583059`, where equilibrium variables carry subscript zero
  and a supply-rate expression contains a superscript transpose;
- keep Phase 1 strictly read-only: no formula reconstruction and no canonical
  mutation.

### PHRAISE inventory refinement

- keep 2,346 canonical publications, 2,326 abstracts present, and 21 suspicious
  abstracts;
- refine the classification from 16 to **15 apparent deterministic candidates**
  and from 5 to **6 review-required cases**;
- report one `script-markup` finding in addition to the existing
  `embedded-graphic` finding.

### Validation

- add regression coverage for scientific subscript/superscript markup;
- validate the release through the pull-request CI before merge.

## 1.6.16 — 2026-09-23

### Embedded formula-image hygiene detection

- classify embedded graphical payloads such as JATS `inline-graphic`,
  `graphic`, HTML `img`, and `image` separately from ordinary structural
  markup in canonical abstracts;
- require human review whenever an embedded graphic is present instead of
  classifying a balanced graphic tag as safe structural unwrapping;
- preserve the Phase 1 read-only boundary: no image is downloaded, interpreted,
  OCRed, or replaced automatically;
- add regression coverage for the historical PHRAISE abstract
  `10.1049/iet-cta.2017.0392`, whose two inline PNG resources represent
  mathematical expressions without a trustworthy textual fallback.

### PHRAISE inventory refinement

- keep 2,346 canonical publications, 2,326 abstracts present, and 21 suspicious
  abstracts;
- refine the classification to 16 apparent deterministic candidates and
  5 review-required cases;
- report one `embedded-graphic` finding containing two inline graphic
  occurrences.

### Validation

- add regression coverage for embedded formula images in canonical abstracts;
- validate the release with 463 passing tests.

## 1.6.15 — 2026-09-23

### Read-only canonical abstract hygiene inventory

- add the offline `bibreview hygiene` command for detecting historical
  structured-markup contamination in canonical abstracts without modifying
  project state;
- classify legacy renderer markers, `inline-formula`, MathML, JATS-like
  markup, XML comments, escaped markup, generic HTML/XML tags, and obviously
  unbalanced structured tags;
- report aggregate family counts by default, bounded publication-level context
  with `-v`, and a complete machine-readable inventory with `--json`;
- distinguish apparent deterministic-cleanup candidates from records that still
  require human review, while deliberately performing no normalization in this
  phase;
- recognize embedded `application/x-tex` annotations as evidence for a later
  lossless MathML-to-TeX normalization path.

### Validation

- cover clean text/TeX no-op behavior, MathML with and without embedded TeX,
  structural HTML/JATS, escaped markup, legacy renderer markers, unbalanced
  tags, aggregate formatting, JSON output, and read-only CLI behavior;
- validate the release with 462 passing tests;
- validate the scanner against the current PHRAISE canonical bibliography:
  2,346 publications, 2,326 abstracts present, 21 suspicious abstracts,
  17 apparent deterministic candidates, and 4 review-required cases.

## 1.6.14 — 2026-09-23

### Lossless Jekyll Liquid escaping

- stop rewriting every closing `}}` sequence in rendered scholarly text;
- preserve ordinary TeX constructs such as `\\lambda_{\\mathrm{out}}`,
  `{\\mathcal H}_2`, and `A_{\\text{ext}}` byte-for-byte in generated
  publication body text;
- protect only actual Liquid openers (`{{` and `{%`) in page-body content
  with self-contained Liquid `raw` blocks, so the rendered website recovers
  the original literal characters;
- keep front-matter values as YAML data instead of injecting Liquid escape
  markup into titles or tags;
- leave canonical bibliography data untouched: the fix is confined to the
  deterministic Jekyll presentation layer.

### Validation

- cover TeX closing-brace preservation in abstracts;
- cover reversible escaping of literal Liquid output/tag openers;
- cover literal braces in front-matter titles;
- validate the release with 449 passing tests.

## 1.6.13 — 2026-09-23

### Batched backfill enrichment

- add reusable batch collection contracts for exact multi-DOI work lookup and
  enrichment so the same architecture can later be reused by refresh;
- prefetch backfill CrossRef work metadata in bounded groups of 25 DOI values
  while preserving canonical input order and proposal ordering;
- add batched OpenAlex abstract fallback in bounded groups of 100 DOI values;
- add batched Semantic Scholar abstract fallback through the paper batch endpoint
  in bounded groups of 500 DOI values;
- keep publisher enrichment per DOI because routing depends on the resolved
  publisher host, and keep Mendeley per DOI because its current adapter exposes
  no exact multi-DOI lookup;
- fall back to individual DOI requests when a CrossRef batch or a non-429
  optional-provider batch fails;
- preserve run-scoped Semantic Scholar/OpenAlex disabling after a persistent
  provider-aware HTTP 429;
- isolate missing or failed base-work DOI records so unrelated backfill
  candidates continue;
- keep review files, apply semantics, stale-value protection, and canonical
  promotion boundaries unchanged.

### Validation

- cover CrossRef-style chunking and proposal-order preservation;
- cover missing records and failed work-batch fallback;
- cover OpenAlex and Semantic Scholar batched abstract adapters;
- cover provider batch-size chunking, persistent 429 handling, and individual
  fallback after non-429 batch failures;
- cover batched enrichment composition and runtime wiring;
- validate the release with 446 passing tests.

## 1.6.12 — 2026-09-23

### Provider-aware HTTP 429 recovery

- remove HTTP 429 from the generic urllib3 retry policy so hidden transport
  retries can no longer bypass provider-local request pacing;
- keep transient 5xx server failures on the existing bounded generic retry path,
  including normal server `Retry-After` handling;
- preserve a parsed HTTP 429 `Retry-After` delay on `HttpError` for the
  provider-local transport layer;
- retry HTTP 429 responses at most twice after the initial request, with every
  retry passing through the same provider-local request-spacing gate;
- honor `Retry-After` when supplied by the provider, otherwise use bounded
  exponential backoff starting at the greater of one second and the configured
  minimum request interval;
- keep persistent HTTP 429 propagation unchanged after provider-aware retries,
  so existing run-scoped optional-provider disabling remains the final fallback;
- expose provider retry attempts in `-vv` diagnostics without revealing request
  paths, parameters, headers, or credentials.

### Validation

- cover separation of HTTP 429 from generic 5xx retries;
- cover propagation of `Retry-After` into the provider-local retry layer;
- cover successful recovery after a transient HTTP 429;
- cover propagation after two failed provider-aware retries;
- validate the release with 436 passing tests.

## 1.6.11 — 2026-09-23

### Observable provider request pacing

- expose provider-local request pacing in `-vv` diagnostics without changing
  network behavior;
- report the configured minimum request interval for the first provider request;
- report the actual elapsed interval between consecutive request starts;
- report the requested sleep when BibReview must wait for the configured request
  slot, and distinguish calls that require no wait;
- keep diagnostics tied to the existing provider request context so logs remain
  attributable without exposing URL paths, query strings, headers, or secrets;
- leave HTTP retry policy, provider fallback ordering, and run-scoped provider
  disabling unchanged.

### Validation

- cover first-request pacing diagnostics;
- cover an enforced wait and the resulting effective interval;
- cover the no-wait path when unrelated work already spaces provider requests;
- validate the release with 433 passing tests.

## 1.6.10 — 2026-09-23

### Abstract placeholder cleanup

- treat the historical canonical abstract placeholder `Not Available` as
  semantically missing, case- and whitespace-insensitively;
- keep this placeholder policy specific to the `abstract` field so unrelated
  scalar metadata is not reinterpreted;
- make reviewed backfill propose real abstracts for historical placeholder
  values instead of skipping them as non-empty;
- allow reviewed backfill and refresh application to replace the placeholder
  while preserving stale-value protection for meaningful canonical abstracts;
- reject accepted/custom abstract values that resolve back to the placeholder;
- normalize provider-side `Not Available` abstract values to empty before
  collection, fallback selection, and audit comparison;
- stop the optional abstract fallback from manufacturing `Not available` when
  no real abstract is available;
- reuse the same semantic missing-value policy across backfill and safe refresh.

### Validation

- cover case/whitespace-insensitive placeholder detection;
- cover backfill proposal and apply from historical placeholder records;
- cover rejection of placeholder provider/custom values;
- cover fallback, collection, refresh, and CrossRef/OpenAlex/Semantic Scholar
  audit normalization;
- validate the release with 432 passing tests.

## 1.6.9 — 2026-09-23

### Configurable provider request pacing

- add `providers.<name>.min_interval_seconds` to configuration schema 1 for
  every metadata provider;
- interpret the value as the minimum interval between the start times of
  consecutive HTTP requests for that provider;
- default to `0.0`, preserving no BibReview-imposed delay unless explicitly
  configured;
- apply provider-local pacing consistently across collect, backfill, refresh,
  discover, audit, and live provider diagnostics;
- keep each provider on an independent limiter so throttling one service never
  delays unrelated provider requests;
- remove the hidden audit-only Semantic Scholar 1.1-second runtime policy;
  projects now express that policy explicitly in YAML;
- retain the provider adapter's direct interval option for isolated adapter tests
  while runtime composition uses the generic transport-level limiter;
- expose the effective minimum interval in human and JSON provider diagnostics.

### Configuration and documentation

- update the example project configuration with explicit request intervals for
  every provider;
- use `1.1` seconds for Semantic Scholar in the example and `0.0` for the
  remaining providers;
- document that upstream policy changes can be handled by editing YAML without
  changing BibReview code;
- validate non-negative integer and floating-point interval values and reject
  booleans, strings, and negative values.

### Validation

- add provider-local timing tests proving that only the remaining interval is
  slept and that `0.0` never sleeps;
- add configuration default/validation tests;
- verify interval wiring for CrossRef, OpenAlex, Elsevier, IEEE,
  Semantic Scholar, and Mendeley across runtime workflows;
- verify Semantic Scholar is not double-throttled;
- verify diagnostic text and JSON expose configured intervals;
- validate the release with 417 passing tests.

## 1.6.8 — 2026-09-23

### Optional publisher failure isolation

- keep optional publisher enrichment failures from aborting collect, backfill,
  refresh, or discovery workflows;
- treat DOI publisher-lookup HTTP failures as non-fatal enrichment misses;
- disable Elsevier, Springer, or IEEE for the remainder of a run after persistent
  HTTP 401, 403, or 429 responses;
- skip only the current publication for other HTTP/transport/response failures,
  allowing later publications to retry the provider;
- continue to configured abstract fallbacks such as OpenAlex, Semantic Scholar,
  and Mendeley after publisher enrichment is skipped;
- keep non-HTTP contract/type errors fatal so implementation defects are not
  silently hidden;
- route all optional-provider failure diagnostics through the normal Reporter.

### Validation

- add regression coverage for IEEE HTTP 403 run-scoped disabling, publisher
  HTTP 503 isolation, DOI publisher-lookup failure isolation, and propagation
  of non-HTTP provider errors;
- validate the release with 411 passing tests.

## 1.6.7 — 2026-09-23

### Partial-provider backfill safety

- allow reviewed scalar-field backfill to extract only the requested metadata
  instead of constructing a complete provider `Publication`;
- prevent unrelated provider omissions such as missing authors/editors from
  aborting `bibreview backfill --field abstract`;
- validate only metadata required by the requested backfill field, so an
  abstract proposal does not depend on unrelated creation-date or contributor
  completeness;
- preserve strict canonical validation for normal collection of new
  publications: new records still require at least one author or editor;
- preserve existing abstract cleanup, enrichment fallback, page-range, and
  scalar metadata normalization rules;
- ensure normal publication collection invokes optional enrichment exactly once.

### Validation

- add regression coverage for abstract backfill from a provider record with no
  authors, editors, or creation date;
- retain regression coverage rejecting newly collected publications without
  authors/editors;
- validate the release with 407 passing tests.

## 1.6.6 — 2026-09-23

### Non-destructive reviewed refresh

- stop `refresh` from writing complete recollected publications directly to
  `collected.json`;
- use the remote DOI BibTeX only as a staleness detector and never as a wholesale
  replacement for tracked local BibTeX;
- compare stale recollections field-by-field with canonical metadata using the
  established audit-equivalence rules;
- turn only configured fields that are currently empty into safe refresh
  proposals;
- retain every meaningful difference affecting an already-populated canonical
  field as explicit collateral evidence that refresh cannot promote;
- add offline `refresh --review` output, with verbose current/provider values
  for every collateral difference;
- add resumable `refresh --resolve` decisions by reusing the backfill
  accept/reject/custom/defer/quit resolution model;
- add `refresh --apply` to stage only accepted/custom missing-field fills after
  rechecking that the canonical field is still empty;
- keep `bibreview merge` as the only canonical promotion boundary.

### BibTeX safety

- remove the old whole-file remote BibTeX replacement behavior from refresh;
- synchronize only reviewed accepted fields in the existing tracked BibTeX;
- reuse the conservative single-entry field editor already used by audited
  corrections;
- create an archive backup before each reviewed BibTeX field-level edit;
- leave unrelated manually corrected BibTeX content untouched.

### Shared reviewed-field logic

- expose audit pair classification for reuse by refresh;
- share reviewed canonical-field application and BibTeX mapping/rendering helpers
  between `audit --apply` and `refresh --apply`;
- reuse backfill resolution state and decision mechanics for refresh instead of
  maintaining a second human-decision implementation.

### Validation

- add regression coverage proving that refresh scanning does not write staging or
  replace tracked BibTeX;
- cover safe missing-field proposals, collateral non-promotion, offline review,
  interactive resolution, stale-proposal blocking, targeted BibTeX editing and
  backups, and preserved reviewed title/author metadata;
- validate the release with 405 passing tests.

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
  CLI resolution/application;
- validate the release with 394 passing tests.

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
