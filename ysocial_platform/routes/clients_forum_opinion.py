"""Initial opinions of a forum client (Standard runtime)."""

from flask import redirect, url_for
from flask_login import login_required
from y_web.sdk import routes
from y_web.sdk.web import (
    apply_opinion_distributions_to_population as _apply_opinion_distributions_to_population,
)
from y_web.sdk.web import (
    invalidate_opinion_evolution_cache_for_experiment as _invalidate_opinion_evolution_cache_for_experiment,
)
from y_web.sdk.web import (
    opinion_configuration_internal as _opinion_configuration_internal,
)
from y_web.sdk.web import (
    persist_non_hpc_opinion_dynamics as _persist_non_hpc_opinion_dynamics,
)
from y_web.sdk.web import (
    resolve_opinion_submission_context as _resolve_opinion_submission_context,
)
from y_web.sdk.web import (
    sync_initial_standard_opinions_to_experiment_db as _sync_initial_standard_opinions_to_experiment_db,
)

clientsr = routes.blueprint("clients")


@clientsr.route("/admin/opinion_configuration_forum/<int:idexp>")
@login_required
def opinion_configuration_forum(idexp):
    """Display opinion configuration for a forum experiment."""
    return _opinion_configuration_internal(idexp, "forum")


def _set_forum_opinion_distributions_internal():
    """Persist opinion distributions for forum experiments."""
    context, error_response = _resolve_opinion_submission_context("forum")
    if error_response is not None:
        return error_response
    save_error = _apply_opinion_distributions_to_population(context)
    if save_error is not None:
        return save_error
    _persist_non_hpc_opinion_dynamics(context)
    _sync_initial_standard_opinions_to_experiment_db(context)
    _invalidate_opinion_evolution_cache_for_experiment(int(context["idexp"]))
    return redirect(url_for("experiments.experiment_details", uid=context["idexp"]))


@clientsr.route("/admin/set_forum_opinion_distributions", methods=["POST"])
@login_required
def set_forum_opinion_distributions():
    """Persist opinion distributions for a forum experiment."""
    return _set_forum_opinion_distributions_internal()
