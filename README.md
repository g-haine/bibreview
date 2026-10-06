# BibReview

**BibReview is a human-reviewed bibliographic engine for reproducible literature
databases and static scholarly websites.**

It automates repetitive bibliographic work while keeping ambiguous decisions,
provider disagreements, and canonical changes inspectable by a human maintainer.

Current release line: **v1.7.1**.

## What BibReview is for

BibReview is designed for curated scientific bibliographies that need to remain
maintainable over time.

It provides:

- DOI discovery and relevance screening;
- metadata collection from CrossRef with optional provider enrichment;
- reviewed import of publications without a DOI;
- persistent publication UUIDs independent of external identifiers;
- explicit staging and merge boundaries;
- reviewed author identity mappings;
- resumable audit, reference, refresh, backfill, and hygiene workflows;
- tracked canonical BibTeX;
- deterministic Jekyll publication, author, and year rendering;
- an optional display-only arXiv feed cache;
- dry-run planning, checkpoints, and backups for mutating workflows.

The central rule is simple:

> **Providers supply evidence; the project maintainer owns the canon.**

BibReview never treats a provider response as an unquestionable replacement for
reviewed project state.

## Installation

BibReview requires **Python 3.12 or newer**.

For reproducible use, install an exact release tag:

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.7.1"

bibreview --version
~~~

On Windows PowerShell:

~~~powershell
.\\.venv\\Scripts\\Activate.ps1
~~~

See [Installation](docs/installation.md) for the complete platform-specific
setup.

## Start a project

Use [`bibreview.example.yml`](bibreview.example.yml) as the configuration
reference, then validate the project:

~~~bash
bibreview validate
bibreview status
~~~

BibReview uses `bibreview.yml` in the current directory by default.

A typical project separates canonical data, tracked BibTeX, maintenance state,
backups, and presentation:

~~~text
data/
  bibliography.json
  collected.json
  author_mappings.json
  ID.txt
  newID.txt
  checkID.txt
  badID.txt
  imports/

bib/
audit/
archive/
site/
bibreview.yml
~~~

See [Data and state files](docs/data-model.md) for the exact semantics.

For a brand-new empty bibliography, BibReview can freeze the configured
discovery universe and process it through stable reviewed batches:

~~~bash
bibreview --dry-run init --batch-size 10
bibreview init --batch-size 10
~~~

Resolve the current batch with the ordinary relevance, `collect`, and `merge`
commands. A later `bibreview init` invocation will observe the merged/rejected
outcomes and only then open the next stable batch.

~~~bash
bibreview init --status
~~~

`init` never runs `collect`, `merge`, `authors`, or `render`
automatically.

## Add DOI-backed publications

The ordinary automated acquisition path remains DOI-only:

~~~text
discover
   ↓
newID.txt / checkID.txt / badID.txt
   ↓
human review when needed
   ↓
collect
   ↓
inspect collected.json + BibTeX
   ↓
merge
   ↓
authors
   ↓
render
~~~

Typical commands:

~~~bash
bibreview discover
bibreview --dry-run review
bibreview review

bibreview collect
bibreview --dry-run merge
bibreview merge

bibreview authors
bibreview authors --apply-safe
bibreview authors --review

bibreview render
~~~

`merge` is the explicit promotion boundary into the canonical bibliography.

## Add a publication without a DOI

DOI-less publications are first-class canonical records, but they do **not**
create a second automated acquisition pipeline.

Initialize a reviewed import manifest:

~~~bash
bibreview import --init publication.yml
~~~

Complete it using [`publication.example.yml`](publication.example.yml) as a
field reference. Do not copy the example UUID into a real import: `--init`
allocates the persistent BibReview UUID for the publication.

Then validate and stage it:

~~~bash
bibreview --dry-run import publication.yml
bibreview import publication.yml

bibreview --dry-run merge
bibreview merge
~~~

Auxiliary identifiers such as PMLR, ISBN, arXiv, or PMID can be retained as
metadata, but DOI remains the sole current **strong/automatable identifier**.

## Maintain an existing bibliography

Routine maintenance is split into focused workflows rather than one destructive
"update everything" command.

