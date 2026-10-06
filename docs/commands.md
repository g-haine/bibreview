# Command reference

Global syntax:

~~~text
bibreview [--config PATH] [-v|-q] [--dry-run] [--no-cache|--refresh-cache] COMMAND
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
| **--no-cache** | Bypass provider-cache reads and writes for this invocation. |
| **--refresh-cache** | Bypass provider-cache reads and refresh entries after successful live requests. |
| **--version** | Print the BibReview version. |

`--no-cache` and `--refresh-cache` are mutually exclusive global options. They
affect provider-backed workflows when the project cache is enabled. Provider
diagnostics with `providers --check` remain live and bypass the provider cache;
the arXiv display cache is separate.

## init

Build a brand-new DOI-backed bibliography through stable end-to-end batches.

Preview the first batch:

~~~bash
bibreview --dry-run init --batch-size 10
~~~

The first real invocation freezes the configured discovery provider's candidate
universe, persists a versioned initialization campaign/report, opens one stable
batch, and screens only that batch:

~~~bash
bibreview init --batch-size 10
~~~

Relevant candidates enter `newID.txt`; unmatched candidates follow the
configured relevance policy into `checkID.txt` or `badID.txt`. Transient
provider/transport failures become retryable campaign items and are **not**
written to `badID.txt`.

An initialization batch remains open while any of its candidates are:

- pending in `newID.txt`;
- awaiting human relevance review in `checkID.txt`;
- staged in `collected.json`.

Resolve those candidates with the ordinary project workflow:

~~~bash
bibreview --dry-run review
bibreview review
bibreview collect
bibreview --dry-run merge
bibreview merge
~~~

If one pending DOI has structurally invalid provider metadata, `collect`
isolates that candidate instead of aborting the whole batch. Valid publications
are still staged, while the invalid DOI remains in the pending queue and is
reported with its reason as requiring explicit human review. BibReview never
moves such a candidate to `badID.txt` automatically.

Run `bibreview init` again. BibReview observes the canonical/rejected outcome
of the current batch, closes it only when fully resolved, and then opens the
next stable batch. It never invokes `collect`, `merge`, `authors`, or
`render` automatically.

Inspect progress offline:

~~~bash
bibreview init --status
bibreview init --status --json
~~~

The status includes total candidates, unscreened, pending, manual-review,
staged, merged, rejected, skipped, retryable, failed, and batch progress.

When project relevance rules change while the current batch is still open,
re-evaluate only its machine-screened pending/review candidates before
continuing:

~~~bash
bibreview --dry-run init --rescreen-current
bibreview init --rescreen-current
~~~

The dry-run performs the provider lookups and reports proposed
`pending`/`review`/`rejected` changes without writing any project state.
Apply mode updates the ordinary queues and initialization report consistently.
Candidates already staged, merged, rejected, or skipped are never resurrected.
If a candidate has been moved between pending and review since its original
machine-screening outcome, BibReview treats that mismatch as an explicit human
decision and preserves it.

A new campaign refuses to start over a non-empty canonical bibliography,
non-empty staging, or pre-existing acquisition queues. Existing initialization
campaigns remain resumable after canonical records begin to accumulate.

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

Run one sanitized live probe per provider that is ready to use:

~~~bash
bibreview --config bibreview.yml providers --check
~~~

The probe uses the same provider-local pacing and bounded HTTP 429 retry policy
as normal workflows. Common live statuses distinguish authentication failure,
access/entitlement denial, persistent rate limiting, and provider unavailability.
Secret values are never printed.

Machine-readable diagnostics:

~~~bash
bibreview --config bibreview.yml providers --json
bibreview --config bibreview.yml providers --check --json
~~~

## hygiene

Scan canonical abstracts for historical structured-markup contamination without
calling providers and without writing project files:

~~~bash
bibreview --config bibreview.yml hygiene
~~~

The default output is aggregate only: number of canonical publications scanned,
number with abstracts, suspicious abstracts, deterministic cleanup candidates,
review-required cases, and counts by markup family.

Show each affected publication with DOI/title, classification, normalization
hint and a bounded context excerpt:

~~~bash
bibreview --config bibreview.yml -v hygiene
~~~

Machine-readable complete inventory:

~~~bash
bibreview --config bibreview.yml hygiene --json
~~~

The initial scanner recognizes legacy renderer markers, `inline-formula`,
explicit `tex-math` payloads, embedded graphical payloads such as JATS
`inline-graphic` / `graphic` and HTML `img` / `image`,
subscript/superscript markup such as IEEE `<inf>` and ordinary `<sub>` /
`<sup>`, MathML, JATS-like tags, XML comments, escaped markup, generic
HTML/XML tags and obviously unbalanced structured tags. Embedded graphics are
always review-required in this read-only phase unless a later normalizer gains
an explicitly trustworthy textual representation; BibReview does not infer
mathematical content from an image path or filename. Script markup is likewise
review-required because plain unwrapping would lose mathematical position
semantics. Inline formulas are marked as apparent deterministic candidates only
when every `inline-formula` carries a non-empty explicit TeX representation,
either an `application/x-tex` annotation or
`<tex-math notation="LaTeX">…</tex-math>`. Partial coverage remains
review-required. That label is diagnostic only: **`hygiene` never normalizes,
stages, or mutates canonical metadata.**

