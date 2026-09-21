# Command reference

Global syntax:

~~~text
bibreview [--config PATH] [-v|-q] [--dry-run] COMMAND
~~~

When the configuration file is named **bibreview.yml** and the command is run
from the project root, `--config` may be omitted:

~~~bash
bibreview audit
bibreview providers --check
~~~

Use `--config PATH` only when the project configuration has another name or is
not in the current working directory.

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

## audit

Audit one stable batch of existing canonical publications against current
provider evidence:

~~~bash
bibreview --config bibreview.yml audit
~~~

The first invocation creates a stable UUID snapshot and opens the first batch.
Each invocation processes **one batch only**, checkpoints every publication
result immediately, closes the batch, and stops for human review.

Choose a smaller pilot batch when starting a campaign, or override the size of
any later **new** batch:

~~~bash
bibreview --config bibreview.yml audit --batch-size 25
~~~

The configured `audit.batch_size` remains the campaign default (50 unless
changed in configuration). `--batch-size` overrides only the next batch that
is opened; it does not change the stable UUID snapshot or the default for later
batches. If a batch is already open after an interruption, BibReview resumes its
persisted membership and ignores a different size override.

Preview the next batch without writing audit state and without making provider
requests:

~~~bash
bibreview --config bibreview.yml --dry-run audit --batch-size 25
~~~

Machine-readable command output:

~~~bash
bibreview --config bibreview.yml audit --json
~~~

The audit currently compares evidence from **CrossRef** and **OpenAlex**, plus
**Semantic Scholar** when that provider is enabled. Authenticated Semantic
Scholar audit requests are paced at a minimum interval of 1.1 seconds to stay
below the provider's introductory one-request-per-second API-key limit. Provider
failures are recorded separately from canonical metadata discrepancies. A
failed/rate-limited provider makes that publication retryable; it does not
modify the canonical record.

Audit writes only the configured audit campaign/report files. It never writes
to **bibliography.json**, **collected.json**, DOI queues, BibTeX, author
mappings, or generated site files. The report is evidence for human review and
is never merge-ready staging.

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
