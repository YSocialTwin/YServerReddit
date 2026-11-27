"""
Forum avatar mode, plus the old addresses of the feed pages.

The reusable RSS/image libraries, the image-feed assignment and the feed limits are pages of the core now (any
platform's experiments use them). The forum keeps the avatar-mode form, and redirects the addresses it used to
serve so that links and bookmarks written for an earlier version still work.
"""

import json
import os

from flask import flash, redirect, request, url_for
from flask_login import login_required

from y_web.sdk import gettext as _
from y_web.sdk import routes
from y_web.sdk.web import load_feed_experiment_context as _load_feed_experiment_context

from ..forum.avatars import (  # noqa: E402
    normalize_force_placeholder,
    normalize_forum_avatar_mode,
)

experiments = routes.blueprint("experiments")


def _redirect_to(new_path):
    # 308 keeps the method and the form of a POST, 301 would turn it into a GET
    return redirect(new_path, code=308)


@experiments.route("/admin/forum_rss_resources")
@login_required
def forum_rss_resources():
    return _redirect_to("/admin/rss_resources")


@experiments.route("/admin/forum_image_resources")
@login_required
def forum_image_resources():
    return _redirect_to("/admin/image_resources")


@experiments.route("/admin/create_forum_rss_resource", methods=["POST"])
@login_required
def create_forum_rss_resource():
    return _redirect_to("/admin/create_rss_resource")


@experiments.route("/admin/create_forum_image_resource", methods=["POST"])
@login_required
def create_forum_image_resource():
    return _redirect_to("/admin/create_image_resource")


@experiments.route(
    "/admin/delete_forum_rss_resource/<int:resource_id>", methods=["POST"]
)
@login_required
def delete_forum_rss_resource(resource_id):
    return _redirect_to(f"/admin/delete_rss_resource/{resource_id}")


@experiments.route(
    "/admin/delete_forum_image_resource/<int:resource_id>", methods=["POST"]
)
@login_required
def delete_forum_image_resource(resource_id):
    return _redirect_to(f"/admin/delete_image_resource/{resource_id}")


@experiments.route("/admin/update_forum_avatar_mode/<int:uid>", methods=["POST"])
@login_required
def update_forum_avatar_mode(uid):
    """Persist forum avatar mode to config_server.json."""
    experiment, experiment_dir, error_response = _load_feed_experiment_context(uid)
    if error_response is not None:
        return error_response

    config_path = os.path.join(experiment_dir, "config_server.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as handle:
                config = json.load(handle)
        except (json.JSONDecodeError, IOError):
            config = {}

    enabled_flag = (
        str(request.form.get("use_actual_profile_pics") or "").strip().lower()
    )
    requested_mode = request.form.get("avatar_mode")
    if requested_mode is not None or enabled_flag:
        mode = normalize_forum_avatar_mode(requested_mode)
        if enabled_flag in {"1", "true", "on", "yes"}:
            mode = "actual"
        config["avatar_mode"] = mode
    if request.form.get(
        "force_placeholder_present"
    ):  # the switch is part of this submission
        config["force_placeholder_avatars"] = normalize_force_placeholder(
            request.form.get("force_placeholder_avatars")
        )

    with open(config_path, "w") as handle:
        json.dump(config, handle, indent=2)

    flash(_("Forum avatar mode updated successfully."), "success")
    return redirect(
        request.referrer or url_for("experiments.experiment_details", uid=uid)
    )