The plain command remains the abstract inventory phase of canonical-data
hygiene.

### Title and reference-citation inventory

Issue #97 starts with a separate **T1 read-only inventory**:

~~~bash
bibreview --config bibreview.yml hygiene --titles
bibreview --config bibreview.yml -v hygiene --titles
bibreview --config bibreview.yml hygiene --titles --json
~~~

This scans canonical publication titles and complete stored
`Reference.citation` strings separately. It does not attempt to infer a title
substring from a free-form citation.

The T1 families include HTML/XML markup, MathML/JATS/formula markup, small-caps
markup such as `<scp>`, HTML entities, TeX/math fragments, embedded graphics,
script markup, escaped markup, selected Unicode/control-character signals, and
obviously unbalanced structured tags.

In v1.6.26 the inventory assessment is driven by the conservative T2
normalizer. A deterministic candidate therefore means that the actual
normalizer can produce a changed lossless value; plain TeX remains
`preserve-tex` and is not counted as a cleanup candidate.

T2 decodes entities iteratively and rescans the decoded value before deciding
whether it is safe. This matters for encoded script markup such as
`L&lt;inf&gt;2&lt;/inf&gt;`, which is review-required after decoding rather
than blindly flattened. Presentation wrappers can be unwrapped, explicit
`tex` / `tex-math` payloads can be retained as inline TeX, and the small
semantic MathML subset observed in PHRAISE can be converted to TeX. Unsupported
MathML, `sub/sup/inf`, malformed markup, Unicode replacement characters,
control characters, embedded graphics, and provider error pages are refused
without changing the value.

The normalizer is still **read-only at the project level** in v1.6.26:
`hygiene --titles` reports what would be safe but does not stage or mutate
titles/citations.

For stable reporting, a reference uses its DOI when available. Otherwise the
inventory uses a SHA-256 fingerprint of the complete original citation; an
ordinal is added only when duplicate identities occur within one publication.
This reporting identity does not introduce fuzzy bibliographic matching.

With no migration action, `hygiene --titles` remains the read-only combined
title/reference-citation inventory above. When combined with `--review`,
`--resolve`, or `--apply`, the same flag selects **publication-title
migration**; reference citations are not migrated by those actions.

### Reviewed historical title migration

T3 of issue #97 reuses the proven historical hygiene decision boundary for
publication titles:

~~~bash
bibreview --config bibreview.yml hygiene --titles --review
bibreview --config bibreview.yml -v hygiene --titles --review
bibreview --config bibreview.yml hygiene --titles --resolve
bibreview --config bibreview.yml --dry-run hygiene --titles --apply
bibreview --config bibreview.yml hygiene --titles --apply
~~~

The review is derived from the current canonical bibliography on every run.
Only findings that need a decision are included: deterministic normalizations
that actually change the title and normalizer refusals. Existing valid TeX and
other deterministic no-op findings remain inventory signals only.

`hygiene --titles --resolve` stores decisions in the separate
`title-hygiene-resolutions.json` file. Deterministic proposals may be accepted,
rejected, customized, deferred, or used to stop/resume the session. A refused
title has no automatic replacement and therefore requires an explicit custom
value, rejection, or defer.

`hygiene --titles --apply` requires complete decisions and empty staging,
rechecks the exact current canonical title, and writes accepted/custom
publication replacements only to `collected.json`. It changes the `title`
field only: the publication UUID and existing `permalink` are preserved
exactly. Canonical promotion still requires an explicit `bibreview merge`.

### Reviewed historical reference-citation migration

T4 of issue #97 operates on complete stored `Reference.citation` strings.
Select this scope explicitly with `--citations`:

~~~bash
bibreview --config bibreview.yml hygiene --citations --review
bibreview --config bibreview.yml -v hygiene --citations --review
~~~

The review is rebuilt from the current canonical bibliography on every run.
Each proposal carries the parent publication identity plus a stable reference
identity: DOI when available, otherwise the complete-citation SHA-256 identity
used by the inventory. Existing valid TeX and other deterministic no-op
findings are preserved rather than proposed.

Because the residual PHRAISE corpus contains thousands of deterministic
citation cleanups, T4 provides one explicit safe staging boundary instead of
requiring thousands of individual confirmations:

~~~bash
bibreview --config bibreview.yml --dry-run hygiene --citations --apply-safe
bibreview --config bibreview.yml hygiene --citations --apply-safe
bibreview --config bibreview.yml merge
~~~

`--apply-safe` stages only deterministic, lossless citation normalizations.
It never stages review-required values and never changes reference identifiers,
reference order, publication UUIDs, titles, or permalinks. Multiple safe
citation changes in one parent publication produce one staged publication.

After merging that deterministic pass, rerun the citation review. Remaining
ambiguous citations use the resumable human resolver:

