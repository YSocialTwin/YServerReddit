"""Helpers shared by the forum views."""


def _forum_effective_link_share(news, share_link):
    """Forum uses link-sharing for feed-backed articles; keep legacy news weights compatible."""
    try:
        news_value = float(news or 0)
    except (TypeError, ValueError):
        news_value = 0.0
    try:
        share_link_value = float(share_link or 0)
    except (TypeError, ValueError):
        share_link_value = 0.0
    return max(news_value, share_link_value)
