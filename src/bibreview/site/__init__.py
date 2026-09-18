"""Static-site transformation and pure rendering primitives for BibReview."""

from .render import (
    JekyllIndexRenderOptions,
    JekyllPublicationRenderOptions,
    RenderedArtifact,
    SiteRenderError,
    render_jekyll_index_pages,
    render_jekyll_publication_posts,
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
    "JekyllPublicationRenderOptions",
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
    "render_jekyll_publication_posts",
    "site_model_data",
]
