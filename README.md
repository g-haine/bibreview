# BibReview

BibReview is a generic bibliographic engine intended to support reproducible, human-reviewed literature databases and static scholarly websites.

This bootstrap implements the first M3 extraction slice:

- versioned `bibreview.yml` loading and validation;
- path resolution independent of any PHRAISE directory layout;
- a publication model whose internal identity is independent of DOI;
- DOI normalization and deterministic migration identifiers;
- conservative strong-identifier matching;
- reusable text normalization inherited from the PHRAISE maintenance code;
- a minimal `validate` / `status` CLI foundation.

PHRAISE remains the integration and non-regression reference during extraction.
