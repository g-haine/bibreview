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
- every canonical publication in which the unresolved name occurs;
- preserved ORCID and affiliation source fields when available.

Those source fields are **evidence for the human reviewer only**. They are not
automatic identity keys.

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
