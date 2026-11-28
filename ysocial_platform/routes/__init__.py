"""The views of the forum platform.

Each module registers its views on a blueprint of the core (``y_web.sdk.routes.blueprint``), so the endpoints
keep the names they always had; ``api_reddit`` is a blueprint of its own that the provider registers.
Importing the package is what attaches them.
"""

from . import (  # noqa: F401
    api_reddit,
    clients_forum,
    clients_forum_details,
    clients_forum_opinion,
    experiments_forum,
    feeds_forum,
    interactions_forum,
    social_forum,
)
