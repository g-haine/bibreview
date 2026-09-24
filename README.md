# BibReview

**BibReview is a generic, human-reviewed bibliographic engine for reproducible
literature databases and static scholarly websites.**

It discovers DOI-backed publications, collects and enriches metadata, keeps
reviewable canonical project state, helps resolve author identities, renders
Jekyll bibliography pages, and can optionally maintain a display-only arXiv
feed.

BibReview is designed so that provider output remains inspectable and ambiguous
decisions remain human decisions.

Current stable release: **v1.6.28**.

## What BibReview provides

- OpenAlex discovery with configurable relevance rules;
- CrossRef-backed DOI metadata collection;
- optional publisher and abstract enrichment providers;
- safe provider/credential diagnostics with optional live checks;
- resumable, non-destructive audits of existing canonical metadata;
- offline audit reclassification and corroboration-aware human-review views;
- resumable interactive resolution of actionable audit findings;
- conservative promotion of completed audit resolutions into reviewable staging;
- explicit pending, review, rejected and collected states;
- persistent publication UUIDs independent from DOI representation;
- reviewed author-name mapping with safe and ambiguous proposals;
- BibTeX retrieval and tracked source files;
- human-reviewed, non-destructive refresh of selected incomplete publications;
- human-reviewed backfill of selected missing canonical fields;
- deterministic Jekyll publication, author and year rendering;
- an optional arXiv feed-cache module, separate from the canonical bibliography;
- dry-run planning for mutating workflows;
- atomic-per-file persistence and explicit backups.

## Quick start

BibReview currently requires **Python 3.12 or newer**.

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.6.28"

bibreview --version
~~~

On Windows PowerShell, activate the environment with:

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

Start a project from
[bibreview.example.yml](bibreview.example.yml), then validate it:

~~~bash
bibreview --config bibreview.yml validate
bibreview --config bibreview.yml status
~~~

See the complete [installation guide](docs/installation.md) for Linux, macOS and
Windows.

## Typical maintenance cycle

~~~text
discover
   ↓
human review of uncertain DOI candidates
   ↓
refresh existing incomplete records
   ↓
review/resolve refresh proposals
   ↓
apply reviewed refresh fills
   ↓
merge
   ↓
collect new pending DOI values
   ↓
inspect/correct staging + BibTeX
   ↓
merge
   ↓
authors
   ↓
human resolution of ambiguous identities
   ↓
render
~~~

The optional arXiv feed runs independently:

~~~text
arxiv → display-only JSON cache
~~~

A curated project should inspect staged metadata and BibTeX before merging.

## Commands

| Command | Purpose |
|---|---|
| **validate** | Validate project configuration. |
| **status** | Show the resolved project configuration summary. |
| **providers** | Inspect credential provenance and optionally live-check providers. |
| **hygiene** | Read-only metadata hygiene inventories plus reviewed canonical abstract migration. |
| **audit** | Incrementally audit new/retryable canonical publications; use `--full` for a complete pass. |
| **references** | Rebuild and compare one resumable batch of canonical reference lists from current CrossRef parent-work metadata. |
| **discover** | Discover and screen new DOI candidates. |
| **collect** | Collect pending DOI metadata into canonical staging. |
| **backfill** | Propose missing-field enrichment, resolve it interactively, then stage accepted values. |
| **refresh** | Detect stale incomplete records, review safe fills, and stage only human-approved changes. |
| **merge** | Merge reviewed staging into the canonical bibliography. |
| **authors** | Analyze and safely extend author identity mappings. |
| **render** | Reconcile generated Jekyll bibliography artifacts. |
| **arxiv** | Refresh the optional display-only arXiv cache. |

Use **--dry-run** with mutating workflows when you want to inspect the plan
without writing project files.

Full details: [command reference](docs/commands.md).

### Reference refresh inventory

BibReview v1.6.28 refines the read-only `bibreview references` workflow after
the first PHRAISE batch showed that parent CrossRef references often expose a
DOI without citation text. When that DOI exactly matches the canonical DOI at
the same position, BibReview now reuses the canonical citation as evidence,
runs it through the current conservative sanitizer, and avoids proposing an
empty citation. Non-DOI entries, reordered references, and identifier drift
never use this fallback.

Default reference campaign state now lives under:

~~~text
audit/references/campaign.json
audit/references/report.json
~~~

BibReview v1.6.27 introduced the first read-only `bibreview references`
workflow. It rebuilds reference lists from the current **parent CrossRef work
records**, runs reconstructed citation strings through the v1.6.26 conservative
normalizer, and compares them with canonical references without changing
`bibliography.json` or `collected.json`.

