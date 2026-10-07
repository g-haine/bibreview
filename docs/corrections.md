# Manual corrections and provider errors

Provider output is input data, not unquestionable truth. BibReview deliberately
keeps staging and source files inspectable so a maintainer can correct errors
before publication.

Always make corrections in a clean Git working tree when possible.

## Wrong metadata detected before merge

After **collect**, **refresh**, **audit --apply**, or **backfill --apply**, inspect the configured collected.json.

If a provider returned an incorrect title, author, journal, date, volume, issue,
pages, abstract, event or keyword:

1. edit the affected publication under publications in collected.json;
2. preserve its internal id;
3. preserve its permalink unless you deliberately want to change the public URL;
4. do not casually change identifiers.doi: DOI is a strong identity key;
5. run:

~~~bash
bibreview --config bibreview.yml --dry-run merge
~~~

6. inspect the plan and Git diff;
7. run the real merge only when the staged record is correct.

JSON is parsed strictly. A malformed manual edit will fail before the merge is
applied.

## Wrong or missing BibTeX before merge

BibTeX is stored separately from canonical JSON:

~~~text
<paths.bibtex>/<permalink>.bib
~~~

Edit that file directly or create it if the provider could not return usable
BibTeX.

BibReview treats an empty or non-BibTeX provider response as unavailable data;
it does not write a placeholder such as “No BibTeX found!”.

**bibreview render** requires a real BibTeX file for every publication and will
report a missing file.

## Optional enrichment provider is wrong

Optional enrichment providers can be disabled in bibreview.yml:

~~~yaml
providers:
  elsevier:
    enabled: false
~~~

Then recollect the affected staging data using the remaining providers.

CrossRef is the core DOI metadata provider and cannot be disabled for DOI-backed
collection. If CrossRef itself contains incorrect bibliographic metadata, use a
reviewed manual correction.

## Applying reviewed historical-audit corrections

When a completed historical audit already contains explicit human resolutions,
prefer the audited staging path instead of editing canonical JSON directly:

~~~bash
bibreview --config bibreview.yml --dry-run audit --apply
bibreview --config bibreview.yml audit --apply
~~~

Inspect **collected.json**, every changed tracked BibTeX file, and the reported
BibTeX backups. If the plan is correct, promote it with the normal
`bibreview merge` command. BibReview refuses to apply a resolution if the
canonical value has changed since the audit, so newer manual/provider work is
not silently overwritten.

## Human-reviewed enrichment of a missing field

If a canonical publication is correct but lacks a field such as an abstract,
prefer a reviewed backfill over manual bulk editing or full recollection:

~~~bash
bibreview --config bibreview.yml backfill --field abstract
bibreview --config bibreview.yml backfill --resolve
bibreview --config bibreview.yml --dry-run backfill --apply
bibreview --config bibreview.yml backfill --apply
~~~

The provider chain only proposes values for fields that are semantically
missing. Empty strings are missing for every supported scalar field. For
`abstract`, the historical placeholder `Not Available` is also missing,
case- and whitespace-insensitively. It can therefore be replaced through the
normal reviewed backfill workflow.

Provider/fallback values equal to that abstract placeholder are normalized to
empty and are never proposed as real abstracts. Every proposal must receive an
explicit human decision. Accepted/custom values are staged in
`collected.json`; rejected values leave the record unchanged. If the canonical
field gains a meaningful value before application, BibReview treats the proposal
as stale and refuses to overwrite the newer value.

When provider evidence is unavailable or deliberately not desired, use:

~~~bash
bibreview --config bibreview.yml backfill --field abstract --manual
~~~

This path makes no provider request. It creates review-required candidates only
for canonically missing abstracts, including DOI-less publications. Because
there is no automatic proposal, Enter/Y is invalid during `--resolve`; provide
an explicit `f VALUE`, reject, or defer. The same fingerprint, staleness,
staging, and merge protections still apply.

