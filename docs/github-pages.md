# GitHub Pages with BibReview and Jekyll

BibReview renders bibliographic content into an existing Jekyll site. It does
not impose a theme or deploy the site for you.

A typical repository looks like this:

~~~text
bibreview.yml
data/
  bibliography.json
  collected.json
  authors.json
  known.txt
  pending.txt
  rejected.txt
  review.txt
bib/
archive/
site/
  _config.yml
  Gemfile
  _layouts/
  _includes/
  assets/
.github/
  workflows/
~~~

Generated publication posts, author pages and year pages are created under the
configured site source by **bibreview render**.

## 1. Configure BibReview

Example:

~~~yaml
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/authors.json
  known: data/known.txt
  pending: data/pending.txt
  rejected: data/rejected.txt
  review: data/review.txt
  bibtex: bib
  archive: archive
  site: site

site:
  enabled: true
  implementation: jekyll
  source: site
~~~

Add your discovery, relevance, provider and rendering policies as described in
[Configuration](configuration.md).

## 2. Create the Jekyll site

A minimal Gemfile may start with:

~~~ruby
source "https://rubygems.org"

gem "github-pages", group: :jekyll_plugins

group :jekyll_plugins do
  gem "jekyll-feed"
  gem "jekyll-seo-tag"
  gem "jekyll-sitemap"
end
~~~

A minimal site/_config.yml may contain:

~~~yaml
title: My Literature Review
url: "https://USERNAME.github.io"
baseurl: "/REPOSITORY"

theme: minima

plugins:
  - jekyll-feed
  - jekyll-seo-tag
  - jekyll-sitemap

defaults:
  - scope:
      type: posts
    values:
      layout: post
~~~

Adapt the theme, layouts, navigation and CSS to your project. BibReview only
owns the bibliographic generated artifacts.

## 3. Render and preview locally

From the repository root:

~~~bash
bibreview --config bibreview.yml render
~~~

Then:

~~~bash
cd site
bundle install
bundle exec jekyll serve
~~~

Open the local URL printed by Jekyll.

## 4. Enable GitHub Pages

In the GitHub repository:

1. open **Settings → Pages**;
2. under **Build and deployment**, select **GitHub Actions** as the source.

GitHub's current custom-workflow documentation is:
<https://docs.github.com/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages>.

## 5. Build and deploy with GitHub Actions

Create .github/workflows/pages.yml:

~~~yaml
name: Deploy Pages

on:
  push:
    branches:
      - main
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: github-pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    env:
      BUNDLE_GEMFILE: site/Gemfile

    steps:
      - name: Check out project
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install pinned BibReview
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@<PINNED-TAG-OR-COMMIT>"

      - name: Render bibliography
        run: bibreview --config bibreview.yml render

      - name: Set up Ruby
        uses: ruby/setup-ruby@v1
        with:
          ruby-version: "3.1"
          bundler-cache: true

      - name: Configure Pages
        uses: actions/configure-pages@v5

      - name: Build Jekyll
        run: bundle exec jekyll build --source site --destination site/_site

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v4
        with:
          path: site/_site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages

    steps:
      - name: Deploy Pages
        uses: actions/deploy-pages@v4
~~~

Pin BibReview to a release tag or commit for reproducibility.

GitHub currently documents configure-pages v5, upload-pages-artifact v4 and
deploy-pages v4 for custom Pages workflows. Recheck the official documentation
when you create or substantially update the deployment workflow.

## 6. Schedule the optional arXiv cache

If the arXiv module is enabled, it can be refreshed automatically because it is
display-only and independent from the curated canonical bibliography.

Example .github/workflows/update-arxiv.yml:

~~~yaml
name: Update arXiv cache

on:
  workflow_dispatch:
  schedule:
    - cron: "17 7 * * *"

permissions:
  contents: write

concurrency:
  group: update-arxiv-cache
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Check out project
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install pinned BibReview
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@<PINNED-TAG-OR-COMMIT>"

      - name: Refresh arXiv cache
        run: bibreview --config bibreview.yml arxiv

      - name: Commit cache if changed
        run: |
          if [ -z "$(git status --porcelain -- site/data/arxiv.json)" ]; then
            echo "No arXiv cache changes."
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add site/data/arxiv.json
          git commit -m "Update arXiv cache"
          git push
~~~

GitHub Actions cron expressions are evaluated in UTC. Scheduled jobs can also be
delayed during periods of high GitHub Actions load, so do not use this mechanism
for time-critical tasks.

A commit produced by this workflow can trigger the normal Pages deployment
workflow.

## 7. Optional scheduled discovery

Discovery can also be scheduled, but use this more conservatively because it
changes human-review queues.

A reasonable policy is:

- configure relevance.unmatched as **manual-review**;
- let a scheduled job run only **bibreview discover**;
- commit only the DOI queue files;
- review the resulting review queue manually before collection.

Do not fully automate collect → merge → authors for a curated scholarly
bibliography unless your project explicitly accepts provider output without
human review.

## 8. Generated files: committed or CI-only?

Both models are possible.

### Commit generated artifacts

Advantages:

- Git shows exactly what the public site will contain;
- local and CI builds can verify that render is a no-op;
- review diffs include generated publication pages.

### Generate only in CI

Advantages:

- smaller repository history;
- canonical JSON + BibTeX remain the source of truth.

Choose one model and apply it consistently. For a human-reviewed bibliography,
committing generated artifacts is often useful because the rendered diff becomes
part of review.

## 9. Privacy-friendly analytics

For optional traffic statistics, BibReview recommends considering GoatCounter.
It is not required and is not injected automatically.

See [GoatCounter analytics](goatcounter.md).

## Showcase

PHRAISE is a public BibReview-powered Jekyll site and can be used as an
integration reference. Project-specific content and policy remain outside the
BibReview engine.