~~~bash
bibreview --config bibreview.yml hygiene --citations --resolve
bibreview --config bibreview.yml --dry-run hygiene --citations --apply
bibreview --config bibreview.yml hygiene --citations --apply
bibreview --config bibreview.yml merge
~~~

Human decisions are persisted separately in
`citation-hygiene-resolutions.json`. Review-required proposals cannot be
accepted directly: provide a complete custom citation string, reject the
proposal, or defer it. Application rechecks the exact reference slot, DOI
identity where present, and canonical citation before staging.

The citation normalizer also refuses inline wrapper removal when doing so would
require inferring an alphanumeric word boundary. These cases are classified
`ambiguous-inline-boundary` and remain human-reviewed.

T4 does not parse a title out of citation prose and does not perform fuzzy
reference matching.

### Reviewed historical abstract migration

Historical abstract migration is an explicit second workflow:

~~~bash
bibreview --config bibreview.yml hygiene --review
bibreview --config bibreview.yml -v hygiene --review
bibreview --config bibreview.yml hygiene --resolve
bibreview --config bibreview.yml --dry-run hygiene --apply
bibreview --config bibreview.yml hygiene --apply
~~~

`hygiene --review` is read-only: it recomputes every proposal from the current
canonical bibliography and writes no proposal file. Deterministic normalizer
results become ordinary proposals; refused cases become `review-required`
proposals with no automatic replacement.

`hygiene --resolve` stores resumable human decisions in
`hygiene-resolutions.json`. Ordinary deterministic proposals support **Y**,
**n**, **f VALUE**, **s**, and **q** (accept, reject, custom, defer, quit).
Review-required cases deliberately disable **Y** and require custom, reject, or
defer.

The resolution fingerprint covers the exact canonical abstract and derived
normalizer state. Any canonical change invalidates stale decisions. After every
proposal has a final decision, `hygiene --apply` rechecks staging and the
canonical abstract, then writes accepted/custom replacements to
`collected.json`. It never edits `bibliography.json` directly; use the normal
explicit `bibreview merge` step after inspecting the staged diff.

## references

Refresh one stable batch of canonical reference lists from current parent-work
provider metadata:

~~~bash
bibreview --config bibreview.yml references
~~~

The first invocation creates a dedicated reference-refresh campaign/report,
opens one stable batch, retrieves the corresponding **parent publication**
records from CrossRef, reconstructs their reference lists, passes reconstructed
citation strings through the conservative title/citation normalizer, compares
them with canonical references, checkpoints every publication result
immediately, closes the batch, and stops.

The networked refresh phase is deliberately read-only with respect to
bibliographic project state. It writes only the configured reference
campaign/report files. Canonical staging is a separate explicit offline action;
the refresh command itself never edits `bibliography.json` or
`collected.json`.

Choose the number of **parent publications** processed by the next unopened
campaign batch with:

~~~bash
bibreview --config bibreview.yml references --batch-size 100
~~~

The configured `references.batch_size` remains the campaign default (50 unless
changed in configuration). This size is distinct from the provider transport
batch size: CrossRef exact work lookup is internally chunked into groups of at
most 25 parent DOI values. A reference campaign batch of 100 publications
therefore remains bounded by four CrossRef **parent** batch requests when
all 100 parents have DOI values. Round 2 then performs additional exact CrossRef
batch requests for the unique DOI values cited by those parents, again chunked
at at most 25 DOI values. Repeated cited DOI values are requested once per
campaign batch, regardless of how many parent publications cite them.

As with audit, an already-open interrupted batch resumes its persisted
membership. Never-visited pending publications are processed before retryable
provider failures.

Preview the next batch without writing campaign state and without making any
provider request:

~~~bash
bibreview --config bibreview.yml --dry-run references --batch-size 100
~~~

Run a deliberate new pass over current publications with:

~~~bash
bibreview --config bibreview.yml references --full
~~~

`--full` preserves campaign history and requeues previously attempted current
publications. If never-visited pending items still exist, the generic campaign
contract finishes those first. An interrupted open batch must be resumed before
a full reset can be planned.

### Reference refresh classifications

Each parent publication receives one of four classifications:

- **unchanged** — reconstructed references are exactly identical to the
  canonical ordered list;
- **safe-update** — the list has the same length, order, and exact identifiers,
  and every citation change is exactly the deterministic v1.6.26 citation
  normalization of the current canonical citation;
- **review-required** — the provider added/removed/reordered references, changed
  identifiers, changed citation text beyond the sanitizer, or otherwise
  introduced a difference that cannot be promoted automatically;
- **unavailable** — no usable parent work/reference list was available, the
  canonical parent has no DOI, or a provider attempt was unavailable.

`safe-update` is intentionally narrow. Provider improvements such as repairing
a Unicode replacement character or adding a DOI to an existing reference are
still `review-required`: they may be good corrections, but they are not merely
sanitization of existing canonical evidence.

Reference refresh uses two distinct provider rounds.

