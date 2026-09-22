# Manual corrections and provider errors

Provider output is input data, not unquestionable truth. BibReview deliberately
keeps staging and source files inspectable so a maintainer can correct errors
before publication.

Always make corrections in a clean Git working tree when possible.

## Wrong metadata detected before merge

After **collect**, **refresh**, or **audit --apply**, inspect the configured collected.json.

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

## Persistent provider error and refresh

A manually corrected BibTeX may differ from the remote DOI BibTeX. Refresh only
examines publications selected by refresh.types and refresh.when_missing_any.

If a manually corrected publication remains refresh-eligible, a later refresh
may stage the remote provider version again. Do **not** blindly merge that
staging batch. Either:

- correct the staged record again before merge;
- narrow or temporarily disable the refresh policy while the provider remains wrong;
- or make the canonical record complete enough that it is no longer selected by
  the configured missing-field policy, when that is factually correct.

When the remote BibTeX response is empty, BibReview retains the existing local
BibTeX and does not overwrite it.

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
