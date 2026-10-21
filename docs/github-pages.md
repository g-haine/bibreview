# GitHub Pages with BibReview

BibReview renders bibliographic content into an existing **Jekyll or Hugo**
site. It does not impose a theme or deploy the site for you.

A typical repository looks like this:

~~~text
bibreview.yml
data/
  bibliography.json
  collected.json
  author_mappings.json
  ID.txt
  newID.txt
  checkID.txt
  badID.txt
  imports/
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

Generated publication, author and year artifacts are created under the
configured site source by **bibreview render**. The exact generated paths depend
on the selected renderer.

## 1. Configure BibReview

Example:

~~~yaml
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/author_mappings.json
  known: data/ID.txt
  pending: data/newID.txt
  rejected: data/badID.txt
  review: data/checkID.txt
  bibtex: bib
  archive: archive
  site: site

site:
  enabled: true
  implementation: jekyll
  source: site
  jekyll:
    publish_data: true
~~~

Add your discovery, relevance, provider and rendering policies as described in
[Configuration](configuration.md).

For a Hugo project, the equivalent renderer selection is:

~~~yaml
site:
  enabled: true
  implementation: hugo
  source: site
  hugo:
    publication_path_prefix: publications
    author_path_prefix: authors
    year_path_prefix: years
    bibtex_asset_prefix: bib
    publish_data: true
~~~

## 2. Create the presentation site

### Jekyll

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

### Hugo

Create a Hugo site below the configured source path, for example:

~~~bash
hugo new site site --format yaml
~~~

A minimal `site/hugo.yaml` for a GitHub Pages project site may contain:

~~~yaml
baseURL: https://USERNAME.github.io/REPOSITORY/
locale: en-us
title: My Literature Review
~~~

The project supplies its own Hugo layouts, CSS, navigation and optional search
integration. BibReview generates bibliographic content and data but deliberately
does not choose a Hugo theme.

Do not commit `site/public/`; Hugo recreates it during the build.

## 3. Complete the presentation contract

### Jekyll

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

### Hugo

BibReview generates publication Markdown plus author and year content under the
configured Hugo prefixes. The Hugo project must provide layouts capable of
rendering those sections and regular publication pages.

Category landing pages remain project-owned. A project may derive categories
from the generated `publication_type` front matter without changing canonical
bibliographic state.

## 4. Render and preview locally

From the repository root:

~~~bash
bibreview --config bibreview.yml render
~~~

### Jekyll

~~~bash
cd site
bundle install
bundle exec jekyll serve
~~~

Open the local URL printed by Jekyll.

### Hugo

Build the production tree with:

~~~bash
hugo --source site --minify
~~~

Preview locally with:

~~~bash
hugo server --source site
~~~

Open the local URL printed by Hugo.

If the host project uses Pagefind or another search/indexing tool, run that
project-owned build step **after** Hugo has produced `site/public/`. BibReview
does not install or configure Pagefind itself.

## 5. Enable GitHub Pages

In the GitHub repository:

1. open **Settings → Pages**;
2. under **Build and deployment**, select **GitHub Actions** as the source.

GitHub's current custom-workflow documentation is:
<https://docs.github.com/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages>.

## 6. Build and deploy with GitHub Actions

### Jekyll

Create `.github/workflows/pages.yml`:

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
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.7.2"

      - name: Render bibliography
        run: bibreview --config bibreview.yml render

      - name: Set up Ruby
        uses: ruby/setup-ruby@v1
        with:
          ruby-version: "3.1"
          bundler-cache: true

      - name: Configure Pages
        uses: actions/configure-pages@v6

      - name: Build Jekyll
        run: bundle exec jekyll build --source site --destination site/_site

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v5
        with:
          path: site/_site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages

    steps:
      - name: Deploy Pages
        uses: actions/deploy-pages@v5
~~~

### Hugo

A minimal Hugo workflow follows the same BibReview-first boundary:

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

env:
  HUGO_VERSION: "0.168.0"

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Check out project
        uses: actions/checkout@v7

      - name: Set up Python
        uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - name: Install pinned BibReview
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.7.2"

      - name: Reconcile generated bibliography artifacts
        run: bibreview --config bibreview.yml render

      - name: Configure Pages
        id: pages
        uses: actions/configure-pages@v6

      - name: Install Hugo
        run: |
          curl -sfL --output-dir "${{ runner.temp }}" \
            -O "https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_${HUGO_VERSION}_linux-amd64.tar.gz"
          mkdir -p "${HOME}/.local/hugo"
          tar -C "${HOME}/.local/hugo" \
            -xf "${{ runner.temp }}/hugo_${HUGO_VERSION}_linux-amd64.tar.gz"
          echo "${HOME}/.local/hugo" >> "${GITHUB_PATH}"

      - name: Build Hugo
        run: |
          hugo --source site \
            --gc \
            --minify \
            --baseURL "${{ steps.pages.outputs.base_url }}/"

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v5
        with:
          path: site/public

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}

    steps:
      - name: Deploy Pages
        id: deployment
        uses: actions/deploy-pages@v5
~~~

Projects that use Pagefind should add their Node setup and Pagefind indexing
step after **Build Hugo** and before **Upload Pages artifact**.

The examples above pin BibReview to **v1.7.2**. Keep an exact release tag or
commit pin for reproducibility, and update it deliberately when adopting a newer
BibReview release.

GitHub Pages deployment action majors evolve independently from BibReview.
Recheck the official GitHub Pages and Hugo deployment documentation when you
create or substantially update a deployment workflow.

## 7. Schedule the optional arXiv cache

If the arXiv module is enabled, it can be refreshed automatically because it is
display-only and independent from the curated canonical bibliography. Since
v1.6.42, a successful refresh whose paper payload is unchanged preserves the
existing cache bytes and `generated_at`, so timestamp-only scheduled churn does
not create a commit.

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
        run: python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.7.2"

      - name: Refresh arXiv cache
        run: bibreview --config bibreview.yml arxiv

      - name: Commit cache if changed
        run: |
          if [ -z "$(git status --porcelain -- site/assets/data/arxiv.json)" ]; then
            echo "No arXiv cache changes."
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add site/assets/data/arxiv.json
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

PHRAISE is the public BibReview-powered **Jekyll** integration reference.
of.FSI.der is the **Hugo** integration demonstrator used to validate the Hugo
renderer and GitHub Pages workflow. Project-specific content and presentation
policy remain outside the BibReview engine.