**Round 1 — parent structure.** Parent CrossRef `reference` payloads define the
ordered reference list, DOI identifiers, and fallback citation text.

**Round 2 — citation strings.** BibReview collects every DOI found in the
Round-1 slots across the whole campaign batch, de-duplicates those DOI values,
retrieves cited-work CrossRef metadata through the same exact multi-DOI batch
endpoint, and renders each cited work locally using the bundled
`springer-basic-author-date-no-et-al-with-issue` CSL style. Only the rendered
citation string is reinjected into the original Round-1 slot. Round 2 never
creates a replacement BibReview publication/reference structure.

The local CSL renderer uses `citeproc-py`; it loads the style once per cited
DOI batch and renders each DOI separately so the exact `DOI -> citation`
mapping is preserved. The full external style collection is not required.

If cited-work metadata is absent or local formatting fails for one DOI,
BibReview retains the Round-1 citation evidence. If an entire cited-DOI
CrossRef transport chunk fails transiently, affected parent publications remain
retryable rather than being finalized from incomplete evidence.

The v1.6.28 exact same-position canonical DOI fallback remains a final safety net
only when Round 2 cannot provide a citation and Round 1 has no usable text.

Transient provider batch failures remain retryable. Missing parent work records,
missing reference lists, and non-DOI canonical parents are recorded explicitly
without discarding current canonical references.

### Offline review

Summarize the persisted campaign report without network access:

~~~bash
bibreview --config bibreview.yml references --review
~~~

Show all non-unchanged publication results:

~~~bash
bibreview --config bibreview.yml -v references --review
~~~

Machine-readable review:

~~~bash
bibreview --config bibreview.yml references --review --json
~~~

Changed/review-required report entries contain exact ordered-list fingerprints,
changed reference indices, refusal reasons where applicable, and the proposed
reference list. Unchanged entries keep fingerprints/counts but deliberately omit
a duplicate copy of the full list.

### Deterministic safe application

After reviewing the persisted evidence, stage only transformations that
BibReview can prove safe without a bibliographic identity decision:

~~~bash
bibreview --config bibreview.yml --dry-run references --apply-safe
bibreview --config bibreview.yml references --apply-safe
~~~

`references --apply-safe` is fully offline. It rechecks every actionable
publication against the exact canonical reference fingerprint stored in the
refresh report and refuses stale evidence or a non-empty `collected.json`.

Safety is deliberately finer-grained than the publication-level explanation,
but the staging boundary remains publication-level. BibReview computes atomic
safe operations, then stages them only when the publication no longer requires
human judgment. A genuinely ambiguous publication is left completely untouched
until the explicit `references --resolve` workflow decides it. The safe
operations are:

- the original strict `safe-update` proposal, where count, order and
  identifiers are unchanged and every citation change is deterministic T2
  normalization;
- DOI typography normalization only when current and provider DOI values compare
  as the same identifier after dash/case normalization;
- citation sanitizer, punctuation/spacing, or duplicated-wrapper cleanup only
  when the replacement is deterministically demonstrated; punctuation-only
  comparison preserves lexical case;
- provider-only insertions from an `explained-provider-expansion`, while
  keeping every existing canonical reference object in place. Refused,
  empty, duplicate, or identity-colliding insertions are skipped.

The command deliberately does **not** auto-apply substantive same-DOI citation
rewrites, DOI-less metadata enrichment, or provider-added identifiers. When
their drift is deterministically explained, the safe policy resolves them by
preserving the canonical value; they do **not** require publication-by-publication
human review merely because provider evidence differs. Ambiguous or unclassified
drift, and provider-only insertions that cannot themselves be handled safely,
remain genuine human-review cases.

The dry-run/application summary distinguishes the original review-required
population from the residual human workload after deterministic policy. A case
is auto-resolved either because BibReview can stage every authorized safe
change or because an explicit conservative rule keeps the existing canonical
evidence. The remaining count therefore means that BibReview still lacks enough
deterministic evidence or policy to decide the publication. Such publications
are not partially staged.

Safe application writes complete revised publications only to normal
`collected.json` staging. At the same time it records a
`deterministic-policy` entry in the cumulative reference resolution ledger,
including the source, provider and exact resolved fingerprints. After ordinary
`merge`, a later safe-application pass recognizes the resolved fingerprint as
already completed rather than treating the old report evidence as stale. It
never edits `bibliography.json` directly.
Inspect the staged diff, then use the ordinary canonical boundary:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

The explicit `--apply-safe` invocation and subsequent merge are the human
approval boundary for this deterministic policy; BibReview does not silently
promote provider evidence during refresh.

### Historical applied-state reconciliation

Projects that merged deterministic reference maintenance before the persistent
reference ledger existed need one explicit bootstrap step. Preview it first:

~~~bash
bibreview --config bibreview.yml --dry-run references --reconcile-applied
bibreview --config bibreview.yml --dry-run references --reconcile-applied --json
~~~

Then, only when the reported stale entries are known to correspond to a
previously reviewed and merged reference batch:

~~~bash
bibreview --config bibreview.yml references --reconcile-applied
~~~

