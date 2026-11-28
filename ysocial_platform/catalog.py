"""The recommenders only the forum offers (Reddit-style rankings), added to the catalogue the selectors list.

YSocial adds the ones its tables do not have yet, by name (see ``recsys_catalog`` of the provider).
"""

RECSYS_CATALOG = (
    {
        "kind": "content",
        "name": "HotRanking",
        "label": "(HOT) Hot Ranking",
        "category": "Reddit-style Ranking",
        "enabled": "Forum",
    },
    {
        "kind": "content",
        "name": "TopRanking",
        "label": "(TOP) Top Ranking",
        "category": "Reddit-style Ranking",
        "enabled": "Forum",
    },
    {
        "kind": "content",
        "name": "MostCommented",
        "label": "(MC) Most Commented",
        "category": "Reddit-style Ranking",
        "enabled": "Forum",
    },
)
