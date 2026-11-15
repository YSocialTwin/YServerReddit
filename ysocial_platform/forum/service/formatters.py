from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from y_web.sdk.models import db
from .data_classes import ArticlePreview
from y_web.sdk.text_utils import strip_tags
from y_web.sdk.experiment_context import get_current_experiment_id
from y_web.sdk.commons.display_time import _get_experiment_dir
from y_web.sdk.models import (
    Agent,
    Articles,
    Exps,
    Images,
    Page,
    Post,
    Rounds,
    User_mgmt,
    Websites,
)


# In-memory cache for OG image lookups to avoid repeated network requests
# Maps article_id -> Optional[Dict] (None means "already tried, no image found")
_og_image_cache: Dict[int, Optional[Dict[str, str]]] = {}
_author_agent_page_cache: Dict[str, bool] = {}


def clean_reddit_formatting(text: str) -> str:
    """Remove the formatting artifacts of subreddit feed summaries from text."""
    if not text:
        return ""
    normalized = html.unescape(text)
    # Remove "submitted by /u/username" patterns (tolerate extra spaces)
    normalized = re.sub(
        r"\bsubmitted\s+by\s+/?u/[A-Za-z0-9_-]+\b", "", normalized, flags=re.IGNORECASE
    )
    # Remove [link] [comments] patterns
    normalized = re.sub(r"\[link\]|\[comments\]", "", normalized, flags=re.IGNORECASE)
    # Clean up extra whitespace
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def extract_reddit_summary_image(summary_html: str) -> Optional[Dict[str, str]]:
    """Extract a preview image from a subreddit feed summary (no network)."""
    if not summary_html:
        return None
    try:
        soup = BeautifulSoup(summary_html, "html.parser")
        img = soup.find("img")
        if not img:
            return None
        src = img.get("src", "")
        if not src or src.startswith("data:"):
            return None
        src = html.unescape(src)
        src = _upgrade_reddit_image_url(src)
        if not src:
            return None
        alt = img.get("alt", "") or ""
        return {"url": src, "description": alt}
    except Exception:
        return None