For an actionable report entry with no existing ledger decision:

- canonical fingerprint still equals the report's source fingerprint: no
  reconciliation is needed; a later `--apply-safe` can record the deterministic
  outcome normally;
- canonical fingerprint differs from the source fingerprint: the explicit
  command records the current canonical fingerprint as
  `reconciled-current`;
- an existing decision is validated and never overwritten.

This command is intentionally **not** an inference mechanism. Invoking the
mutating form is the maintainer assertion that the changed canonical state is
the already-reviewed result of historical reference maintenance. It writes only
the resolution ledger, refuses occupied `collected.json`, and never changes
canonical bibliography data.

### Explicit human reference resolution

Cases that remain genuinely ambiguous after deterministic policy are resolved
offline and publication-by-publication:

~~~bash
bibreview --config bibreview.yml references --resolve
~~~

The resolver persists decisions in `resolutions.json` beside the configured
reference campaign/report. Resolution state is cumulative: it is keyed by the
publication UUID plus the exact source and provider reference fingerprints, so
later campaign batches may extend the report without invalidating earlier
decisions.

The interactive choices are:

- `k` — keep the complete current canonical reference list;
- `p` — use the complete persisted provider proposal;
- `c FILE` — use an explicitly reviewed JSON reference list from `FILE`;
- `s` — defer the publication;
- `q` — stop while preserving previous decisions.

A custom file may be either a JSON list of reference objects or
`{"references": [...]}`. Each reference has the ordinary BibReview
`identifiers` / `citation` shape.

The resolver never changes canonical metadata. Once every current human case is
terminally resolved, preview and stage reviewed decisions with:

~~~bash
bibreview --config bibreview.yml --dry-run references --apply
bibreview --config bibreview.yml references --apply
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

Application is fully offline and preserves the ordinary staging boundary.
`keep-canonical` is a terminal decision that requires no staging.
`use-provider` and `custom` stage complete revised Publication objects in
`collected.json`.

Every terminal decision stores a `resolved_fingerprint`. During later runs,
BibReview distinguishes three states:

- canonical fingerprint equals the source fingerprint: the decision is known
  but has not yet been promoted;
- canonical fingerprint equals the resolved fingerprint: the decision has
  already been merged;
- canonical fingerprint matches neither: the decision is stale and application
  is refused.

This per-publication evidence contract is deliberately independent of a global
report fingerprint because the reference report grows as new campaign batches
are appended.

v1.6.29 uses reference report **schema v2**. A v1.6.28 schema-v1
campaign/report cannot be resumed under the new two-round semantics. Archive
both files together, then start a fresh campaign. For the default layout:

~~~bash
mv audit/references audit/references-v1.6.28
bibreview references --batch-size 100
~~~

Reference report schema v2 remains the evidence basis for the reviewed
application workflow. Provider reconstruction and safe application stay
separate so improved comparison/application policy never rewrites persisted
provider evidence.

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
and spacing variants before provider support is counted.

If an abstract contains structured markup that BibReview cannot normalize
losslessly, the raw provider payload is retained in the audit report with
classification `provider-review-required`. It remains visible in verbose/JSON
review but is never counted as ordinary provider corroboration or disagreement
and is never actionable in `audit --resolve`.

One-day `created_date` offsets and obvious provider truncations of a fuller
canonical abstract remain informational. Provider-role disagreements stay
informational. Use `--json` for the complete machine-readable review.

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

Provider request pacing is controlled by each provider's
`min_interval_seconds` value in `bibreview.yml`. With BibReview's default audit
batch size of 50, one audit batch therefore normally needs two CrossRef requests,
one OpenAlex request, and one Semantic Scholar batch request. Provider failures are recorded
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

## relevance

Analyze accumulated relevance evidence without changing bibliography or
relevance configuration:

~~~bash
bibreview --config bibreview.yml relevance --analyze
~~~

Ordinary discovery and initialization screening persist the exact
title/abstract/keyword evidence already available during screening. This
retention does not perform an additional provider request. Explicit KEEP/REJECT
choices made through `bibreview review` are annotated in the same evidence
ledger, so later analysis can distinguish human decisions from automatic
terminal state.

`relevance --analyze` is fully offline and read-only. It reports:

- labeled KEEP/REJECT evidence and label provenance;
- a replay of the **current** accept/reject rules over retained evidence;
- automatic coverage plus agreement/disagreement with current project state;
- the same replay restricted to explicitly human-reviewed examples, where
  precision is a genuine human-label metric;
- per-pattern support, class counts, precision, unique coverage, and conflicts;
- interpretable unigram/bigram/trigram signals ranked specifically by their
  ability to resolve candidates that the **current rules would still send to
  manual review**, using support and a conservative Wilson lower confidence
  bound;
- bounded two-signal co-occurrence candidates mined from that same review gap,
  revalidated against all retained labels, with a deterministic two-lookahead
  regex rendered for inspection/copying.

Statistical feature extraction strips HTML/JATS/XML wrapper syntax before
tokenization so provider markup names cannot become candidate scientific
signals. This cleanup is analysis-only: current-rule replay still uses the exact
retained screening surface.