~~~bash
bibreview --dry-run references
bibreview references
bibreview references --review
bibreview -v references --review
~~~

The campaign is resumable and checkpointed publication-by-publication.
`safe-update` is intentionally narrow: reference count, order, and identifiers
must remain identical, and every citation change must be exactly explained by
the deterministic T2 sanitizer. Any provider change beyond that is
`review-required`. There is deliberately **no `references --apply` in
v1.6.28**; PHRAISE validation of the refined report comes first.

### Canonical abstract hygiene inventory

Historical bibliographies can contain provider HTML/JATS/MathML payloads inside
otherwise reviewed canonical abstracts. Inspect them without network access or
project-state mutation with:

~~~bash
bibreview hygiene
bibreview -v hygiene
bibreview hygiene --json
~~~

The compact view reports counts by contamination family. Verbose output adds
the affected DOI/title, a short context excerpt and a normalization hint; JSON
contains the complete inventory. A deterministic-candidate label means only
that the observed markup has an apparently lossless cleanup path. For inline
formulas, BibReview accepts explicit `application/x-tex` annotations or
`<tex-math notation="LaTeX">…</tex-math>` payloads only when **every**
`inline-formula` has a non-empty representation. Embedded graphics such as
JATS `inline-graphic` are instead review-required because removing the tag
could discard mathematical content. Subscript/superscript markup such as IEEE
`<inf>` and ordinary `<sub>` / `<sup>` is also review-required because plain
unwrapping would lose mathematical position semantics. **Phase 1 never rewrites
canonical metadata.**

BibReview v1.6.19 introduced the library-level
`bibreview.structured_abstract.normalize_structured_abstract()` primitive for
lossless structured normalization.

BibReview v1.6.20 routes incoming provider abstracts through that conservative
primitive before canonical collection/enrichment. Safe structural markup is
normalized; refused structured payloads are not flattened into misleading text.
Collection can fall through from an unsafe publisher/CrossRef abstract to another
safe provider, and optional fallback providers skip unsafe candidates with an
explicit warning. Publisher adapters preserve their raw abstract markup until
this central policy boundary. **Collect remains fully automatic:** refused
abstract evidence is not persisted by the collection workflow; if no safe source
exists, the collected publication simply has no abstract and a later reviewed
backfill/refresh can revisit the missing field. Discovery remains non-canonical:
refused provider text may still participate transiently in relevance matching so
useful search evidence is not discarded.

BibReview v1.6.26 adds the conservative T2 title/reference normalizer while
keeping the workflow read-only at project level. `hygiene --titles` now assesses
each finding with the real normalizer rather than the earlier T1 heuristic.
Entity decoding is iterative and followed by rescanning, explicit TeX is
preserved, the small semantic MathML subset observed in PHRAISE can be converted
losslessly to inline TeX, and script markup / malformed structures / replacement
characters remain review-required. No canonical title or citation is migrated
automatically in v1.6.26.

BibReview v1.6.25 starts the separate title/reference hygiene campaign with a
strictly read-only T1 inventory:

~~~bash
bibreview hygiene --titles
bibreview -v hygiene --titles
bibreview hygiene --titles --json
~~~

This scans canonical publication titles and complete stored `Reference.citation`
strings separately. It reports structural/encoding families such as HTML/XML,
small-caps markup, entities, TeX/math fragments, MathML/JATS, script markup,
escaped markup, and selected Unicode/control signals. Reference findings use
their DOI when available; otherwise BibReview reports a SHA-256 fingerprint of
the complete original citation, adding an ordinal only for duplicates within
the same publication. Plain TeX is inventoried but marked `preserve-tex`, not
as an automatic cleanup candidate. **No title/citation normalizer or migration
is introduced in v1.6.25.**

BibReview v1.6.24 completes the historical abstract migration workflow:

~~~bash
bibreview hygiene --review
bibreview -v hygiene --review
bibreview hygiene --resolve
bibreview --dry-run hygiene --apply
bibreview hygiene --apply
bibreview merge
~~~

Migration proposals are always recomputed read-only from the current canonical
bibliography; only `hygiene-resolutions.json` persists human decisions. The
decision fingerprint includes the exact canonical abstract plus the normalizer
result/reason, so canonical changes invalidate stale decisions. Deterministic
proposals may be accepted, rejected, customized or deferred. Refused cases are
`review-required` and cannot be accepted directly: they require an explicit
custom abstract, rejection, or defer. `hygiene --apply` writes accepted/custom
changes only to `collected.json`; the ordinary explicit `merge` command remains
the sole canonical promotion boundary.

