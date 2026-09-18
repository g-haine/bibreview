# GoatCounter analytics (optional, recommended)

BibReview does not require analytics. For a public scholarly bibliography,
however, lightweight traffic statistics can help identify useful pages without
adding a large tracking stack.

[GoatCounter](https://www.goatcounter.com/) is a good optional fit for a static
BibReview/Jekyll site. Its hosted JavaScript integration is small, works on
GitHub Pages, and GoatCounter documents that it does not use cookies,
localStorage or persistent tracker IDs for visitor tracking.

BibReview therefore **recommends considering GoatCounter**, but does not make it
a dependency and does not inject analytics automatically. Analytics belongs to
the site's presentation and privacy policy.

## Basic Jekyll integration

Create a GoatCounter site, then add an endpoint to the Jekyll _config.yml:

~~~yaml
goatcounter:
  endpoint: "https://MYCODE.goatcounter.com/count"
~~~

In a shared layout or footer:

~~~liquid
{% if site.goatcounter.endpoint %}
<script
  data-goatcounter="{{ site.goatcounter.endpoint }}"
  async
  src="https://gc.zgo.at/count.js"></script>
{% endif %}
~~~

Official integration documentation:
<https://www.goatcounter.com/help/js>.

By default the GoatCounter script uses a canonical link when present, otherwise
the current path and query string. Most Jekyll sites therefore do not need a
custom path callback.

## Local development

GoatCounter does not count local addresses by default. This is desirable for
normal development because local previews do not pollute production statistics.

If you deliberately want to test local counting, GoatCounter supports the
**allow_local** setting. Do not leave it enabled accidentally if you do not want
local traffic recorded.

## Content Security Policy

If the site defines a CSP, the standard hosted integration needs the GoatCounter
script host and the site's counting endpoint in the corresponding directives.

See <https://www.goatcounter.com/help/csp>.

## Privacy notice

GoatCounter's current privacy documentation states that it does not store IP
addresses, full User-Agent strings, tracker IDs, cookies or browser storage for
visitor tracking:

<https://www.goatcounter.com/help/privacy>

A site's legal and privacy obligations still depend on jurisdiction, deployment
and configuration. Document the analytics choice on the site's privacy or about
page; do not treat this guide as legal advice.

## Showcase

PHRAISE is one public example of a BibReview-powered Jekyll site using
GoatCounter. This mention is illustrative only; GoatCounter configuration is
not part of BibReview's bibliographic core.