Machine-readable output is available with:

~~~bash
bibreview --config bibreview.yml relevance --analyze --json
~~~

Candidate-signal ranking deliberately focuses on the current review gap rather
than already-automated publications. A phrase that perfectly describes papers
already covered by existing rules is therefore not promoted merely because it
has high global precision.

Contextual candidates combine two independently recurring textual signals. Pair
mining is bounded to a compact pool of recurrent/discriminative features, skips
pairs where one phrase is wholly contained in the other, and requires the
conjunction to improve review-gap precision by at least 0.10 over the better
single constituent. Candidates must span at least two initialization batches
and remain directionally stable in at least 75% of the represented batches.

BibReview then distinguishes exploratory evidence from promotion-ready evidence.
A candidate is promotion-ready only when its review-gap support is at least 6,
observed agreement is at least 0.95, the Wilson lower confidence bound is at
least 0.60, and the evidence spans at least 3 batches. Candidates below those
thresholds remain available in JSON for inspection but are not presented as
rules ready to promote.

The report includes review-gap support, precision gain, confidence, batch
agreement, and all-history validation. The emitted regex uses two positive
lookaheads; it is a copyable proposal, not executable project mutation.

The statistical signals are suggestions only. BibReview never writes
`relevance.patterns` or `relevance.reject_patterns` automatically, and no
classifier is introduced into screening. Maintainers can inspect the evidence,
choose a narrow deterministic rule, edit `bibreview.yml`, and then use the
ordinary rescreen/review workflow.

For a brand-new project, start with a deliberately broad or minimal relevance
policy, review the first small batches, then rerun `relevance --analyze` as
human labels accumulate. Very small samples remain descriptive: one-off phrases
are not promoted as high-confidence evidence merely because their observed
precision is 100%.

Projects created before persistent relevance evidence can run a one-time
compatibility backfill:

~~~bash
bibreview --config bibreview.yml --dry-run relevance --backfill-evidence
bibreview --config bibreview.yml relevance --backfill-evidence
~~~

Canonical accepted publications are reconstructed locally from
`bibliography.json`; only rejected DOI records still missing evidence require
provider lookup. The normal provider cache applies. This migration never changes
canonical bibliography, DOI queues, or relevance decisions.

## review

Resolve DOI values in the manual relevance queue (`data/checkID.txt`) with an
explicit human decision:

~~~bash
bibreview --config bibreview.yml review
~~~

For each DOI, BibReview refreshes the configured discovery metadata and shows:

- a clickable DOI resolver link on supporting terminals;
- title and publication type;
- abstract and its source when available;
- keywords;
- matches against the **current** `relevance.patterns` and
  `relevance.reject_patterns`;
- the current initialization batch and attempt when the DOI belongs to an open
  `bibreview init` batch.

The prompt accepts:

- **k / keep** — move the DOI from review to pending collection;
- **r / reject** — move it from review to the rejected queue;
- **s / skip / defer** — leave it in review for a later session;
- **q / quit** — stop cleanly.

Each KEEP/REJECT decision is persisted immediately. Therefore Ctrl-C, EOF, or
`q` preserves earlier decisions and a later `bibreview review` naturally
resumes with the remaining DOI values. The command never collects metadata into
`collected.json` and never merges anything into the canonical bibliography.

For initialization candidates, KEEP changes the init report outcome from
`review` to `queued` while leaving the campaign item active until normal
collection/merge completes it. REJECT records a terminal rejected outcome and
completes the campaign item; a fully resolved batch is closed immediately.

Preview all current cases without prompting or writing:

~~~bash
bibreview --config bibreview.yml --dry-run review
~~~

Machine-readable refreshed evidence is available in dry-run mode:

~~~bash
bibreview --config bibreview.yml --dry-run review --json
~~~

Because queue state stores identifiers rather than a metadata snapshot, review
evidence is refreshed from providers at review time. Pattern diagnostics
therefore describe the **current** project relevance rules; the final
KEEP/REJECT boundary remains explicitly human.

## collect

Collect metadata for DOI values in the pending queue and write canonical staging
plus available BibTeX files:


As of v1.6.36, collection applies the conservative structured metadata policy
before any new record is staged:

- publication titles pass through the title normalizer **before permalink/slug
  generation**;
- deterministic entity, presentation-wrapper, trusted TeX and supported MathML
  cleanup is canonicalized immediately;
- a title with unsupported or ambiguous structured markup is rejected with its
  refusal reason rather than flattened or sent to an interactive collection
  review;
- DOI-formatted reference citations are sanitized first, then fall back to the
  CrossRef unstructured citation and finally to CrossRef's structured
  author/title/journal/year fields;
- when a DOI-backed reference has no safe textual citation, its DOI identity is
  retained with an empty citation and renders as a DOI-only reference;
- an unsafe DOI-less reference with no deterministic textual fallback is
  omitted rather than persisted as contaminated canonical text.

