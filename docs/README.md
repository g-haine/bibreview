# BibReview documentation

BibReview is a human-reviewed bibliographic engine. The documentation is split
by task so that the repository README can remain an introduction rather than a
complete operating manual.

## New to BibReview

Read these first:

1. [Installation](installation.md) — install an exact release on Linux, macOS,
   or Windows.
2. [Configuration](configuration.md) — define project identity, paths,
   providers, credentials, relevance policy, and rendering.
3. [Local workflow](workflow.md) — add publications and maintain an existing
   bibliography without bypassing the review boundary.

The repository also ships two maintained examples:

- [`bibreview.example.yml`](../bibreview.example.yml) — configuration reference;
- [`publication.example.yml`](../publication.example.yml) — field reference for
  reviewed DOI-less imports.

## Operating a project

Use these when maintaining a real bibliography:

- [Command reference](commands.md) — CLI commands and options.
- [Data and state files](data-model.md) — canonical bibliography, staging,
  typed identifier queues, import evidence, audit state, BibTeX, and backups.
- [Manual corrections](corrections.md) — provider errors, missing/wrong metadata,
  BibTeX corrections, and recovery.
- [Author identities](authors.md) — safe mappings and ambiguous contributors.

BibReview's central operating rule is:

> Provider data is evidence. Canonical changes remain explicit and reviewable.

The ordinary promotion boundary is always:

~~~text
evidence / candidate
        ↓
reviewable staging
        ↓
bibreview merge
        ↓
canonical bibliography
~~~

## Publishing a site

BibReview can render bibliographic data into an existing Jekyll project.

- [GitHub Pages](github-pages.md) — rendering, GitHub Actions, Pages deployment,
  and scheduled arXiv refresh.
- [GoatCounter](goatcounter.md) — optional lightweight analytics guidance.

Jekyll presentation remains project-owned: BibReview generates bibliographic
artifacts but does not own themes, CSS, navigation, or deployment policy.

## Understanding the project

- [Roadmap](roadmap.md) — current maturity, priorities, and longer-term work.
- [Changelog](../CHANGELOG.md) — released behavior and historical changes.
- [Release checklist](releasing.md) — validation and release procedure.

## Core concepts

### Canonical identity

Every publication has a persistent BibReview UUID. External identifiers such as
DOI, PMLR, ISBN, arXiv, or PMID are metadata and do not replace that internal
identity.

### Automated and manual ingestion

DOI is currently the sole strong/automatable identifier.

DOI-backed publications use:

~~~text
discover → review → collect → merge
~~~

DOI-less publications use:

~~~text
import --init → human review → import → merge
~~~

Both paths converge on the same canonical model and the same explicit merge
boundary.

### Deterministic project state

Canonical JSON, tracked BibTeX, author mappings, typed identifier files, and
generated site artifacts are designed to be inspectable in version control.

PHRAISE is the primary real-world integration demonstrator used to validate that
architecture.