Inspect staging and use the normal `bibreview merge` boundary only after the
accepted values are satisfactory.

## Correction after a publication was already merged

If incorrect metadata is already in the canonical bibliography:

1. commit or otherwise back up the current state;
2. edit the publication in bibliography.json;
3. preserve the persistent id;
4. preserve the DOI unless you are deliberately correcting publication identity;
5. update metadata.last_update to the correction date if the public site uses
   this field as its bibliography update date;
6. correct the corresponding BibTeX file if necessary;
7. run:

~~~bash
bibreview --config bibreview.yml authors
bibreview --config bibreview.yml --dry-run render
bibreview --config bibreview.yml render
~~~

8. inspect the resulting Git diff.

## Correcting a relevance decision after screening

Use the dedicated relevance-correction workflow rather than editing
`bibliography.json`, `ID.txt`, `newID.txt`, `checkID.txt`, `badID.txt`, or the
relevance ledger by hand. It supports corrections regardless of whether the
original terminal choice was automatic or human-reviewed.

First inspect the DOI and its complete project state:

~~~bash
bibreview --config bibreview.yml correct 10.1234/example
~~~

Then preview and apply the intended state:

~~~bash
bibreview --config bibreview.yml --dry-run correct 10.1234/example --keep
bibreview --config bibreview.yml correct 10.1234/example --keep
~~~

or:

~~~bash
bibreview --config bibreview.yml --dry-run correct 10.1234/example --reject
bibreview --config bibreview.yml correct 10.1234/example --reject
~~~

REJECT → KEEP returns the DOI to pending collection. KEEP → REJECT removes the
canonical record and its registry entry, archives the pre-correction
bibliography and any tracked BibTeX source, and records the DOI as rejected.
The same command also resolves a DOI in manual review through the ordinary
human-decision path, rejects a pending DOI, or safely cancels a staged DOI
before merge. A requested state already represented by the project is a
read-only no-op.
The original screening evidence and original human decision are never
overwritten; a distinct correction decision takes precedence for subsequent
offline relevance analysis.

For an initialization campaign, `correct` reconciles the campaign report while
preserving the original batch membership and attempt; it never reopens a
historical batch. Corrections that reverse or cancel a prior decision require
relevance evidence; run
`bibreview relevance --backfill-evidence` for legacy state before correcting
such a DOI.

## Persistent provider error and refresh

A manually corrected canonical field or BibTeX file is protected by the reviewed
refresh workflow.

Remote BibTeX is used only to detect that an eligible incomplete publication may
be stale. It is never copied wholesale over the tracked BibTeX. The recollected
metadata is compared field-by-field:

- a configured field that is canonically empty may become a safe proposal;
- a difference on an already-populated field is recorded as collateral evidence
  and cannot be promoted by refresh.

Review collateral differences with:

~~~bash
bibreview --config bibreview.yml -v refresh --review
~~~

Only explicit accepted/custom missing-field decisions from
`refresh --resolve` can be staged by `refresh --apply`. At application time,
BibReview verifies that the canonical field is still semantically missing. If
it was manually filled or corrected with a meaningful value after the refresh
scan, the proposal is stale and the
operation stops instead of overwriting it.

For accepted fields with a meaningful tracked BibTeX representation, BibReview
edits only that local field and creates a backup. Unrelated manually corrected
BibTeX content remains untouched.

## Wrong DOI

Changing a DOI is an identity correction, not an ordinary metadata edit.

Prefer to reject or remove the incorrect identity deliberately and collect the
correct DOI through the normal pending → collect → merge path. Preserve Git
history so the identity change is auditable.

## Provider outage

Use **-v** or **-vv** for more progress information.

For normal DOI collection, a hard provider failure aborts planning before the
persistence layer writes a partial collection.

The optional arXiv module is intentionally different: after exhausting bounded
retries for a transient outage, it warns and leaves the existing cache unchanged
so a scheduled site workflow can remain healthy.
