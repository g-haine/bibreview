# Data and state files

BibReview deliberately makes intermediate states visible and versionable.

## Canonical bibliography

The canonical bibliography is a JSON document:

~~~json
{
  "metadata": {
    "schema_version": 2,
    "last_update": "2026-09-21"
  },
  "publications": [
    {
      "id": "persistent-uuid",
      "identifiers": {
        "doi": "10.xxxx/example"
      },
      "title": "Example title",
      "authors": [],
      "editors": [
        {
          "given": "Ada",
          "family": "Lovelace",
          "literal": null,
          "source_fields": {}
        }
      ]
    }
  ]
}
~~~

The internal **id** is the persistent identity. Do not regenerate it during
manual metadata corrections. A DOI is an external identifier, not the canonical
identity, and a publication may legitimately have no DOI.

Every canonical publication must have at least one bibliographic responsibility
entry:

~~~text
len(authors) + len(editors) >= 1
~~~

Authors and editors use the same source-preserving name shape but remain
different roles. Editor names are not silently promoted to author identities.

Schema version 2 adds the **editors** field and the contributor invariant.
For compact canonical JSON, **editors** is omitted when the list is empty and is
written only when at least one editor is present. BibReview still reads valid
schema-version-1 bibliographies; an authorless version-1 record must be repaired
before it can become a valid version-2 publication.

**metadata.last_update** changes on an effective merge that adds or updates a
publication.

## Collected staging

**collected.json** uses the same document envelope and is temporary staging for
**collect** and **refresh**.

Only one staging batch is allowed at a time. This is intentional: inspect and
resolve the current batch before starting another collection or refresh.

## DOI queues

BibReview's current discovery and automated collection workflow is DOI-backed
and uses plain text files with one DOI per line:

- **known** — accepted DOI values already represented by the canonical bibliography;
- **pending** — DOI values waiting for collection;
- **review** — discovered candidates requiring human relevance judgment;
- **rejected** — DOI values deliberately excluded.

Comments and blank lines are ignored when queue files are read.

The canonical model itself is not DOI-dependent. Future non-DOI ingestion must
define how records are acquired and matched, which external identifiers are
trusted, and how a real BibTeX record is obtained or reviewed. It must not
invent a fake DOI or silently fabricate unreliable citation metadata.

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

Editor metadata is intentionally separate and does not participate in author
identity mapping.

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

For future non-DOI ingestion, this provenance rule still applies: either obtain
a trustworthy BibTeX record or add a reviewed explicit mechanism for creating
one. Do not generate a citation merely to satisfy the renderer.

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

The renderer receives editors through the renderer-independent site model. It
may label editor-only rows explicitly (for example **Ed.** or **Eds.**) and
render a dedicated **Editors** section.

Themes, layouts, CSS, hand-written pages, analytics and deployment remain
project-owned. BibReview therefore does not need a second project-specific
"Jekyll template" module: the existing site-model/renderer boundary is the
extension point.