def extract_youtube_thumbnail(url: str) -> Optional[str]:
    """Extract YouTube thumbnail URL from a YouTube link."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        video_id = None

        if "youtu.be" in host:
            video_id = path.strip("/").split("/")[0] if path else None
        elif "youtube.com" in host or "youtube-nocookie.com" in host:
            if path.startswith("/watch"):
                qs = parse_qs(parsed.query)
                video_id = (qs.get("v") or [None])[0]
            elif path.startswith("/embed/"):
                video_id = path.split("/embed/")[1].split("/")[0]
            elif path.startswith("/shorts/"):
                video_id = path.split("/shorts/")[1].split("/")[0]
            elif path.startswith("/live/"):
                video_id = path.split("/live/")[1].split("/")[0]
            elif path.startswith("/v/"):
                video_id = path.split("/v/")[1].split("/")[0]

        if not video_id:
            return None
        return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    except Exception:
        return None


def _fetch_and_cache_og_image(article) -> Optional[Dict[str, str]]:
    """Fetch OG image from article URL, cache in DB and memory."""
    if article.id in _og_image_cache:
        return _og_image_cache[article.id]

    image = None
    try:
        import requests

        from y_web.sdk.article_extractor import extract_image

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(article.link, headers=headers, timeout=5)
        if resp.ok:
            soup = BeautifulSoup(resp.text, "html.parser")
            og_img = extract_image(soup, article.link)
            if og_img:
                image = {"url": og_img, "description": ""}
                # Cache in DB for future lookups (persists across restarts)
                try:
                    existing = db.session.scalars(
                        select(Images).filter_by(article_id=article.id)
                    ).first()
                    if not existing:
                        img_record = Images(url=og_img, article_id=article.id)
                        db.session.add(img_record)
                        db.session.commit()
                except Exception:
                    db.session.rollback()
    except Exception:
        pass

    _og_image_cache[article.id] = image
    return image


def _article_summary_needs_enrichment(summary: Optional[str]) -> bool:
    """
    Heuristic for deciding whether a link's summary should be upgraded by an LLM.

    We store a fast metadata-derived summary on share; if it is empty or looks like
    a generic placeholder, we can request an on-demand LLM summary.
    """
    s = (summary or "").strip()
    if not s:
        return True
    lowered = s.lower()
    if lowered == "user shared article":
        return True
    if lowered.startswith("shared link:"):
        return True
    # Very short summaries are usually meta tag stubs or placeholders.
    return len(s) < 60


def _resolve_article(article: Optional[Articles]) -> Optional[ArticlePreview]:
    if article is None:
        return None
    website = db.session.scalars(
        select(Websites).filter_by(id=article.website_id)
    ).first()
    source = website.name if website else ""
    subreddit = ""
    if website:
        rss_value = (getattr(website, "rss", "") or "").strip().lower()
        match = re.search(r"(?:^|/)r/([a-z0-9_]+)", rss_value)
        if match:
            subreddit = match.group(1)
    if not subreddit and article.link:
        link_value = (article.link or "").strip().lower()
        match = re.search(r"(?:^|/)r/([a-z0-9_]+)", link_value)
        if match:
            subreddit = match.group(1)

    # Fetch image associated with this article
    image = None
    try:
        img = db.session.scalars(
            select(Images).filter_by(article_id=article.id)
        ).first()
        if img and img.url:
            image = {
                "url": img.url,
                "description": getattr(img, "description", "") or "",
            }
    except OperationalError:
        # Handle schema mismatch for older databases
        try:
            row = db.session.execute(
                text(
                    "SELECT url, description FROM images WHERE article_id = :article_id LIMIT 1"
                ),
                {"article_id": article.id},
            ).fetchone()
            if row and row[0]:
                image = {"url": row[0], "description": row[1] or ""}
        except Exception:
            pass

    summary = strip_tags(article.summary) if article.summary else ""
    summary = clean_reddit_formatting(summary)

    if not image and article.summary:
        image = extract_reddit_summary_image(article.summary)

    if not image:
        yt_thumb = extract_youtube_thumbnail(article.link)
        if yt_thumb:
            image = {"url": yt_thumb, "description": ""}

    # Fallback: fetch OG image from the article URL and cache it in the DB
    if not image and article.link:
        image = _fetch_and_cache_og_image(article)

    return ArticlePreview(
        title=strip_tags(article.title) if article.title else "",
        summary=summary,
        url=article.link,
        source=source,
        subreddit=subreddit,
        image=image,
    )


def _resolve_image(image_id: Optional[int]) -> Optional[str]:
    if not image_id:
        return ""
    try:
        image = db.session.scalars(select(Images).filter_by(id=image_id)).first()
    except OperationalError as exc:
        message = str(exc).lower()
        if "no such column" in message and "remote_article_id" in message:
            row = db.session.execute(
                text("SELECT url FROM images WHERE id = :image_id LIMIT 1"),
                {"image_id": image_id},
            ).fetchone()
            if row and row[0]:
                return {
                    "url": row[0],
                    "description": "",
                    "media_type": _media_type_from_url(row[0]),
                }
            return ""
        raise
    if not image:
        return ""
    url = getattr(image, "url", None)
    description = getattr(image, "description", "") or ""
    if not url:
        return ""
    return {
        "url": url,
        "description": description,
        "media_type": _media_type_from_url(url),
    }


def _media_type_from_url(url: str) -> str:
    if not url:
        return "image"
    lowered = url.split("?", 1)[0].split("#", 1)[0].lower()
    if (
        lowered.endswith(".mp4")
        or lowered.endswith(".webm")
        or lowered.endswith(".ogg")
        or lowered.endswith(".gifv")
    ):
        return "video"
    return "image"


def _upgrade_reddit_image_url(url: str) -> str:
    """Normalize subreddit image URLs without rewriting them into broken preview variants."""
    if not url:
        return url

    from y_web.sdk.feeds import unwrap_media_url

    return unwrap_media_url(url)


def _normalize_forum_local_image_path(local_path: str) -> str:
    value = str(local_path or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "/static/", "/uploads/")):
        return value
    if value.startswith("uploads/"):
        # Legacy feed bootstrap stores files under y_web/static/uploads/...
        return f"/static/{value}"
    return value


def _resolve_image_post(image_post_id: Optional[int]) -> Optional[Dict[str, str]]:
    """Resolve image from image_posts table (standalone images shared by agents)."""
    if not image_post_id:
        return None
    try:
        # Use the current experiment's database bind
        from y_web.sdk.experiment_context import get_current_experiment_bind

        bind_key = get_current_experiment_bind()
        engine = db.get_engine(bind=bind_key)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT url, description, local_path, high_res_url, subreddit "
                    "FROM image_posts WHERE id = :id LIMIT 1"
                ),
                {"id": image_post_id},
            ).fetchone()
            if row and row[0]:
                local_path = _normalize_forum_local_image_path(row[2] or "")
                high_res_url = str(row[3] or "").strip()
                base_url = str(row[0] or "").strip()
                # Historical working behavior for feed images used locally
                # downloaded files first. Fall back to high_res_url, then remote URL.
                source_url = local_path or high_res_url or base_url
                url = _upgrade_reddit_image_url(source_url)
                return {
                    "url": url,
                    "description": row[1] or "",
                    "media_type": _media_type_from_url(url),
                    "subreddit": row[4] or "",
                }
    except Exception:
        pass
    return None


def _shared_from(post: Post):
    """Origin of a cross-post as ``(post_id, author, community_slug)``; ``-1`` when the post is not a cross-post."""
    if post.shared_from == -1 or post.shared_from is None:
        return -1
    author = (
        db.session.query(User_mgmt.username)
        .join(Post, User_mgmt.id == Post.user_id)
        .filter(Post.id == post.shared_from)
        .scalar()
    )
    if not author:
        return -1
    community = ""
    try:
        from y_web.sdk.models import Interests, Post_topics

        community = (
            db.session.query(Interests.interest)
            .join(Post_topics, Post_topics.topic_id == Interests.iid)
            .filter(Post_topics.post_id == post.shared_from)
            .scalar()
            or ""
        )
    except Exception:  # noqa: BLE001 - the origin line degrades to the author only
        community = ""
    return (post.shared_from, author, community)


def _get_profile_pic(user: User_mgmt) -> str:
    from ..avatars import resolve_forum_profile_pic

    return resolve_forum_profile_pic(user, get_current_experiment_id())


def _is_agent_or_page_author(user: Optional[User_mgmt]) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_page", False)):
        return True

    username = (getattr(user, "username", "") or "").strip()
    if not username:
        return False
    if username in _author_agent_page_cache:
        return _author_agent_page_cache[username]

    is_agent = (
        db.session.scalars(select(Agent).filter_by(name=username)).first() is not None
    )
    is_page = (
        db.session.scalars(select(Page).filter_by(name=username)).first() is not None
    )
    result = bool(is_agent or is_page)
    _author_agent_page_cache[username] = result
    return result

