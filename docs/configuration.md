# Configuration

BibReview reads project policy from **bibreview.yml**. Relative paths are
resolved relative to that file, so a project can be moved as a directory without
rewriting absolute paths.

The repository contains a complete starting point in
[bibreview.example.yml](../bibreview.example.yml).

## Independent schema versions

BibReview deliberately versions different persisted formats independently:

- `bibreview.yml` currently uses **configuration schema 1**;
- the canonical bibliography currently uses **bibliography schema 2**;
- resumable campaign checkpoints currently use **campaign schema 2**;
- the audit report has its own report schema.

A schema-version change in one format does not imply that the others change.
For example, editor support moved the canonical bibliography to schema 2 while
the project configuration remained schema 1.

## Minimal project identity

~~~yaml
schema_version: 1

project:
  name: My Literature Review
  slug: my-literature-review
  title: My literature review
  repository: https://github.com/example/my-review
  contact:
    name: Example Maintainer
    email: maintainer@example.org
~~~

The contact email is required when the optional arXiv module is enabled and is
also used when a provider supports an explicit contact address.

## Environment file

Secrets may be loaded from a dotenv file:

~~~yaml
environment:
  file: .env
~~~

The path is resolved relative to **bibreview.yml**. Values already exported in
the process environment override values from the dotenv file.

Do not commit **.env**.

## Provider response cache

Provider-backed workflows can share a short-lived user-level cache of provider
responses:

~~~yaml
cache:
  enabled: true
  ttl_hours: 6
~~~

