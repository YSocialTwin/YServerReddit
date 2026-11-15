"""How the forum shows its threads in the pages the core builds (``provider.threads``): titled, timed posts of a
community."""

from y_web.sdk.text_utils import process_reddit_post

from .service import _format_display_time, _format_display_time_from_created_at, _resolve_article
from .service.queries import _build_root_post_community_map, _primary_community_payload


def display_time(created_at, day, hour):
    """The time of a post as the experiment's clock shows it."""
    return _format_display_time_from_created_at(created_at) or _format_display_time(
        str(day), f"{int(hour):02d}" if str(hour).isdigit() else str(hour)
    )


def article(article):
    return _resolve_article(article)


def community(article_preview, topics):
    return _primary_community_payload(article_preview, topics)


def split_title(text):
    """``(title, body)`` of a post written as a title and a body."""
    return process_reddit_post(text)


def origin_community(source_id) -> str:
    """Slug of the community a cross-posted thread was copied from ('' when unknown)."""
    try:
        entries = _build_root_post_community_map([source_id]).get(source_id) or []
        if entries:
            return str(entries[0].get("slug") or "")
    except Exception:  # noqa: BLE001 - the origin line degrades to the author only
        pass
    return ""
