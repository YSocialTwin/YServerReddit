"""The forum's own profile pages.

The shared profile routes of YSocial ask the provider for them (``render_profile``, ``render_edit_profile``): the
forum shows the profile with its communities and its notifications, and a person can only edit their own profile.
"""

from __future__ import annotations

from flask import redirect, render_template, url_for
from flask_login import current_user

from y_web.sdk import social, template_name
from y_web.sdk.commons import service


def render_profile(provider, exp, context):
    user = context["user"]
    logged_id = context["logged_id"]
    exp_id = exp.idexp
    logged = social.logged_user()
    mentions = social.get_unanswered_mentions(logged.id) if logged else []
    recsys = provider.human_recsys
    suggest = getattr(recsys, "suggested_users", None)
    suggested_users = (
        suggest(logged.username, pages=False) if logged and suggest else []
    )
    suggested_pages = suggest(logged.username, pages=True) if logged and suggest else []
    forum_context = dict(context["common_context"])
    forum_context["mentions"] = mentions
    forum_context["recent_communities"] = service.fetch_recent_active_communities()
    forum_context["my_communities"] = (
        service.fetch_user_communities(logged.id) if logged else []
    )
    return render_template(
        template_name(provider.provider_id, "forum/profile.html"),
        profile_pic=social.current_profile_pic(exp_id, logged),
        viewed_profile_pic=context["profile_pic"],
        profile_pic_feed=social.current_profile_pic(exp_id, logged),
        profile_delete_inline=True,
        feed_user_id=None,
        timeline="profile",
        sfollow=suggested_users,
        spages=suggested_pages,
        forum_memory_enabled=social.feed_memory_enabled(exp_id),
        can_follow_profile=str(user.id) != str(logged_id),
        feed_type="new",
        **forum_context,
    )


def render_edit_profile(provider, exp, context):
    exp_id = exp.idexp
    user = context["user"]
    logged_id = context["logged_id"]
    # a person may only edit their own profile; no topics or opinions and no recommender choice here
    if str(logged_id) != str(context["user_id"]):
        return redirect(
            url_for(
                "main.profile_logged",
                exp_id=exp_id,
                user_id=logged_id,
                mode="recent",
                page=1,
            )
        )
    return render_template(
        template_name(provider.provider_id, "forum/edit_profile.html"),
        user=user,
        profile_pic=context["profile_pic"],
        available_profile_pics=context["available_profile_pics"],
        cover_image=context["cover_image"],
        available_cover_images=context["available_cover_images"],
        is_page=user.is_page,
        username=user.username,
        user_id=context["user_id"],
        logged_username=current_user.username,
        logged_id=logged_id,
        is_admin=social.is_admin(current_user.username),
        exp_id=exp_id,
        forum_memory_enabled=social.feed_memory_enabled(exp_id),
    )


def page_context(ui):
    annotations = bool(ui.get("annotations_enabled", True))
    return {
        "platform_ui": "forum",
        "forum_show_emotions": annotations
        and bool(ui.get("annotation_emotions", True)),
        "forum_show_topics": annotations and bool(ui.get("annotation_topics", True)),
    }