Refresh remains non-interactive: when provider title structure is unsafe, its
raw title is retained as provider evidence for comparison rather than causing
historical canonical metadata to be rewritten.


~~~bash
bibreview --config bibreview.yml collect
~~~

Collection refuses to overwrite a non-empty staging bibliography.

## import

Stage one reviewed publication that cannot use the DOI acquisition chain.

First create a human-editable manifest with a persistent BibReview UUID:

~~~bash
bibreview import --init publication.yml
~~~

Initialization is intentionally a real write and cannot be combined with
`--dry-run`: the generated UUID is persisted immediately so later metadata
edits, dry-runs and retries cannot change canonical identity.

Use the repository-level [`publication.example.yml`](../publication.example.yml)
as a field reference while editing the generated manifest. Do **not** copy or
rename that example to begin a real import: its UUID is illustrative, and
`--init` deliberately refuses to overwrite an existing file. Always initialize
the real manifest first, then transfer only the reviewed bibliographic fields.

Edit the manifest, then validate the complete import without writing project
state:

~~~bash
bibreview --dry-run import publication.yml
~~~

Stage the reviewed publication:

~~~bash
bibreview import publication.yml
~~~

The command:

- accepts DOI-less publications only;
- refuses a `doi` identifier and redirects that case conceptually to the
  existing DOI discovery/collection workflow;
- accepts auxiliary identifiers such as ISBN, arXiv, PMID, PMLR or
  publisher-specific identifiers without promoting them to strong identity keys;
- requires explicit provenance (`manual`, `official-import`, or `provider`);
- requires reviewed BibTeX content and never fabricates it;
- generates a permalink from the title when the manifest leaves it empty;
- refuses canonical UUID/permalink collisions;
- reports matching auxiliary identifiers and matching normalized title/year as
  possible-duplicate warnings without auto-merging;
- refuses non-empty `collected.json` staging;
- writes only `collected.json`, the tracked `bib/<permalink>.bib`, and the
  normalized evidence sidecar under `data/imports/<UUID>.yml`.

It does **not** modify `bibliography.json` or `ID.txt`. Review the staged
diff, then use the ordinary promotion boundary:

~~~bash
bibreview --dry-run merge
bibreview merge
~~~

The v1 import manifest has the shape below. The complete maintained field-reference
example is [`publication.example.yml`](../publication.example.yml):

~~~yaml
schema_version: 1
id: 550e8400-e29b-41d4-a716-446655440000

provenance:
  kind: official-import
  source: https://example.org/official-record
  note: Reviewed against the official proceedings page.

citation: "Optional human-readable citation"

publication:
  identifiers:
    pmlr: "331:example"
  type: proceedings-article
  title: Example DOI-less publication
  authors:
    - given: Ada
      family: Lovelace
  editors: []
  abstract: ""
  container_title: Example Proceedings
  publication_year: "2026"
  volume: "331"
  issue: ""
  pages: "1--10"
  publisher: ""
  event: ""
  keywords: []
  created_date: null
  permalink: ""
  references: []

bibtex: |
  @inproceedings{example,
    title = {Example DOI-less publication}
  }
~~~

For `official-import` and `provider` provenance, `provenance.source` is
required. Manual provenance may leave it empty.

## backfill

Propose values for selected fields that are semantically missing in existing
canonical publications. Provider-backed proposal generation requires a DOI;
manual abstract mode also supports DOI-less records. For `abstract`, the historical
`Not Available` placeholder is treated as missing case- and
whitespace-insensitively:

~~~bash
bibreview --config bibreview.yml backfill --field abstract
~~~

Repeat `--field` to propose more than one scalar field. Use repeated `--type`
options to restrict proposal generation to selected publication types. Existing
non-empty canonical fields are never proposed for replacement.

For a missing abstract that must be supplied by a maintainer rather than a
provider, use manual mode:

~~~bash
bibreview --config bibreview.yml backfill --field abstract --manual
~~~

Manual mode performs **no provider lookup** and therefore also works for
DOI-less canonical publications. It creates one `review_required` candidate
with no automatic value for each semantically missing abstract. The normal
resolver then requires an explicit **f VALUE**, reject, or defer decision;
direct Enter/Y acceptance is disabled. Manual mode is currently exposed only
for `--field abstract`.

Proposal generation may call CrossRef, publisher enrichment, and configured
abstract fallbacks such as OpenAlex, Semantic Scholar, and Mendeley. Backfill
uses exact multi-DOI requests where the provider adapter supports them: CrossRef
work metadata is chunked at 25 DOI values, OpenAlex abstract fallback at 100,
and Semantic Scholar abstract fallback at 500. Publisher enrichment remains
per DOI because provider selection depends on the resolved publisher host, and
Mendeley remains per DOI because the current adapter has no exact batch lookup.
Failed CrossRef batches fall back to individual DOI requests for that chunk;
non-rate-limit OpenAlex/Semantic Scholar batch failures do the same, while a
persistent HTTP 429 preserves the existing run-scoped provider disable policy.
Batching does
**not** write `collected.json` or modify the canonical bibliography. Instead it
persists a fingerprinted local proposal set beside the configured audit state.

