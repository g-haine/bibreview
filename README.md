# BibReview

**BibReview is a generic, human-reviewed bibliographic engine for reproducible
literature databases and static scholarly websites.**

It discovers DOI-backed publications, collects and enriches metadata, keeps
reviewable canonical project state, helps resolve author identities, renders
Jekyll bibliography pages, and can optionally maintain a display-only arXiv
feed.

BibReview is designed so that provider output remains inspectable and ambiguous
decisions remain human decisions.

Current stable release: **v1.2.0**.

## What BibReview provides

- OpenAlex discovery with configurable relevance rules;
- CrossRef-backed DOI metadata collection;
- optional publisher and abstract enrichment providers;
- safe provider/credential diagnostics with optional live checks;
- resumable, non-destructive audits of existing canonical metadata;
- explicit pending, review, rejected and collected states;
- persistent publication UUIDs independent from DOI representation;
- reviewed author-name mapping with safe and ambiguous proposals;
- BibTeX retrieval and tracked source files;
- refresh/recollection of selected incomplete publications;
- deterministic Jekyll publication, author and year rendering;
- an optional arXiv feed-cache module, separate from the canonical bibliography;
- dry-run planning for mutating workflows;
- atomic-per-file persistence and explicit backups.

## Quick start

BibReview currently requires **Python 3.12 or newer**.

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.2.0"

bibreview --version
~~~

On Windows PowerShell, activate the environment with:

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

Start a project from
[bibreview.example.yml](bibreview.example.yml), then validate it:

~~~bash
bibreview --config bibreview.yml validate
bibreview --config bibreview.yml status
~~~

See the complete [installation guide](docs/installation.md) for Linux, macOS and
Windows.

## Typical maintenance cycle

~~~text
discover
   ↓
human review of uncertain DOI candidates
   ↓
refresh existing incomplete records
   ↓
merge
   ↓
collect new pending DOI values
   ↓
inspect/correct staging + BibTeX
   ↓
merge
   ↓
authors
   ↓
human resolution of ambiguous identities
   ↓
render
~~~

The optional arXiv feed runs independently:

~~~text
arxiv → display-only JSON cache
~~~

A curated project should inspect staged metadata and BibTeX before merging.

## Commands

| Command | Purpose |
|---|---|
| **validate** | Validate project configuration. |
| **status** | Show the resolved project configuration summary. |
| **providers** | Inspect credential provenance and optionally live-check providers. |
| **audit** | Audit one stable batch of existing canonical publications. |
| **discover** | Discover and screen new DOI candidates. |
| **collect** | Collect pending DOI metadata into canonical staging. |
| **refresh** | Recollect selected incomplete existing publications. |
| **merge** | Merge reviewed staging into the canonical bibliography. |
| **authors** | Analyze and safely extend author identity mappings. |
| **render** | Reconcile generated Jekyll bibliography artifacts. |
| **arxiv** | Refresh the optional display-only arXiv cache. |

Use **--dry-run** with mutating workflows when you want to inspect the plan
without writing project files.

Full details: [command reference](docs/commands.md).

## Project state is explicit

BibReview keeps canonical and intermediate state visible in ordinary files:

~~~text
bibliography.json    canonical reviewed bibliography
collected.json       current collection/refresh staging batch
known.txt            accepted DOI state
pending.txt          DOI values waiting for collection
review.txt           DOI values requiring human relevance review
rejected.txt         deliberately excluded DOI values
authors.json         reviewed author-name mappings
bib/                 tracked BibTeX sources
archive/             backups created by refresh/merge
~~~

The canonical bibliography is a versioned JSON document with global metadata,
including the bibliography update date.

See [Data and state files](docs/data-model.md).

## Provider output can be corrected

Provider metadata is not treated as infallible.

BibReview supports a review-first workflow where you can correct staged JSON or
BibTeX before merge. Missing or invalid BibTeX is treated as missing data rather
than replaced by a fake placeholder.

For recovery procedures, persistent provider errors, manual corrections and
post-merge repairs, see
[Manual corrections and provider errors](docs/corrections.md).

## Ambiguous authors stay human-reviewed

BibReview can automatically apply only unambiguous author mappings:

~~~bash
bibreview --config bibreview.yml authors --apply-safe
~~~

Possible identity collisions remain under manual review.

See [Author identities and ambiguous names](docs/authors.md).

## Static sites and GitHub Pages

BibReview renders bibliographic content into an existing Jekyll site; themes,
layouts, CSS and deployment remain project-owned.

The documentation includes a complete guide for:

- local Jekyll preview;
- GitHub Pages deployment with GitHub Actions;
- a pinned BibReview installation in CI;
- scheduled arXiv refresh;
- optional scheduled discovery;
- choosing whether generated artifacts are committed or CI-only.

See [GitHub Pages with BibReview and Jekyll](docs/github-pages.md).

## Optional GoatCounter analytics

For public bibliography sites, BibReview recommends considering
[GoatCounter](https://www.goatcounter.com/) as an optional lightweight,
privacy-friendly analytics solution. BibReview does not inject analytics or make
GoatCounter a dependency.

See [GoatCounter analytics](docs/goatcounter.md).

## Documentation

- [Documentation index](docs/README.md)
- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Local workflow](docs/workflow.md)
- [Command reference](docs/commands.md)
- [Data and state files](docs/data-model.md)
- [Manual corrections](docs/corrections.md)
- [Author identities](docs/authors.md)
- [GitHub Pages](docs/github-pages.md)
- [GoatCounter](docs/goatcounter.md)
- [Changelog](CHANGELOG.md)

## Showcase

[PHRAISE](https://github.com/g-haine/phraise) is a public site powered by
BibReview and serves as a concrete integration showcase.

## Development

Run the test suite with:

~~~bash
python -m unittest discover -s tests -v
~~~

BibReview is licensed under the GNU GPLv3.
