"""Static-site transformation and pure rendering primitives for BibReview."""

from .render import (
    JekyllIndexRenderOptions,
    RenderedArtifact,
    SiteRenderError,
    render_jekyll_index_pages,
)
from .transform import (
    SiteAuthor,
    SiteAuthorPage,
    SiteModel,
    SitePublication,
    SitePublicationAuthor,
    SiteReference,
    SiteTransformError,
    SiteYearPage,
    build_site_model,
    site_model_data,
)

__all__ = [
    "JekyllIndexRenderOptions",
    "RenderedArtifact",
    "SiteAuthor",
    "SiteAuthorPage",
    "SiteModel",
    "SitePublication",
    "SitePublicationAuthor",
    "SiteReference",
    "SiteRenderError",
    "SiteTransformError",
    "SiteYearPage",
    "build_site_model",
    "render_jekyll_index_pages",
    "site_model_data",
]