Review those proposals interactively:

~~~bash
bibreview --config bibreview.yml backfill --resolve
~~~

The resolver presents one missing field at a time. Normal safe proposals use
the usual controls:

- **Enter** or **Y** — accept the proposed value;
- **n** — reject it;
- **f VALUE** — store an explicit human-selected value;
- **s** — defer it for a later session;
- **q** — stop cleanly while preserving previous decisions.

For an abstract that has **no safe automatic value but does have refused provider
evidence**, BibReview persists a `review-required` candidate instead of treating
the field as `no_value`. The resolver shows each retained provider source,
refusal reason, and raw payload. Direct **Enter/Y acceptance is disabled** for
that case: use **f VALUE** to supply a reviewed safe abstract, **n** to reject the
evidence, **s** to defer, or **q** to stop.

If a safe provider value exists alongside refused alternatives, the safe value
remains the ordinary proposal and the refused payloads are shown as additional
evidence. Decisions and retained evidence are fingerprinted together, so a
changed evidence payload invalidates stale resolution state.

Nothing reaches canonical staging until every proposal has a final accepted,
custom, or rejected decision:

~~~bash
bibreview --config bibreview.yml --dry-run backfill --apply
bibreview --config bibreview.yml backfill --apply
~~~

`backfill --apply` stages only accepted/custom values in `collected.json`.
It refuses non-empty staging, unresolved/deferred decisions, stale proposal
fingerprints, missing canonical publications, or fields that are no longer
semantically missing. An accepted/custom abstract cannot itself be a
`Not Available` placeholder. It never edits `bibliography.json` directly and
does not rewrite tracked
BibTeX as part of missing-field enrichment.

Inspect the staged JSON, then use the normal canonical boundary:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

## refresh

Inspect configured incomplete existing records and use remote BibTeX only to
detect whether provider metadata appears stale:

~~~bash
bibreview --config bibreview.yml refresh
~~~

The networked scan does **not** write `collected.json` and does **not** replace
tracked BibTeX. For each stale publication, BibReview recollects metadata in
memory and compares it with the canonical record using the audit equivalence
rules.

Only configured fields that are currently empty may become proposals.
Meaningful differences on any already-populated field are stored as collateral
evidence and can never be applied by refresh.

For a missing abstract, structured provider payloads follow the same conservative
normalization policy as collection. A safe abstract becomes a normal proposal.
Refused alternatives remain attached as provider evidence. If no safe abstract
exists but refused evidence does, refresh stores a `review-required` proposal
instead of discarding the payload.

Review the persisted result offline:

~~~bash
bibreview --config bibreview.yml refresh --review
bibreview --config bibreview.yml -v refresh --review
~~~

The default review prints aggregate counts, including the number of
review-required proposals. Verbose review shows every safe missing-field
proposal, every retained provider abstract evidence item, and every collateral
current/provider difference.

Resolve proposals interactively:

~~~bash
bibreview --config bibreview.yml refresh --resolve
~~~

The resolver reuses the backfill decision model. For an ordinary safe proposal:

- **Enter** or **Y** — accept the proposed missing-field value;
- **n** — reject it;
- **f VALUE** — choose an explicit custom value;
- **s** — defer it;
- **q** — stop and resume later.

For a `review-required` abstract, direct **Enter/Y acceptance is disabled**.
The resolver displays the provider source, refusal reason, and raw payload; use
**f VALUE** for an explicit reviewed replacement, **n** to reject, **s** to
defer, or **q** to stop.

Collateral differences are deliberately absent from the resolver because refresh
has no code path that can promote them.

After every proposal has a final decision:

~~~bash
bibreview --config bibreview.yml --dry-run refresh --apply
bibreview --config bibreview.yml refresh --apply
~~~

`refresh --apply` rechecks that every accepted/custom canonical field is still
semantically missing. It stages only those reviewed fills in `collected.json`.
Existing title, authors, container, dates, publisher, and other meaningful
canonical values remain
untouched even when the provider recollection differs.

Tracked BibTeX is synchronized conservatively only for accepted fields. BibReview
edits the existing single-entry BibTeX field-by-field and creates an archive
backup first. The remote DOI BibTeX is **never copied wholesale**, so a manual
BibTeX correction cannot be silently replaced merely because the provider
continues to return different text.

Inspect the staged JSON and BibTeX diff, then use the ordinary merge boundary:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

A non-empty staging bibliography blocks both refresh scanning and refresh
application.

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

Review the remaining ambiguous identities interactively:

~~~bash
bibreview --config bibreview.yml authors --review
~~~

Preview the same residual evidence without writing or prompting:

~~~bash
bibreview --config bibreview.yml --dry-run authors --review
~~~

Machine-readable analysis:

~~~bash
bibreview --config bibreview.yml authors --json
~~~

Ambiguous proposals are never applied automatically. Interactive review
requires an explicit human choice for every mapping or new identity and writes
only `author_mappings.json`. See [Author identities](authors.md).

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
