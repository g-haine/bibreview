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
**collect**, reviewed **refresh --apply**, explicit **audit --apply**, and reviewed
**backfill --apply** promotion.

Only one staging batch is allowed at a time. This is intentional: inspect and
resolve the current batch before starting another collection or reviewed
application. Refresh scanning/review itself never writes staging, while
`refresh --apply`, `audit --apply`, and `backfill --apply` all refuse to
overwrite non-empty staging.

## Backfill review evidence

Backfill proposal generation writes local review state beside the configured
audit files; it never changes canonical bibliography data directly. A normal
candidate stores a safe `proposed_value`. Abstract candidates may additionally
carry retained provider evidence:

~~~json
{
  "field": "abstract",
  "proposed_value": "",
  "review_required": true,
  "evidence": [
    {
      "source": "crossref",
      "reason": "embedded-graphic",
      "value": "provider payload retained verbatim"
    }
  ]
}
~~~

A `review_required: true` candidate has no safe automatic proposal and therefore
cannot be accepted directly. Only an explicit custom value can become staged
metadata; reject and defer remain non-mutating decisions. Evidence is part of
the backfill review fingerprint, so resolution state cannot be reused after the
provider evidence changes.

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


## Resumable campaign state

Long-running workflows such as the planned **audit** and **init** commands share
a small generic campaign model. Campaign state is deliberately separate from
the canonical bibliography, DOI queues and `collected.json`.

A campaign starts from a stable ordered universe of item keys and processes it
through bounded batches. The generic layer does not know whether a key is a
publication UUID, a normalized external identifier or another command-specific
identity. Audit orchestration may append newly discovered canonical publication
UUIDs between closed batches so the local audit state can serve as persistent
incremental history; existing item order and historical batch membership remain
unchanged.

Its current versioned JSON representation has this shape:

~~~json
{
  "schema_version": 2,
  "kind": "audit",
  "default_batch_size": 50,
  "items": [
    {
      "key": "stable-item-id",
      "state": "pending",
      "attempts": 0,
      "detail": ""
    }
  ],
  "batches": []
}
~~~

The mechanical item states are:

- **pending** — never selected yet;
- **active** — belongs to the single currently open batch;
- **completed** — command-specific processing finished;
- **retryable** — processing was unavailable or transiently failed and may be
  selected again after the first pass;
- **failed** — terminal failure for this campaign.

Only one batch may be open at a time. Opening a campaign that already has an
open batch returns that same batch, so interrupted work resumes on stable item
membership rather than recalculating mutable offsets. New pending items are
processed before retryable items. The campaign `default_batch_size` is the
default for new batches, not a structural maximum: command-specific workflows
may choose a different size for the next unopened batch. Batch identities are monotonic
(`batch-0001`, `batch-0002`, ...), and historical membership is retained so
attempt counts and restart behavior are inspectable.

Campaign schema version 1 used the less precise field name `batch_size`.
BibReview still reads that format and maps it to `default_batch_size`; the
next audit-state write serializes the same campaign as schema version 2 without
changing item states, batch membership, attempt counts, or report entries.

This layer intentionally does **not** define audit classifications, initialization
review decisions, provider policy, canonical corrections or automatic merge
behavior. Those belong to the command-specific workflow built on top of the
shared mechanics.

The optional per-item `detail` field is persisted verbatim and therefore must
contain only already-sanitized diagnostic text. Credentials, authorization
headers and raw provider responses must never be stored in campaign state.

### Refresh review and resolution state

Safe refresh keeps its provider-comparison evidence and human decisions beside
the configured audit report:

~~~text
data/audit/refresh.json
data/audit/refresh-resolutions.json
~~~

`refresh.json` records the DOI values whose remote BibTeX changed or was
missing, safe proposals for configured fields that were canonically empty, and
all meaningful collateral provider differences on other fields. Collateral
differences preserve both current and provider values but are explicitly
non-promotable.

`refresh-resolutions.json` stores accepted, custom, rejected, and deferred
human decisions only for the safe missing-field proposals. It is fingerprinted
against the exact refresh review, so a new provider scan cannot silently reuse
stale decisions.

Neither file is canonical data or staging. Only `refresh --apply` may turn
completed accepted/custom safe proposals into `collected.json`, after
rechecking that the canonical field is still empty. Applicable tracked BibTeX
fields are edited locally and backed up; the remote BibTeX is never used as a
wholesale replacement.

### Backfill proposal and resolution state

Human-reviewed missing-field backfill keeps its proposal and decision files
beside the configured audit report by default:

~~~text
data/audit/backfill.json
data/audit/backfill-resolutions.json
~~~

The proposal file contains only values for requested fields that were empty in
the canonical publication at proposal time. The resolution file stores
accepted, custom, rejected, and deferred human decisions and is fingerprinted
against the exact proposal set.

Neither file is canonical bibliographic data and neither is merge-ready staging.
Only `bibreview backfill --apply` may convert completed accepted/custom
decisions into `collected.json`. Application rechecks that the canonical field
is still empty so a newer correction cannot be overwritten silently.

### Audit campaign and report

The audit workflow uses two separate files by default:

~~~text
data/audit/campaign.json
data/audit/report.json
~~~

They are created and updated together when an audit campaign starts. A partial
state where only one of the two files exists is rejected rather than silently
reconstructed.

The audit campaign starts with publication **UUIDs** in canonical order. Normal
later audit runs append newly added canonical UUIDs as pending items without
revisiting completed publications; historical batch membership remains stable.
Resuming an interrupted run returns the same open batch.

The audit report stores only the latest result for each processed UUID, together
with the batch ID and attempt number that produced it. If a retry later
succeeds, that result replaces the previous report entry; the campaign file
still records the complete batch/attempt history.

Pairwise provider/canonical values remain stored even when they are classified
as formatting-only. The human review view is **derived**, not a second source of
truth: it suppresses equal/formatting-only/provider-missing noise and groups
review-equivalent provider alternatives before counting their support. External
metadata differences are informational by default; a candidate correction
becomes actionable only when at least two independent providers corroborate the
same alternative. A substantive alternative is kept informational if another
provider confirms the canonical value. One-day `created_date` offsets, obvious
provider truncations of a fuller canonical abstract, and provider-author versus
canonical-editor role disagreements are also informational. Existing
year/container suppression remains conservative when another provider confirms
the canonical value.

Comparison rules may improve after a long audit has started. BibReview can
reclassify the values already stored in the report entirely offline. This
changes only derived classifications/disagreements in the report; it does not
repeat provider requests, modify campaign progress, or alter the stored
canonical/provider values.

After the actionable review is stable, `bibreview audit --resolve` may create a
third, versioned human-decision file beside the report:

~~~text
data/audit/resolutions.json
~~~

This file stores explicit accepted, custom, rejected, and deferred decisions.
It is fingerprinted against the exact actionable review so decisions cannot be
silently reused after the evidence or review classification changes. Accepted
and custom values are still review state only: they are not canonical
bibliographic data until a separate staging/promotion step is explicitly run.

None of these files is canonical bibliographic data. Audit planning and checkpointing
must not modify **bibliography.json**, **collected.json**, DOI queues, BibTeX,
author mappings, or generated site files.

## Archive

`refresh --apply` and `audit --apply` create backups of tracked BibTeX before
reviewed field-level edits in the configured archive directory. Refresh never
archives/replaces a complete remote BibTeX response. Merge also backs up the
previous bibliography before replacement when appropriate.

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