### Non-destructive reviewed refresh

`refresh` uses the remote DOI BibTeX only as a staleness detector. A stale
record is recollected in memory and compared field-by-field with canonical
metadata. Configured fields that are semantically missing become safe proposals;
for abstracts, the historical `Not Available` placeholder is treated as missing
case- and whitespace-insensitively. Differences affecting already-reviewed
meaningful fields are retained as
**collateral evidence** and are never auto-applied.

~~~bash
bibreview refresh
bibreview -v refresh --review
bibreview refresh --resolve
bibreview --dry-run refresh --apply
bibreview refresh --apply
~~~

The resolver reuses the same resumable human decision model as backfill.
Since v1.6.22, a refused provider abstract is retained in `refresh.json` as
inspectable evidence when the canonical abstract is missing. If a safe provider
abstract also exists, it remains the normal proposal and the refused alternatives
stay attached as evidence. If no safe abstract exists, refresh creates a
`review-required` proposal: direct accept is disabled and the reviewer must
provide an explicit custom value, reject the evidence, or defer it.

`refresh --apply` can fill only the reviewed missing-field proposals. It
refuses stale proposals when a field has since become non-empty. Tracked BibTeX
is edited only for accepted/custom fields, with backup; the remote BibTeX
response is never copied wholesale. `bibreview merge` remains the only
canonical promotion boundary.

### Human-reviewed missing-field backfill

When an existing canonical record is intentionally incomplete, `backfill`
can ask the configured metadata/enrichment chain for a candidate value without
recollecting or replacing the rest of the reviewed record:

~~~bash
bibreview backfill --field abstract
bibreview backfill --resolve
bibreview --dry-run backfill --apply
bibreview backfill --apply
~~~

Proposal generation writes only local review state beside the audit files.
For network efficiency, backfill batches exact multi-DOI lookups when the
configured provider supports them: CrossRef work metadata is fetched in groups
of up to 25 DOI values, OpenAlex fallback abstracts in groups of up to 100, and
Semantic Scholar fallback abstracts in groups of up to 500. Publisher enrichment
and Mendeley remain per DOI. Batch transport changes neither proposal ordering
nor the human-review boundary.

`backfill --resolve` uses the same resumable human decision model as audit
resolution: accept, reject, choose a custom value, defer, or quit. Only
accepted/custom values can reach `collected.json`, and a stale proposal is
rejected if the canonical field has gained a meaningful value meanwhile. For
abstracts, historical `Not Available` values are eligible for replacement and
provider/fallback placeholders are never proposed as real abstracts.

Since v1.6.21, an abstract rejected by the structured normalizer is **retained as
provider evidence instead of becoming `no_value`**. The local backfill review
records the provider source, refusal reason and raw payload. If no safe abstract
exists, the field is marked `review-required`: direct accept is disabled and
the reviewer must provide an explicit custom value, reject the evidence, or
defer it. If another provider supplies a safe abstract, that value remains the
normal proposal while refused alternatives stay attached as inspectable
evidence. Evidence participates in the review fingerprint, so changed provider
payloads stale existing resolutions.

The ordinary `bibreview merge` command remains the only canonical promotion
boundary.

### Incremental audit history

Normal `bibreview audit` runs remember completed publication UUIDs in the local
audit state, append newly added canonical publications automatically, and avoid
repeating already visited records. Use `bibreview audit --full` only when you
deliberately want fresh provider evidence for the entire current bibliography.

### Conservative audit review

`bibreview audit --review` is a read-only view derived from persisted audit
evidence. A provider-only difference remains informational by default. A
canonical-missing or substantive alternative becomes actionable only when the
same candidate value is corroborated by at least two independent providers
after review-equivalent representations are grouped.

One-day `created_date` offsets, obvious provider truncations of a fuller
canonical abstract, contributor-role disagreements, and isolated contributor
anomalies remain informational evidence rather than automatic corrections. The
raw audit comparisons are preserved separately from this review interpretation.

Since v1.6.23, an audit provider abstract whose structured markup cannot be
normalized losslessly is preserved verbatim in `audit-report.json` and
classified as `provider-review-required`. Such evidence remains visible to
human review but is never counted as an ordinary provider disagreement and is
never actionable through `audit --resolve`. Safe/lossless structured abstracts
continue to be normalized before comparison.

