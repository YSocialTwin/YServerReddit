"""Forum prompts page of an experiment."""

import json
import os

from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from y_web.sdk import gettext as _
from y_web.sdk import routes
from y_web.sdk.models import Exps, db
from y_web.sdk.web import check_privileges
from y_web.sdk.web import hpc_prompts_filename as _hpc_prompts_filename

experiments = routes.blueprint("experiments")


@experiments.route("/admin/prompts_forum/<int:uid>")
@login_required
def prompts_forum(uid):
    """Handle forum prompts operation."""
    check_privileges(current_user.username)

    from y_web.sdk.runtime import get_writable_path

    BASE_DIR = get_writable_path()

    experiment = db.session.scalars(select(Exps).filter_by(idexp=uid)).first()

    if not experiment:
        flash(_("Experiment not found."), "error")
        return redirect(url_for("admin.dashboard"))

    if experiment.simulator_type == "HPC":
        return redirect(url_for("experiments.prompts_hpc", uid=uid))
    if experiment.platform_type != "forum":
        return redirect(url_for("experiments.prompts", uid=uid))

    prompts_path = os.path.join(
        BASE_DIR,
        f"y_web{os.sep}experiments{os.sep}{experiment.db_name.split(os.sep)[1]}{os.sep}{_hpc_prompts_filename(experiment)}",
    )

    with open(prompts_path) as f:
        prompts = json.load(f)

    return render_template(
        "@ysocial_server_reddit/admin/prompts_forum.html",
        experiment=experiment,
        prompts=prompts,
    )
