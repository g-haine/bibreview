"""Static-site transformation, rendering, and persistence primitives."""

from .persist import (
    SitePersistenceError,
    SitePersistencePlan,
    apply_rendered_artifacts,
    plan_rendered_artifacts,
)
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
    SitePublicationEditor,
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
    "SitePersistenceError",
    "SitePersistencePlan",
    "SitePublication",
    "SitePublicationAuthor",
    "SitePublicationEditor",
    "SiteReference",
    "SiteRenderError",
    "SiteTransformError",
    "SiteYearPage",
    "apply_rendered_artifacts",
    "build_site_model",
    "plan_rendered_artifacts",
    "render_jekyll_index_pages",
    "render_jekyll_publication_posts",
    "site_model_data",
]