Once an audit is complete, `bibreview audit --resolve` walks through actionable
findings one by one and records explicit human decisions without changing the
canonical bibliography. Exact common provider values may be accepted directly;
ambiguous representations require an explicit custom value. Rejected and
deferred findings remain distinguishable, and the session can be stopped and
resumed safely.

After every actionable finding has a final decision, `bibreview audit --apply`
promotes accepted/custom resolutions into the normal reviewable
`collected.json` staging while synchronizing applicable tracked BibTeX fields.
Changed BibTeX files are backed up first. The command refuses non-empty staging,
stale canonical values, incomplete resolutions, or unsafe BibTeX edits. Use
`bibreview --dry-run audit --apply` before applying, inspect JSON/BibTeX diffs,
then use the ordinary explicit `bibreview merge` step.

By default, `bibreview audit --review` prints only the aggregate review summary
so long-running campaigns remain readable. Use the existing global verbose form,
`bibreview -v audit --review`, for the complete publication-by-publication
human-readable findings; `--json` remains complete regardless of verbosity.

During networked audit runs, BibReview batches DOI lookups whenever the provider
supports an exact multi-DOI API. CrossRef uses repeated exact DOI filters in
bounded groups of 25, OpenAlex uses bounded OR-filter requests up to 100 DOI
values, and Semantic Scholar uses its paper batch endpoint up to 500 DOI values.
Batching changes transport efficiency only; comparison semantics,
per-publication checkpointing, and canonical data remain unchanged.

## Project state is explicit

BibReview keeps canonical and intermediate state visible in ordinary files:

~~~text
bibliography.json    canonical reviewed bibliography
collected.json       current collect/refresh-apply/audit-apply/backfill-apply staging batch
known.txt            accepted DOI state
pending.txt          DOI values waiting for collection
review.txt           DOI values requiring human relevance review
rejected.txt         deliberately excluded DOI values
authors.json         reviewed author-name mappings
bib/                 tracked BibTeX sources
archive/             backups created by refresh/audit-apply/merge
~~~

The canonical bibliography is a versioned JSON document with global metadata,
including the bibliography update date.

See [Data and state files](docs/data-model.md).

## Provider output can be corrected

Provider metadata is not treated as infallible.

BibReview supports a review-first workflow where you can correct staged JSON or
BibTeX before merge. Missing or invalid BibTeX is treated as missing data rather
than replaced by a fake placeholder.

For recovery procedures, persistent provider errors, manual corrections and
post-merge repairs, see
[Manual corrections and provider errors](docs/corrections.md).

## Ambiguous authors stay human-reviewed

BibReview can automatically apply only unambiguous author mappings:

~~~bash
bibreview --config bibreview.yml authors --apply-safe
~~~

Possible identity collisions remain under manual review.

See [Author identities and ambiguous names](docs/authors.md).

## Static sites and GitHub Pages

BibReview renders bibliographic content into an existing Jekyll site; themes,
layouts, CSS and deployment remain project-owned.

The documentation includes a complete guide for:

- local Jekyll preview;
- GitHub Pages deployment with GitHub Actions;
- a pinned BibReview installation in CI;
- scheduled arXiv refresh;
- optional scheduled discovery;
- choosing whether generated artifacts are committed or CI-only.

See [GitHub Pages with BibReview and Jekyll](docs/github-pages.md).

## Optional GoatCounter analytics

For public bibliography sites, BibReview recommends considering
[GoatCounter](https://www.goatcounter.com/) as an optional lightweight,
privacy-friendly analytics solution. BibReview does not inject analytics or make
GoatCounter a dependency.

See [GoatCounter analytics](docs/goatcounter.md).

## Documentation

- [Documentation index](docs/README.md)
- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Local workflow](docs/workflow.md)
- [Command reference](docs/commands.md)
- [Data and state files](docs/data-model.md)
- [Manual corrections](docs/corrections.md)
- [Author identities](docs/authors.md)
- [GitHub Pages](docs/github-pages.md)
- [GoatCounter](docs/goatcounter.md)
- [Changelog](CHANGELOG.md)
- [Release checklist](docs/releasing.md)

## Showcase

[PHRAISE](https://github.com/g-haine/phraise) is a public site powered by
BibReview and serves as a concrete integration showcase.

## Development

Run the test suite with:

~~~bash
python -m unittest discover -s tests -v
~~~

Before preparing a release, follow the [release checklist](docs/releasing.md).

BibReview is licensed under the GNU GPLv3.