| Workflow | Purpose |
|---|---|
| `providers` | Inspect configured credentials and optionally live-check providers. |
| `audit` | Compare canonical records with current provider evidence in resumable batches. |
| `references` | Rebuild and review reference-list evidence without silent canonical replacement. |
| `refresh` | Review safe fills for configured incomplete/stale records. |
| `backfill` | Propose provider enrichment or request a reviewed manual value for a missing field. |
| `hygiene` | Inspect and review structured-text/title/citation cleanup. |
| `review` | Resolve manual publication relevance decisions from `checkID.txt`. |
| `authors` | Resolve contributor identity mappings. |
| `render` | Reconcile generated Jekyll bibliography artifacts. |
| `arxiv` | Refresh the optional display-only arXiv cache. |

All reviewed correction workflows preserve the same principle:

~~~text
evidence
   ↓
review / resolution
   ↓
collected.json staging
   ↓
explicit merge
   ↓
canonical bibliography
~~~

For the operational sequence, see [Local workflow](docs/workflow.md). For every
CLI option, see [Command reference](docs/commands.md).

## Identity and state

Every canonical publication has a persistent opaque `Publication.id` UUID.

External identifiers live separately in `Publication.identifiers`. At present,
DOI is the only identifier used by the automated acquisition chain.

The canonical registry `data/ID.txt` contains exactly one token per canonical
publication:

~~~text
doi:10.1234/example
id:550e8400-e29b-41d4-a716-446655440000
~~~

A DOI-backed publication projects to `doi:<DOI>`; a DOI-less publication
projects to `id:<UUID>`.

This separation allows metadata and external identifiers to evolve without
changing canonical publication identity.

## Providers

BibReview can use several external services, depending on project configuration:

- CrossRef;
- OpenAlex;
- Elsevier / Scopus;
- Springer Nature;
- IEEE Xplore;
- Semantic Scholar;
- Mendeley.

Credentials remain project-local and can be supplied through environment
variables or the configured dotenv file. Inspect the effective configuration
without exposing secrets:

~~~bash
bibreview providers
bibreview providers --check
~~~

Provider failures are reported as evidence/availability problems, not silently
converted into bibliographic rejection.

## Static-site rendering

BibReview can render bibliography content into an existing Jekyll project.
Themes, layouts, CSS, navigation, analytics, and deployment remain project-owned.

BibReview can generate:

- publication posts;
- author pages;
- year pages;
- canonical metadata snapshots for the site;
- public copies of tracked canonical BibTeX.

See [GitHub Pages with BibReview and Jekyll](docs/github-pages.md).

## Documentation

Start with the [documentation index](docs/README.md).

The main guides are:

- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Local workflow](docs/workflow.md)
- [Command reference](docs/commands.md)
- [Data and state files](docs/data-model.md)
- [Manual corrections](docs/corrections.md)
- [Author identities](docs/authors.md)
- [GitHub Pages](docs/github-pages.md)
- [Roadmap](docs/roadmap.md)
- [Changelog](CHANGELOG.md)

Historical release details belong in the changelog rather than this README.

## Project status

The v1.7 line marks the stabilization of the current core architecture:

- reviewed DOI and DOI-less ingestion are both validated end to end;
- canonical identity is UUID-based;
- project-facing identifier state is typed and generic;
- historical audit/reference/hygiene campaigns have been exercised on PHRAISE;
- tracked BibTeX and generated site state reconcile deterministically;
- PHRAISE serves as the primary real-world integration demonstrator.

The next development priorities are onboarding new bibliographies, filling the
remaining manual-maintenance gaps, and reducing unnecessary provider traffic
without weakening review boundaries.

See [Roadmap](docs/roadmap.md).

## Showcase

[PHRAISE](https://github.com/g-haine/phraise) is a public bibliography of
port-Hamiltonian research powered by BibReview and is used as the primary
end-to-end integration demonstrator.

## Development

Run the test suite with:

~~~bash
python -m unittest discover -s tests -v
~~~

Before preparing a release, follow the [release checklist](docs/releasing.md).

BibReview is licensed under the GNU GPLv3.
