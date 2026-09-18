# Data and state files

BibReview deliberately makes intermediate states visible and versionable.

## Canonical bibliography

The canonical bibliography is a JSON document:

~~~json
{
  "metadata": {
    "schema_version": 1,
    "last_update": "2026-09-18"
  },
  "publications": [
    {
      "id": "persistent-uuid",
      "identifiers": {
        "doi": "10.xxxx/example"
      },
      "title": "Example title"
    }
  ]
}
~~~

The internal **id** is the persistent identity. Do not regenerate it during
manual metadata corrections.

**metadata.last_update** changes on an effective merge that adds or updates a
publication.

## Collected staging

**collected.json** uses the same document envelope and is temporary staging for
**collect** and **refresh**.

Only one staging batch is allowed at a time. This is intentional: inspect and
resolve the current batch before starting another collection or refresh.

## DOI queues

BibReview uses plain text files with one DOI per line:

- **known** — accepted DOI values already represented by the canonical bibliography;
- **pending** — DOI values waiting for collection;
- **review** — discovered candidates requiring human relevance judgment;
- **rejected** — DOI values deliberately excluded.

Comments and blank lines are ignored when queue files are read.

## Author mappings

The author mapping file is JSON:

~~~json
{
  "ada-lovelace": [
    "Ada Lovelace",
    "A. Lovelace"
  ]
}
~~~

The key is the stable site slug; the list contains exact source-visible name
variants assigned to that identity.

See [Author identities](authors.md).

## BibTeX

BibTeX files live under the configured **paths.bibtex** directory and are named
from the publication permalink:

~~~text
bib/<permalink>.bib
~~~

BibReview does not fabricate a placeholder when DOI content negotiation fails.
A missing or invalid provider response is treated as unavailable BibTeX. The
publication may still be collected, but **render** will refuse to render the site
until a real BibTeX file exists.

This makes missing source material visible instead of silently publishing a fake
citation.

## Archive

Refresh creates backups of changed stored BibTeX before replacement in the
configured archive directory. Merge also backs up the previous bibliography
before replacement when appropriate.

Keep the archive under version control only if that matches your project's
retention policy; otherwise ensure you still have reliable Git history or an
external backup.

## Generated Jekyll data

**bibreview render** owns only the generated roots used by its Jekyll renderer,
not the whole site. It reconciles publication posts, author pages, year pages
and BibReview metadata used by the site.

Themes, layouts, CSS, hand-written pages, analytics and deployment remain
project-owned.
