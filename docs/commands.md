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

The first invocation creates the local audit history and opens the first batch.
Each invocation processes **one batch only**, checkpoints every publication
result immediately, closes the batch, and stops for human review. Later normal
invocations keep completed publications as already visited, append newly added
canonical publication UUIDs as pending work, and continue retryable provider
failures only after never-audited publications.

Force a deliberate new pass over every publication currently present in the
canonical bibliography with:

~~~bash
bibreview --config bibreview.yml audit --full
~~~

A full pass preserves local audit history and attempt counts while requeueing the
current canonical UUIDs. It refuses to reset an interrupted open batch; resume
that batch first.

Choose a smaller pilot batch when starting a campaign, or override the size of
any later **new** batch:

~~~bash
bibreview --config bibreview.yml audit --batch-size 25
~~~

The configured `audit.batch_size` remains the campaign default (50 unless
changed in configuration). `--batch-size` overrides only the next batch that
is opened; it does not change the default for later batches. If a batch is
already open after an interruption, BibReview resumes its persisted membership
and ignores a different size override.

Preview the next batch without writing audit state and without making provider
requests:

~~~bash
bibreview --config bibreview.yml --dry-run audit --batch-size 25
~~~

Machine-readable command output:

~~~bash
bibreview --config bibreview.yml audit --json
~~~

Review the current report offline without making provider requests or changing
project files. By default, only the aggregate review summary is printed:

~~~bash
bibreview --config bibreview.yml audit --review
~~~

Show the complete publication-by-publication findings with the existing global
verbose option:

~~~bash
bibreview --config bibreview.yml -v audit --review
~~~

The JSON form remains complete regardless of verbosity:

~~~bash
bibreview --config bibreview.yml audit --review --json
~~~

The review view applies the current comparison rules in memory, hides pairwise
`equal`, `formatting-only`, and `provider-missing` noise, then groups
review-equivalent provider values before deciding whether a finding is
actionable. A provider-only difference is informational by default.
`canonical-missing` and substantive alternatives become actionable only when
at least two independent providers corroborate the same value; a corroborated
substantive alternative remains informational when another provider confirms
the canonical value. Review-level equivalence also joins harmless TeX/Unicode
and spacing variants before provider support is counted. One-day
`created_date` offsets and obvious provider truncations of a fuller canonical
abstract remain informational. Provider-role disagreements stay informational.
Use `--json` for the complete machine-readable review.

Resolve actionable findings interactively after the audit campaign is complete:

~~~bash
bibreview audit --resolve
~~~

The resolver is offline and never edits the canonical bibliography. It presents
one actionable finding at a time and stores resumable human decisions in
`resolutions.json` beside the configured audit report. The prompt uses:

- **Enter** or **Y**: accept the proposed value, but only when all supporting
  providers expose one exact common representation;
- **n**: explicitly reject the proposed correction and keep the current
  canonical value;
- **f VALUE**: force a human-selected replacement value instead of the provider
  representation;
- **s**: defer the finding so it is presented again in a later resolution
  session;
- **q**: stop cleanly; decisions already made remain persisted.

For tuple-valued metadata such as contributor lists, `f` accepts the same
semicolon-separated form shown by the resolver, for example:

~~~text
f Nguyen Thanh Sang; Tan Chee Keong; Hussain Mohd Azlan
~~~

A JSON string array remains supported when it is more convenient or less
ambiguous:

~~~text
f ["Ada Lovelace", "Alan Turing"]
~~~

On terminals where Python's standard `readline` module is available, the
interactive resolver enables normal command-line editing for `input()`,
including left/right arrows, Home/End, Backspace/Delete, and shell-style input
history. If `readline` is unavailable, BibReview falls back gracefully to the
platform's default input behavior without adding a runtime dependency.

When corroborating providers agree only after review normalization but retain
different raw representations, BibReview deliberately offers no default **Y**
choice. Use `f VALUE` to choose the canonical representation explicitly, or
defer/reject the finding. Page ranges are the intentional exception: provider
single/Unicode dashes are normalized in the proposed value to BibTeX-style
double hyphens (for example `8793--8805`) without changing the stored provider
evidence.

The resolution file is fingerprinted against the exact actionable review. If
the underlying actionable evidence changes after reclassification or a new
audit, BibReview refuses to reuse stale decisions. `--dry-run audit --resolve`
supports the same interactive flow without writing the resolution file.
Interactive `--resolve` is intentionally incompatible with `--json` and
`--quiet`.

Resolution decisions are review state only. They do not update
**bibliography.json** or **collected.json** until the maintainer explicitly
promotes them:

~~~bash
bibreview --config bibreview.yml --dry-run audit --apply
bibreview --config bibreview.yml audit --apply
~~~

`audit --apply` is offline. It requires every actionable finding to be either
accepted, custom, or rejected; deferred/unresolved findings block the operation.
It rechecks the resolution fingerprint and verifies that every audited canonical
value is still unchanged before applying anything. A non-empty
**collected.json** also blocks the operation so audit corrections cannot be mixed
with an existing collect/refresh batch.