The cache is disabled when the block is omitted. When enabled, `ttl_hours` must
be greater than zero. Cache files live in the platform user cache directory
(for example **~/.cache/bibreview/providers-v1/** on Linux, or the corresponding
XDG location) and never inside the project repository.

The cache is an optimization of provider evidence retrieval, not bibliographic
state. An identical deterministic request reuses a fresh entry until its
original network timestamp reaches the configured TTL. Reading an entry does
**not** renew that timestamp. A missing or expired entry requires a live provider
request; stale data is never used silently as fallback after a failed refresh.

Successful provider payloads and deterministic not-found responses may be
cached. Authentication/access failures, rate limits, server failures, transport
errors, and OAuth/form token exchanges do not replace cache entries. A failed
refresh therefore preserves any older entry physically without using it for the
current command.

Two global controls override normal cache use for one invocation:

~~~bash
bibreview --no-cache collect
bibreview --refresh-cache collect
~~~

`--no-cache` bypasses both reads and writes. `--refresh-cache` bypasses reads,
performs live requests, and replaces entries only after cacheable responses.
The options are mutually exclusive. They apply across provider-backed workflows
such as discovery, initialization screening, relevance review, collection,
refresh, backfill, audit, and reference maintenance.

`bibreview providers --check` always performs live diagnostics and bypasses this
cache. The optional arXiv display cache is a separate feature with separate
state and semantics.

## Project paths

~~~yaml
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/author_mappings.json
  known: data/ID.txt
  pending: data/newID.txt
  rejected: data/badID.txt
  review: data/checkID.txt
  bibtex: bib
  imports: data/imports
  archive: archive
  site: site
~~~

See [Data and state files](data-model.md) for the role of each path.

BibReview's recommended project layout deliberately separates canonical project
state from presentation:

~~~text
data/      canonical bibliography, staging, mappings, identifier state and reviewed import evidence
bib/       tracked BibTeX source records
audit/     initialization, audit, and reference campaign evidence
archive/   backups created by reviewed maintenance operations
site/      static-site source and generated presentation artifacts
~~~

The site directory should not be used as canonical bibliographic storage.

The configuration keys remain `known`, `pending`, `rejected`, and `review`
for schema compatibility, but the default filenames are identifier-generic.

Since v1.6.38, these text files use typed tokens:

~~~text
doi:10.1234/example
~~~

The canonical registry (`paths.known`, normally `ID.txt`) may also contain:

~~~text
id:550e8400-e29b-41d4-a716-446655440000
~~~

where `id:` refers exclusively to the canonical top-level `Publication.id`
UUID of a DOI-less publication.

Historical bare DOI lines remain readable during migration, but every rewritten
state file uses the canonical typed form. Untyped non-DOI values are rejected;
BibReview never guesses their identifier type.

## Discovery

~~~yaml
discovery:
  provider: openalex
  query: fluid-structure interaction
  search_field: title_and_abstract
  max_pages: 20
  accepted_types:
    - journal-article
    - proceedings-article
    - book-chapter
    - book
    - monograph
  exclude_doi_substrings: []
~~~

OpenAlex discovery can choose its text-search surface with
`search_field`:

- `title` — search only work titles;
- `abstract` — search only abstracts;
- `title_and_abstract` — search both (the backward-compatible default).

The configured Boolean query is passed unchanged to the selected OpenAlex
`*.search` filter. This lets a project choose recall-oriented discovery across
titles/abstracts or a higher-precision title-only seed corpus without encoding
provider syntax inside the query itself.

For ordinary discovery, `exclude_doi_substrings` is applied during candidate
screening. For a new `bibreview init` campaign, the same exclusions are also
applied **before the stable campaign universe is frozen**, so repository DOI
artifacts such as configured Zenodo/arXiv records never consume initialization
slots.


For a new initialization campaign, `bibreview --dry-run init --json` also
reports OpenAlex discovery diagnostics: the provider's total matching-work
count, pages fetched, works examined, unique DOI candidates found, and whether
the configured `max_pages` limit truncated retrieval. This distinction matters:
the campaign DOI count is not the same quantity as OpenAlex's total query count.

Discovery currently uses OpenAlex to find DOI-backed candidates, then verifies
metadata through CrossRef.

## Relevance rules

~~~yaml
relevance:
  patterns:
    - 'fluid[-\s]+structure'
  reject_patterns:
    - 'experimental[-\s]+test[-\s]+bench'
  unmatched: manual-review
~~~

Both `patterns` and `reject_patterns` are case-insensitive regular
expressions evaluated against the same title/abstract/keyword screening text.

Triage is deliberately conservative:

- an accept-pattern match only goes to the pending collection queue;
- a reject-pattern match only goes to the rejected queue;
- a work matching both accept and reject patterns goes to manual review;
- a work matching neither follows **unmatched**.

**unmatched** can be:

- **manual-review** — unmatched supported works go to the review queue;
- **reject** — unmatched supported works go directly to the rejected queue.

Omitting `reject_patterns` preserves the previous behavior. Prefer narrow,
high-confidence rejection rules; conflicting evidence is intentionally surfaced
for human review instead of being auto-rejected.

## Initialization state

New-project initialization has its own campaign/report pair:

~~~yaml
initialization:
  campaign: audit/init/campaign.json
  report: audit/init/report.json
  batch_size: 50
~~~

The campaign freezes the DOI candidate universe returned by the configured
discovery query. Its batch size controls only newly opened initialization
batches; an already-open batch always resumes with its persisted membership.

Initialization state is separate from `bibliography.json`,
`collected.json`, and the typed acquisition queues. The command observes those
ordinary project files to determine whether the current batch is still pending,
under manual review, staged, merged, or deliberately rejected.

A new initialization campaign refuses to start when canonical bibliography,
canonical staging, or acquisition queues are already non-empty. Once a campaign
exists, later canonical publications are expected because they are the output of
completed initialization batches.

## Refresh policy

Refresh is opt-in:

~~~yaml
refresh:
  types:
    - journal-article
  when_missing_any:
    - volume
    - issue
    - pages
~~~

Only publications of a configured type with at least one configured empty field
are eligible. BibReview compares the stored BibTeX with the current DOI BibTeX;
changed or missing BibTeX can trigger an in-memory recollection and persisted
refresh review.

The remote BibTeX is only a staleness detector. Refresh never stages a complete
provider recollection. Configured fields that are currently empty may become
human-review proposals; meaningful differences affecting already-populated
canonical fields are stored as collateral evidence and cannot be applied through
refresh. Only `refresh --apply`, after explicit `refresh --resolve` decisions,
may stage accepted/custom missing-field fills.

## Audit state

The non-destructive audit workflow keeps its checkpoint and report separate from
canonical bibliography state and collection staging:

~~~yaml
audit:
  campaign: audit/campaign.json
  report: audit/report.json
  batch_size: 50
~~~

**campaign** stores the stable UUID snapshot, batch history and retry state.
**report** stores the latest comparison result for each publication already
processed. A later retry replaces that publication's previous report entry while
the campaign retains the attempt/batch history.

**batch_size** is the default size for newly opened audit batches. A command-line
`--batch-size` value may temporarily override the next new batch without
changing this default or the stable campaign snapshot.

The two paths must be different. Existing projects may omit the whole section;
the defaults shown above are then used.

Audit state is intentionally not placed under **paths.collected** and is never
interpreted as merge-ready bibliographic data.

## Reference refresh state

The provider-driven reference refresh workflow keeps its own resumable campaign
and machine-readable report separate from audit and canonical bibliography
state:

~~~yaml
references:
  campaign: audit/references/campaign.json
  report: audit/references/report.json
  batch_size: 50
~~~

**campaign** stores the stable canonical publication UUID snapshot, batch
history, attempts, and retry state. **report** stores the latest reconstructed
reference comparison for each processed publication.

**batch_size** is the number of parent publications placed in each newly opened
BibReview campaign batch. It is not the CrossRef API batch limit: BibReview
automatically splits parent DOI lookup into CrossRef-supported chunks of at most
25 DOI values.

Existing projects may omit the whole section; the defaults above are used.

The v1.6.28 defaults intentionally differ from the short-lived v1.6.27
`data/references/` location. This starts a fresh refined campaign after the
first PHRAISE observation exposed artificial citation drift from empty parent
CrossRef citation payloads. Explicitly configured paths are never rewritten.

The campaign and report paths must differ from one another and may not overlap
canonical bibliography, collected staging, author mappings, DOI queue/review
files, or audit campaign/report paths. This prevents a configuration typo from
turning reference-maintenance state into canonical data.

In v1.6.28 these files are evidence/checkpoint state only. No reference result
is merge-ready, and there is deliberately no `references --apply` yet.

## Providers and secrets

CrossRef is the mandatory metadata provider for DOI-backed workflows.

Optional provider examples:

~~~yaml
providers:
  crossref:
    enabled: true
    min_interval_seconds: 0.0

  openalex:
    enabled: true
    min_interval_seconds: 0.0
    api_key_env: OPENALEX_API_KEY

  elsevier:
    enabled: true
    min_interval_seconds: 0.0
    api_key_env: ELSEVIER_API_KEY

  springer:
    enabled: true
    min_interval_seconds: 0.0
    api_key_env: SPRINGER_API_KEY

  ieee:
    enabled: true
    min_interval_seconds: 0.0
    api_key_env: IEEE_API_KEY

  semantic_scholar:
    enabled: true
    min_interval_seconds: 1.1
    api_key_env: SEMANTIC_SCHOLAR_API_KEY

  mendeley:
    enabled: true
    min_interval_seconds: 0.0
    client_id_env: MENDELEY_CLIENT_ID
    client_secret_env: MENDELEY_CLIENT_SECRET
~~~

A corresponding private **.env** may contain:

~~~text
OPENALEX_API_KEY=...
ELSEVIER_API_KEY=...
SPRINGER_API_KEY=...
IEEE_API_KEY=...
SEMANTIC_SCHOLAR_API_KEY=...
MENDELEY_CLIENT_ID=...
MENDELEY_CLIENT_SECRET=...
~~~

Optional providers whose required configured secret is missing are skipped with
a warning. OpenAlex and Semantic Scholar can run without API keys, but supplying
their optional keys may provide more predictable rate limits. When the publisher
and CrossRef provide no abstract, enabled OpenAlex, Semantic Scholar and Mendeley
providers participate in the optional abstract fallback; BibReview keeps the
longest valid fallback abstract returned for that DOI.

Every provider block accepts `min_interval_seconds`, a non-negative number that
sets the minimum interval between the **start times** of consecutive HTTP
requests for that provider. The default is `0.0` (no BibReview-imposed delay).
Intervals are provider-local: throttling Semantic Scholar does not slow CrossRef,
OpenAlex, IEEE, or any other provider. Configure the value according to the
provider's current API policy; changing the YAML is sufficient when upstream
rate-limit guidance changes.

HTTP 429 handling is provider-local as well. BibReview performs at most two
provider-aware retries after the initial request. A valid `Retry-After` response
header takes precedence; otherwise BibReview uses a bounded exponential fallback
starting at the greater of one second and the configured minimum interval. Every
retry still passes through the same provider-local request-spacing gate. A
persistent HTTP 429 is then propagated to the workflow, where optional fallback
providers may be disabled for the remainder of that run. Generic HTTP transport
retries remain reserved for transient 5xx server failures.

The example uses `1.1` seconds for Semantic Scholar and `0.0` for the other
providers. This is project policy, not a hard-coded BibReview special case.

For Mendeley, configure the **Application ID** and **Application Secret** from
the Mendeley Developer Portal. BibReview uses the OAuth 2.0
`client_credentials` flow to request a short-lived bearer access token from
Mendeley, caches it for the current process, and automatically requests a new
token before expiry. The application secret is never used directly as a bearer
token and is never printed by diagnostics.

### Provider diagnostics

Inspect provider configuration and credential provenance without making network
requests:

~~~bash
bibreview --config bibreview.yml providers
~~~

The report shows the configured environment-variable name, credential source,
and effective minimum request interval. It never prints the secret value itself.

To perform one sanitized live request per provider that is ready to use:

~~~bash
bibreview --config bibreview.yml providers --check
~~~

Live diagnostics classify common conditions such as authentication failure
(HTTP 401), access/entitlement denial (HTTP 403), rate limiting (HTTP 429), and
temporary provider/server unavailability. Provider URL paths, query parameters,
headers, and credentials remain hidden from diagnostic errors.

Machine-readable output is available with:

~~~bash
bibreview --config bibreview.yml providers --json
bibreview --config bibreview.yml providers --check --json
~~~

If an enrichment provider is producing incorrect data, disable that provider
temporarily and recollect the affected staging data. See
[Manual corrections](corrections.md).

## Optional arXiv feed

arXiv is deliberately independent from the canonical bibliography:

~~~yaml
arxiv:
  enabled: true
  query: all:fluid AND all:structure
  max_results: 25
  sort_by: lastUpdatedDate
  sort_order: descending
  output: site/assets/data/arxiv.json
~~~

The arXiv cache is display-only. It does not create canonical publications and
does not change the bibliography update date.

## Site rendering

~~~yaml
site:
  enabled: true
  implementation: jekyll
  source: site
  jekyll:
    publish_data: false
    category_by_type:
      journal-article: articles
      proceedings-article: proceedings
      book-chapter: chapters
      book: books
      monograph: books
    event_category_rules:
      - pattern: 'Conference|Workshop|Symposium'
        category: proceedings
    isbn_types:
      - book
      - monograph
    include_authorless_year_publications: true
~~~

`include_authorless_year_publications` is retained for compatibility with
existing project configurations. In canonical bibliography schema version 2,
a valid publication always has at least one author or editor; editor-only publications
are therefore rendered even when this option is `false`.

**bibreview render** owns generated publication posts, author pages and year
pages. It does not own your Jekyll theme, layouts, CSS, deployment or analytics.

When `site.jekyll.publish_data: true`, render also owns the dedicated
`assets/data/bibreview/` subtree below the configured site source and publishes
read-only snapshots of the canonical `bibliography.json` and
`author_mappings.json`, and also publishes the tracked BibTeX sources under
the configured `site.jekyll.bibtex_asset_prefix` (default: `assets/bib`).
These files are generated presentation artifacts, not canonical state. Other
site data such as `assets/data/arxiv.json` remains outside those managed
subtrees.

Continue with [Local workflow](workflow.md) and
[GitHub Pages](github-pages.md).


### Manual import evidence

`paths.imports` defaults to `data/imports`. It stores normalized, reviewed
YAML sidecars for DOI-less publications staged through `bibreview import`.
These files retain the persistent BibReview UUID, provenance, reviewed metadata,
citation text when supplied, and the reviewed BibTeX after normalization to
BibReview's tracked canonical format. Non-citation payload fields such as
`abstract`, `month`, `url`, and `pdf` are omitted from that BibTeX. The
sidecars are evidence, not canonical bibliography state: promotion still
occurs only through `collected.json` followed by `bibreview merge`.
