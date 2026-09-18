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

## Resolve an ambiguous variant

Suppose the report says:

~~~text
? a-lovelace: A. Lovelace
  Possible match: ada-lovelace (Ada Lovelace)
~~~

If it is the same person, append the exact variant to the existing identity:

~~~json
{
  "ada-lovelace": [
    "Ada Lovelace",
    "A. Lovelace"
  ]
}
~~~

Do not create a second slug for the same person.

If it is a genuinely different person, create a distinct stable slug and assign
the source name to that slug.

Then rerun:

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
