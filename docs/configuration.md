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

## Project paths

~~~yaml
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/authors.json
  known: data/known.txt
  pending: data/pending.txt
  rejected: data/rejected.txt
  review: data/review.txt
  bibtex: bib
  archive: archive
  site: site
~~~

See [Data and state files](data-model.md) for the role of each path.

## Discovery

~~~yaml
discovery:
  provider: openalex
  query: fluid-structure interaction
  max_pages: 20
  accepted_types:
    - journal-article
    - proceedings-article
    - book-chapter
    - book
    - monograph
  exclude_doi_substrings: []
~~~

Discovery currently uses OpenAlex to find DOI-backed candidates, then verifies
metadata through CrossRef.

## Relevance rules

~~~yaml
relevance:
  patterns:
    - 'fluid[-\s]+structure'
  unmatched: manual-review
~~~

Patterns are regular expressions. **unmatched** can be:

- **manual-review** — unmatched supported works go to the review queue;
- **reject** — unmatched supported works go directly to the rejected queue.

Prefer conservative patterns and human review over an over-aggressive reject
rule.

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
  campaign: data/audit/campaign.json
  report: data/audit/report.json
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
  output: site/data/arxiv.json
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

Continue with [Local workflow](workflow.md) and
[GitHub Pages](github-pages.md).
