# Command reference

Global syntax:

~~~text
bibreview [--config PATH] [-v|-q] [--dry-run] COMMAND
~~~

Global options:

| Option | Meaning |
|---|---|
| **--config PATH** | Configuration file; default: bibreview.yml. |
| **-v**, **--verbose** | Increase progress output. Can be repeated. |
| **-q**, **--quiet** | Suppress normal output. |
| **--dry-run** | Plan a mutating command without writing project files. |
| **--version** | Print the BibReview version. |

## validate

Validate the configuration file:

~~~bash
bibreview --config bibreview.yml validate
~~~

## status

Show the resolved project configuration, enabled providers and arXiv status:

~~~bash
bibreview --config bibreview.yml status
~~~

## providers

Inspect provider enablement, configured credential-variable names, and safe
credential provenance without making network requests:

~~~bash
bibreview --config bibreview.yml providers
~~~

Run one sanitized live request per provider that is ready to use:

~~~bash
bibreview --config bibreview.yml providers --check
~~~

Common live statuses distinguish authentication failure, access/entitlement
denial, rate limiting, and provider unavailability. Secret values are never
printed.

Machine-readable diagnostics:

~~~bash
bibreview --config bibreview.yml providers --json
bibreview --config bibreview.yml providers --check --json
~~~

## discover

Search the configured discovery provider, verify candidates and update DOI
queues:

~~~bash
bibreview --config bibreview.yml discover
~~~

Use **--dry-run** to inspect the discovery plan without writing queue files.

## collect

Collect metadata for DOI values in the pending queue and write canonical staging
plus available BibTeX files:

~~~bash
bibreview --config bibreview.yml collect
~~~

Collection refuses to overwrite a non-empty staging bibliography.

## refresh

Inspect configured incomplete existing records, compare stored/current BibTeX,
and stage stale records:

~~~bash
bibreview --config bibreview.yml refresh
~~~

A non-empty staging bibliography must be merged or otherwise resolved first.

## merge

Merge canonical staging into the bibliography:

~~~bash
bibreview --config bibreview.yml merge
~~~

This preserves persistent publication UUIDs and updates
**metadata.last_update** only when publications are added or updated.

## authors

Inspect author identity mappings:

~~~bash
bibreview --config bibreview.yml authors
~~~

Apply only unambiguous proposals:

~~~bash
bibreview --config bibreview.yml authors --apply-safe
~~~

Machine-readable analysis:

~~~bash
bibreview --config bibreview.yml authors --json
~~~

Ambiguous proposals are never applied automatically. See
[Author identities](authors.md).

## render

Render/reconcile Jekyll publication posts, author pages and year pages:

~~~bash
bibreview --config bibreview.yml render
~~~

Rendering performs no provider lookup. Every publication must have its tracked
BibTeX file; missing BibTeX is reported as an error.

## arxiv

Refresh the optional display-only arXiv cache:

~~~bash
bibreview --config bibreview.yml arxiv
~~~

Transient API failures keep the existing cache untouched and return success with
a warning, making this command suitable for scheduled website maintenance.
