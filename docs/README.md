# BibReview documentation

BibReview is a generic engine for maintaining a human-reviewed bibliography and
rendering bibliographic content into a static Jekyll site.

## Start here

- [Installation](installation.md) — Linux, macOS and Windows.
- [Configuration](configuration.md) — project policy, providers, paths and secrets.
- [Local workflow](workflow.md) — discover, review, collect, merge, authors, render and arXiv.
- [Command reference](commands.md) — CLI commands and global options.
- [Data and state files](data-model.md) — canonical bibliography, staging, queues, BibTeX and backups.
- [Manual corrections](corrections.md) — wrong provider metadata, wrong/missing BibTeX and recovery.
- [Author identities](authors.md) — safe mappings and ambiguous names.
- [GitHub Pages](github-pages.md) — build and deploy a Jekyll site with GitHub Actions.
- [GoatCounter](goatcounter.md) — recommended optional privacy-friendly analytics.
- [Changelog](../CHANGELOG.md) — released versions and notable changes.
- [Release checklist](releasing.md) — required version, documentation, validation and tagging checks.

## Design principle

BibReview separates automated retrieval from human decisions. Discovery may
classify candidates, providers may supply metadata, and safe author mappings may
be applied automatically, but ambiguous identity decisions and suspicious
provider output remain reviewable project state.

The canonical bibliography is version-controlled JSON. The rendered site can be
regenerated from canonical bibliography data, reviewed author mappings and
tracked BibTeX files.
