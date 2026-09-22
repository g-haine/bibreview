# Release checklist

Use this checklist for every BibReview release. A release is not complete when
the code alone is ready: version metadata, user-facing documentation, examples,
validation, and the release tag must remain consistent.

## 1. Stabilize the release candidate

- merge or close the feature/fix pull requests intended for the release;
- keep unrelated work out of the release branch;
- run the complete test suite:

~~~bash
python -m unittest discover -s tests -v
~~~

- run any relevant real-data or regression-corpus validation in addition to
  synthetic tests;
- record important validation results in the changelog when they materially
  describe the release.

## 2. Update version metadata

Update every authoritative runtime/package version:

- `pyproject.toml`;
- `src/bibreview/__init__.py`.

Then search the repository for the previous version string and inspect every
remaining occurrence:

~~~bash
git grep -n "<OLD_VERSION>"
~~~

Historical changelog entries must of course retain their original version
numbers.

## 3. Update user-facing documentation

Check all places where users may discover, install, configure, or automate
BibReview:

- `README.md`:
  - current stable release;
  - installation command/tag;
  - concise description of important user-visible behavior changes;
- `CHANGELOG.md`:
  - dated release entry;
  - notable behavior, compatibility, and validation changes;
- `docs/installation.md`:
  - release number;
  - pinned installation commands;
  - expected `bibreview --version` output;
- `docs/github-pages.md`:
  - pinned BibReview version in every workflow example;
- command/data/configuration/workflow documentation affected by the release;
- `bibreview.example.yml` and examples when configuration or interfaces change.

Do not update documentation mechanically if a historical example is intentionally
version-specific; inspect each occurrence.

## 4. Validate the release branch

Before opening or merging the release pull request:

- run the full test suite;
- verify the package metadata and source-tree runtime version without requiring
  a prior installation:

~~~bash
grep '^version' pyproject.toml
PYTHONPATH=src python -c "import bibreview; print(bibreview.__version__)"
~~~

  Both must report the intended version;
- inspect `git diff main...HEAD`;
- repeat the old-version search and confirm only intentional historical
  references remain;
- confirm README, changelog, installation guide, GitHub Pages examples, and
  affected reference documentation agree.

## 5. Release pull request

The release PR should contain only the changes required to make the already
validated codebase a coherent release.

Its summary should explicitly mention:

- version bump;
- changelog;
- README;
- documentation/examples updated;
- validation performed.

Merge only after CI is green and the final diff has been reviewed.

## 6. Tag and post-release verification

After the release PR is merged into `main`:

~~~bash
git switch main
git pull --ff-only
git tag -a v<X.Y.Z> -m "BibReview v<X.Y.Z>"
git push origin v<X.Y.Z>
~~~

Then verify the released tag itself:

~~~bash
python -m pip install --force-reinstall \
  "git+https://github.com/g-haine/bibreview.git@v<X.Y.Z>"
bibreview --version
~~~

Finally, update downstream projects deliberately to the new exact tag and run
their normal validation/build workflows before committing the new pin.

## Release invariant

At the moment a version is tagged, these must agree:

~~~text
pyproject.toml
src/bibreview/__init__.py
README.md
CHANGELOG.md
docs/installation.md
docs/github-pages.md
release tag
~~~

Any user-facing command, configuration, or behavior changed by the release must
also be reflected in the corresponding reference documentation.