Accepted and custom decisions are copied into normal **collected.json** staging;
rejected decisions make no metadata change. Applicable fields in the tracked
BibTeX file are updated at the same time (solution A), with the previous BibTeX
saved under the configured archive directory. A required missing/malformed
BibTeX file or an unsafe field edit aborts the complete plan before any output is
written. Fields with no meaningful tracked BibTeX representation remain JSON
only and are reported as such.

By default the command prints aggregate counts, including explicit
**no-op resolutions** when an accepted/custom resolved value is already equal
to the current canonical value. No-op resolutions are not staged and never
trigger BibTeX writes. Use the global `-v` option to show each current/staged
change plus each no-op decision and its unchanged value, or `--json` for the
complete machine-readable application plan. `--dry-run` performs all safety
checks and computes the same JSON/BibTeX changes without writing files.

After applying, inspect **collected.json**, BibTeX changes and reported backups,
then use the ordinary merge boundary:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

`audit --apply` never edits **bibliography.json** directly.

When comparison rules improve, reclassify the already-stored raw values without
re-querying any provider:

~~~bash
bibreview --config bibreview.yml --dry-run audit --reclassify
bibreview --config bibreview.yml audit --reclassify
~~~

Reclassification rewrites only the audit report. It preserves campaign
progress, batch history, attempt counts, canonical bibliography, and stored
provider/canonical values. The dry run reports before/after classification
counts without writing.

The audit currently compares evidence from **CrossRef** and **OpenAlex**, plus
**Semantic Scholar** when that provider is enabled. Provider requests are
batched whenever the upstream API supports exact multi-DOI lookup. CrossRef
uses repeated exact DOI filters in bounded groups of 25, OpenAlex uses one
OR-filter request for up to 100 DOI values, and Semantic Scholar uses the paper
batch endpoint for up to 500 DOI values.

Authenticated Semantic Scholar requests retain a minimum 1.1-second interval
between batch requests. With BibReview's default audit batch size of 50, one
audit batch therefore normally needs two CrossRef requests, one OpenAlex
request, and one Semantic Scholar batch request. Provider failures are recorded
separately from canonical metadata discrepancies. If one provider batch fails,
only the DOI values in that provider chunk become retryable; it does not modify
the canonical record.

Networked audit, offline review/reclassification, and interactive resolution
write only their dedicated audit state. They never edit **bibliography.json**,
DOI queues, author mappings, or generated site files. Only the explicit
`audit --apply` promotion step writes normal **collected.json** staging and
reviewed tracked BibTeX updates; it still never edits the canonical bibliography
directly.

Current comparison normalization treats common bibliographic representation
differences conservatively: LaTeX page ranges such as `1128--1144` versus
provider `1128-1144`, compatible contributor-name variants (initials,
diacritics, and hyphenation without reordering), common TeX/Unicode title
variants, and near-identical abstracts with markup/prefix differences are
formatting-only. Contributor reordering, truncated abstracts, identifier
conflicts, and genuinely different values remain substantive.

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

## backfill

Propose values for selected fields that are currently empty in existing
canonical DOI-backed publications:

~~~bash
bibreview --config bibreview.yml backfill --field abstract
~~~

Repeat `--field` to propose more than one scalar field. Use repeated `--type`
options to restrict proposal generation to selected publication types. Existing
non-empty canonical fields are never proposed for replacement.

Proposal generation may call CrossRef, publisher enrichment, and configured
abstract fallbacks such as OpenAlex, Semantic Scholar, and Mendeley. It does
**not** write `collected.json` or modify the canonical bibliography. Instead it
persists a fingerprinted local proposal set beside the configured audit state.

Review those proposals interactively:

~~~bash
bibreview --config bibreview.yml backfill --resolve
~~~

The resolver presents one missing field at a time and uses the same controls as
the audit resolver:

- **Enter** or **Y** — accept the proposed value;
- **n** — reject it;
- **f VALUE** — store an explicit human-selected value;
- **s** — defer it for a later session;
- **q** — stop cleanly while preserving previous decisions.

Decisions are resumable and fingerprinted against the exact proposal set.
Proposal text is shown in full (wrapped for readability), which is especially
important for abstracts. Provider candidates have already passed BibReview's
normal metadata/abstract cleaning before they are proposed.

Nothing reaches canonical staging until every proposal has a final accepted,
custom, or rejected decision:

~~~bash
bibreview --config bibreview.yml --dry-run backfill --apply
bibreview --config bibreview.yml backfill --apply
~~~

`backfill --apply` stages only accepted/custom values in `collected.json`.
It refuses non-empty staging, unresolved/deferred decisions, stale proposal
fingerprints, missing canonical publications, or fields that are no longer
empty. It never edits `bibliography.json` directly and does not rewrite tracked
BibTeX as part of missing-field enrichment.

Inspect the staged JSON, then use the normal canonical boundary:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

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
