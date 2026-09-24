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

## 3. Complete the small Jekyll contract

BibReview renders publication posts plus author/year index pages. Your Jekyll
site still owns layouts, category landing pages and shared includes.

By default, generated author/year pages include:

~~~liquid
{% include count-posts.html %}
~~~

Create site/_includes/count-posts.html, for example:

~~~html
<script>
  const list = document.getElementsByClassName('post-list')[0];
  const count = list ? list.getElementsByTagName('li').length : 0;
  const target = document.getElementById('number-posts');
  if (target) target.textContent = `There are ${count} items referenced.`;
</script>
~~~

If you do not want this behavior, set **site.jekyll.count_posts_include** to an
empty string in bibreview.yml.

If you configure category names such as **articles**, **books**, **chapters** or
**proceedings**, create matching Jekyll category landing pages yourself. A
minimal page looks like:

~~~markdown
---
layout: category
title: Articles
category: articles
permalink: /categories/articles
---
~~~

Your theme must provide the corresponding **category** layout, or you may choose
another category-page implementation.

## 4. Render and preview locally

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

## 5. Enable GitHub Pages

In the GitHub repository:

1. open **Settings → Pages**;
2. under **Build and deployment**, select **GitHub Actions** as the source.

GitHub's current custom-workflow documentation is:
<https://docs.github.com/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages>.

## 6. Build and deploy with GitHub Actions

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
        uses: actions/checkout@v7

      - name: Set up Python
        uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - name: Install pinned BibReview
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.6.23"

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

The example above pins BibReview to **v1.6.23**. Keep an exact release tag or
commit pin for reproducibility, and update it deliberately when adopting a newer
BibReview release.

GitHub's current Pages documentation uses configure-pages v5,
upload-pages-artifact v4 and deploy-pages v4 for custom Pages workflows.
checkout/setup-python are shown here at their current major versions. Recheck
the official documentation when you create or substantially update the
deployment workflow.

## 7. Schedule the optional arXiv cache

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
        uses: actions/checkout@v7

      - name: Set up Python
        uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - name: Install pinned BibReview
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.6.23"

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

GitHub Actions schedules use POSIX cron. UTC is the default, and GitHub now
also supports an optional IANA timezone on scheduled triggers. Scheduled jobs
can be delayed during periods of high GitHub Actions load, especially near the
start of an hour, so do not use this mechanism for time-critical tasks.

A commit produced by this workflow can trigger the normal Pages deployment
workflow.

## 8. Optional scheduled discovery

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

## 9. Generated files: committed or CI-only?

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

## 10. Privacy-friendly analytics

For optional traffic statistics, BibReview recommends considering GoatCounter.
It is not required and is not injected automatically.

See [GoatCounter analytics](goatcounter.md).

## 11. Showcase

PHRAISE is a public BibReview-powered Jekyll site and can be used as an
integration reference. Project-specific content and policy remain outside the
BibReview engine.
