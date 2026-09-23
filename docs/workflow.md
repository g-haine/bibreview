# Local workflow

This is the recommended human-reviewed maintenance cycle.

## 1. Validate the project

~~~bash
bibreview --config bibreview.yml validate
bibreview --config bibreview.yml status
~~~

Commit a clean baseline before a substantial update.

## Optional: audit historical canonical metadata

A historical/data-quality audit is separate from the normal update cycle. Start
with a pilot batch when introducing audit to an established project:

~~~bash
bibreview --config bibreview.yml --dry-run audit --batch-size 25
bibreview --config bibreview.yml audit --batch-size 25
~~~

Inspect the configured audit report after each invocation. Provider differences
are hypotheses to review, not automatic corrections. Apply any justified
canonical/BibTeX/identity correction explicitly through the normal reviewed
project workflow.

Each later invocation processes the next batch. Omitting `--batch-size`
returns to the configured default (50 in the example configuration):

~~~bash
bibreview --config bibreview.yml audit
~~~

The audit directory acts as persistent local history: completed publication
UUIDs are not revisited by default, while publications added to the canonical
bibliography later are appended automatically as new pending work. If a run is
interrupted, invoke the same command again: the open batch is resumed and
already checkpointed publications are not repeated. Retryable provider failures
are revisited only after never-yet-audited publications have received their
first pass.

When you deliberately want fresh provider evidence for the whole current
bibliography, use:

~~~bash
bibreview --config bibreview.yml audit --full
~~~

This requeues all current canonical publication UUIDs while preserving the
existing audit history and attempt counters.

When the campaign is complete, derive the concise review and resolve every
actionable finding explicitly:

~~~bash
bibreview --config bibreview.yml audit --review
bibreview --config bibreview.yml audit --resolve
~~~

Then promote the completed human decisions through the normal staging boundary:

~~~bash
bibreview --config bibreview.yml --dry-run audit --apply
bibreview --config bibreview.yml audit --apply
~~~

The apply step requires empty **collected.json**, refuses stale canonical values,
stages only accepted/custom corrections, updates applicable tracked BibTeX with
backups, and leaves rejected decisions unchanged. Inspect the staging JSON and
BibTeX diff before merging:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

Do not start a collect/refresh/backfill batch while audit corrections remain staged.

## 2. Discover candidate publications

Preview:

~~~bash
bibreview --config bibreview.yml --dry-run discover
~~~

Apply:

~~~bash
bibreview --config bibreview.yml discover
~~~

Discovery updates the pending, review and rejected DOI queues according to the
configured relevance policy.

## 3. Review uncertain DOI candidates

Open the configured review file. For each DOI:

- move it to the pending file if it belongs in the bibliography;
- move it to the rejected file if it does not;
- remove it from the review file once decided.

This is intentionally a human decision.

## 4. Refresh existing incomplete records

Refresh, collection, and audit application share the same staging file, so handle any existing audit staging before refresh:

~~~bash
bibreview --config bibreview.yml --dry-run refresh
bibreview --config bibreview.yml refresh
~~~

Inspect:

- collected.json;
- any reported BibTeX backup;
- the changed or newly created BibTeX files.

If the staging data is correct:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

If a provider response is wrong, correct it before merging. See
[Manual corrections](corrections.md).

## Optional: backfill missing canonical fields

For an existing record that is otherwise reviewed but lacks a field such as an
abstract, use the human-reviewed backfill workflow instead of recollecting the
entire publication:

~~~bash
bibreview --config bibreview.yml backfill --field abstract
bibreview --config bibreview.yml backfill --resolve
bibreview --config bibreview.yml --dry-run backfill --apply
bibreview --config bibreview.yml backfill --apply
~~~

Proposal generation does not touch canonical or staging data. Resolve every
proposal explicitly; only accepted/custom decisions are staged. Inspect
`collected.json`, then promote with the ordinary merge boundary:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

Backfill never replaces a non-empty canonical value and refuses stale proposals
when the field was filled after proposal generation.

## 5. Collect new pending DOI values

~~~bash
bibreview --config bibreview.yml --dry-run collect
bibreview --config bibreview.yml collect
~~~

Inspect the staged JSON and BibTeX before accepting it.

A publication can be collected even when its provider BibTeX is unavailable; in
that case no fake BibTeX file is created. Add a correct BibTeX manually before
rendering the site.

Then merge:

~~~bash
bibreview --config bibreview.yml --dry-run merge
bibreview --config bibreview.yml merge
~~~

## 6. Resolve author identities

~~~bash
bibreview --config bibreview.yml authors
bibreview --config bibreview.yml authors --apply-safe
bibreview --config bibreview.yml authors
~~~

The final command should leave only genuinely ambiguous cases. Resolve those by
editing the author mapping file manually. See [Author identities](authors.md).

## 7. Render the site

Preview the reconciliation plan:

~~~bash
bibreview --config bibreview.yml --dry-run render
~~~

Apply:

~~~bash
bibreview --config bibreview.yml render
~~~

Inspect **git diff**. Rendering should only touch BibReview-managed generated
site artifacts.

## 8. Refresh optional arXiv links

If enabled:

~~~bash
bibreview --config bibreview.yml arxiv
~~~

This cache is separate from the canonical bibliography.

## 9. Preview Jekyll

From the site source directory:

~~~bash
bundle install
bundle exec jekyll serve
~~~

Check generated publication pages, author indexes, year indexes, links and
search behavior.

## 10. Commit

Before committing:

~~~bash
git status
git diff
~~~

A useful maintenance commit contains canonical state, reviewed mapping changes,
correct BibTeX and deterministic rendered artifacts together.
