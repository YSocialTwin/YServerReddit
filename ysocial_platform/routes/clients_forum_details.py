"""Details of a forum client (Standard runtime) and the updates its page posts."""

import os

from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from y_web.sdk import gettext as _
from y_web.sdk import routes
from y_web.sdk.models import Client, Content_Recsys, Exps, Follow_Recsys, Population, db
from y_web.sdk.web import (
    build_hourly_activity_chart_series as _build_hourly_activity_chart_series,
)
from y_web.sdk.web import check_privileges
from y_web.sdk.web import (
    client_config_path_for_platform as _client_config_path_for_platform,
)
from y_web.sdk.web import (
    extract_llm_names_from_population_payload as _extract_llm_names_from_population_payload,
)
from y_web.sdk.web import get_client_population_pages as _get_client_population_pages
from y_web.sdk.web import get_experiment_folder_name as _get_experiment_folder_name
from y_web.sdk.web import get_llm_models, llm_backend_status
from y_web.sdk.web import read_json_if_exists as _read_json_if_exists
from y_web.sdk.web import redirect_to_details_page as _redirect_to_details_page
from y_web.sdk.web import update_client_llm_internal as _update_client_llm_internal
from y_web.sdk.web import (
    update_client_simulation_internal as _update_client_simulation_internal,
)
from y_web.sdk.web import update_recsys_internal as _update_recsys_internal

from ._helpers import _forum_effective_link_share

clientsr = routes.blueprint("clients")


@clientsr.route("/admin/client_details_forum/<int:uid>")
@login_required
def client_details_forum(uid):
    """Handle forum client details operation."""
    check_privileges(current_user.username)

    client = db.session.scalars(select(Client).filter_by(id=uid)).first()
    if not client:
        flash(_("Client not found."), "error")
        return redirect(url_for("experiments.settings"))

    experiment = db.session.scalars(select(Exps).filter_by(idexp=client.id_exp)).first()
    if not experiment:
        flash(_("Experiment not found."), "error")
        return redirect(url_for("experiments.settings"))

    misplaced = _redirect_to_details_page(
        experiment, "clientsr.client_details_forum", uid
    )
    if misplaced is not None:
        return misplaced

    population = db.session.scalars(
        select(Population).filter_by(id=client.population_id)
    ).first()
    pages = _get_client_population_pages(client)

    from y_web.sdk.runtime import get_writable_path

    base_dir = get_writable_path()
    exp_folder = _get_experiment_folder_name(experiment)
    exp_dir = os.path.join(
        base_dir,
        "y_web",
        "experiments",
        exp_folder,
    )
    config_path = _client_config_path_for_platform(
        exp_dir, experiment, client.name, population.name
    )
    population_path = os.path.join(
        exp_dir,
        f"{population.name}.json",
    )

    config = _read_json_if_exists(config_path)
    agents = _read_json_if_exists(population_path)
    llms = _extract_llm_names_from_population_payload(agents)

    activity = ((config or {}).get("simulation") or {}).get("hourly_activity") or {
        str(hour): 0 for hour in range(24)
    }
    idx, data = _build_hourly_activity_chart_series(activity)

    models = get_llm_models()
    llm_backend = llm_backend_status()

    frecsys_all = db.session.scalars(select(Follow_Recsys)).all()
    crecsys_all = db.session.scalars(select(Content_Recsys)).all()
    frecsys = [r for r in frecsys_all if r.enabled and "Standard" in r.enabled]
    # forum clients also get the recommenders reserved to forums (Hot, Top, Most commented)
    crecsys = [
        r
        for r in crecsys_all
        if r.enabled and ("Standard" in r.enabled or "Forum" in r.enabled)
    ]

    forum_action_weights = {
        "post": client.post,
        "comment": client.comment,
        "read": client.read,
        "search": client.search,
        "share_link": _forum_effective_link_share(client.news, client.share_link),
        "share_image": ((config or {}).get("simulation") or {})
        .get("actions_likelihood", {})
        .get("share_image", 0.0),
    }

    return render_template(
        "@ysocial_server_reddit/admin/client_details_forum.html",
        data=data,
        idx=idx,
        activity=activity,
        client=client,
        experiment=experiment,
        population=population,
        pages=pages,
        models=models,
        llm_backend=llm_backend,
        frecsys=frecsys,
        crecsys=crecsys,
        llms=llms,
        forum_action_weights=forum_action_weights,
    )


@clientsr.route("/admin/update_forum_recsys/<int:uid>", methods=["POST"])
@login_required
def update_forum_recsys(uid):
    """Update recommenders for a forum client."""
    return _update_recsys_internal(uid, "forum")


@clientsr.route("/admin/update_forum_client_llm/<int:uid>", methods=["POST"])
@login_required
def update_forum_client_llm(uid):
    """Update LLM for a forum client."""
    return _update_client_llm_internal(uid, "forum")


@clientsr.route("/admin/update_forum_client_settings/<int:uid>", methods=["POST"])
@login_required
def update_forum_client_settings(uid):
    return _update_client_simulation_internal(uid, "forum")
