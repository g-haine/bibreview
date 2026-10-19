# Author identities and ambiguous names

BibReview keeps source-visible author names separate from reviewed site
identities.

The mapping file has this shape:

~~~json
{
  "ada-lovelace": [
    "Ada Lovelace",
    "A. Lovelace"
  ],
  "example-research-consortium": [
    "Example Research Consortium"
  ]
}
~~~

Each exact source-visible name variant may belong to only one mapping slug.

## Inspect mappings

~~~bash
bibreview --config bibreview.yml authors
~~~

The report separates:

- **Safe proposals** — one unknown name, a new slug, and no plausible existing
  author with the same surname and first initial;
- **Manual review required** — possible collisions or ambiguous variants.

For machine-readable output:

~~~bash
bibreview --config bibreview.yml authors --json
~~~

## Apply safe mappings

Preview:

~~~bash
bibreview --config bibreview.yml --dry-run authors --apply-safe
~~~

Apply:

~~~bash
bibreview --config bibreview.yml authors --apply-safe
~~~

Only safe proposals are written. Ambiguous cases remain untouched.

## Resolve ambiguous variants interactively

After applying safe mappings, resolve the residual ambiguous names with:

~~~bash
bibreview --config bibreview.yml authors --review
~~~

BibReview presents one exact source-visible name at a time, together with:

- its proposed stable slug;
- the reason manual review is required;
- plausible existing identities and all their known variants;
- canonical publications already attached to each possible existing identity;
- every canonical publication in which the unresolved name occurs;
- preserved ORCID and affiliation source fields on both sides when available.

Those source fields are **evidence for the human reviewer only**. They are not
automatic identity keys. On an interactive terminal, every displayed DOI is an
OSC 8 hyperlink to its `https://doi.org/` resolver; non-interactive output
keeps the same plain DOI text.

For each case, the prompt accepts:

- **1..N** — map the exact source name to one of the displayed possible identities;
- **m SLUG** — map it explicitly to another existing identity;
- **n** — create a new identity using the proposed slug;
- **n SLUG** — create a new identity using an explicit distinct slug;
- **s** — defer the case without changing it;
- **q** — stop cleanly.

Every accepted human decision is written immediately through BibReview's author
mapping layer. Therefore **q**, Ctrl-C, EOF, or a later invocation naturally
resume from the remaining unresolved names; no separate resolution file is needed.

Creating a new identity with a slug that already exists is refused. Mapping to
an identity that does not exist is also refused. Each exact source-visible name
may still belong to only one identity.

To preview all residual cases and their evidence without prompting or writing:

~~~bash
bibreview --config bibreview.yml --dry-run authors --review
~~~

Interactive `--review` is intentionally incompatible with `--json` and
`--quiet`. The existing `authors --json` command remains the machine-readable
analysis surface.

After review, rerun:

~~~bash
bibreview --config bibreview.yml authors
~~~

Continue until the report is acceptable.

## Hygiene of already-mapped identities

Once every publication author is mapped, run:

~~~bash
bibreview --config bibreview.yml hygiene --authors
~~~

This is distinct from `authors --review`: it audits the **existing identity
graph** rather than unresolved source names. It can flag weak initial-based
slugs, encoding-normalization duplicates, initials/full-name pairs, and mapped
author names that look institution- or affiliation-like (for example
departments, universities, institutes, laboratories, centres, schools, or
numbered affiliation strings).

Use `-v` or `--json` to inspect all candidates. No candidate is merged or
removed automatically. In particular, an institution-like name may be a
legitimate collective author and always remains a human-review decision.

For suspicious contributor entries inside canonical publications, review and
resolve them separately from identity merging:

~~~bash
bibreview hygiene --authors --review
bibreview hygiene --authors --resolve
bibreview --dry-run hygiene --authors --apply
bibreview hygiene --authors --apply
bibreview --dry-run merge
bibreview merge
~~~

KEEP/REMOVE decisions are persisted and resumable. Applying them stages only
the affected publications, preserving UUID, permalink, retained contributor
order, and contributor source fields. Removed exact name variants are reconciled
from author mappings only at the subsequent canonical merge, and only when no
canonical publication still uses the variant.

After validating the evidence, use the first-class mapping operations instead
of editing `author_mappings.json` directly:

~~~bash
bibreview --config bibreview.yml --dry-run hygiene --authors \
  --merge-author SOURCE TARGET
bibreview --config bibreview.yml hygiene --authors \
  --merge-author SOURCE TARGET

bibreview --config bibreview.yml --dry-run hygiene --authors \
  --rename-author OLD_SLUG NEW_SLUG
bibreview --config bibreview.yml hygiene --authors \
  --rename-author OLD_SLUG NEW_SLUG
~~~

A merge requires both identities to exist. A rename requires the target slug
not to exist. Both operations validate the invariant that each exact
source-visible name variant belongs to only one identity.

## Same exact display name for two different people

The current mapping model requires one exact name string to map to one identity.
If two different people are supplied by providers under the **exact same
display name**, BibReview cannot disambiguate them from the name string alone.

Do not guess. Keep the case under human review and improve the source-visible
identity information before mapping it, or leave the site mapping unresolved
until the project has a reliable distinction.

## Source fields

Canonical authors may preserve provider-specific source fields such as ORCID or
affiliation metadata, but the site identity mapping is intentionally based on
reviewed display-name variants.

Source metadata can help a human decide whether two variants identify the same
person; it is not currently an automatic merge key.
