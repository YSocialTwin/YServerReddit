"""The forum platform on the Flask runtime ("Standard", YServerReddit/YClientReddit): what YSocial needs to run, show
and manage a Standard forum experiment.

Another repository may serve the same platform key on another runtime: the two share nothing but the key (each has
its own provider, library, pages and templates). The provider is declared in ``meta/registry.json`` (``platform`` section) and loaded by YSocial at
startup. It talks to the core through ``y_web.sdk`` only; the pages of the platform (``routes/``, ``templates/``,
``static/``) and the forum library (``forum/``) are part of this repository.
"""

from __future__ import annotations

import importlib
from typing import Any, Mapping, Optional, Tuple

from y_web.sdk import (
    COMMON_FRONTEND_CAPABILITIES,
    N_,
    STANDARD,
    CreationRules,
    FederationTraits,
    PlatformProvider,
    StandardRuntime,
)
from y_web.sdk import gettext as _
from y_web.sdk import services, web


class ForumStandard(PlatformProvider):
    key = "forum"
    id = "ysocial_server_reddit"
    simulator_types = (STANDARD,)
    label = N_("Forum")
    icon = "message-square"
    order = 20

    def agent_resource_cards(self) -> list:
        from sqlalchemy import func, select

        from y_web import db
        from y_web.src.models import ForumImageFeedResource

        count = db.session.scalar(
            select(func.count()).select_from(ForumImageFeedResource)
        )
        return [
            {
                "title": _("Forum Image Feeds"),
                "subtitle": _("Built-in resource"),
                "icon": "mdi-image-multiple-outline",
                "tone": "is-info",
                "description": _(
                    "Register reusable subreddit-based image feeds for forum experiments, including interest tags used during image ingestion."
                ),
                "highlights": [
                    _("%(count)s reusable image feeds available", count=count),
                    _("Subreddit validation is done before assignment"),
                    _("The same subreddit definition can be reused safely"),
                ],
                "cta_label": _("Manage Image Resources"),
                "cta_href": "/admin/forum_image_resources",
                "secondary_label": _(
                    "Maintain the canonical image-feed catalog here and assign only the feeds needed by each forum simulation."
                ),
                "meta": {},
                "deployment_tags": [self.key],
                "is_plugin": False,
                "image_feed_count": count,
            }
        ]

    @property
    def human_gateway(self):
        """How a person of a World Simulation gets an account here and acts (``gateway.py``)."""
        return importlib.import_module(f"{__package__}.gateway").HUMAN_GATEWAY

    @property
    def human_recsys(self):
        """The recommender systems of the people who use the frontend (``recsys/``)."""
        return importlib.import_module(f"{__package__}.recsys").RECSYS

    server_label = "YServerReddit"
    feeds_read_at_client_start = True
    runtime_keys = ("forum_server", "forum_client")
    runtime_pair_description = N_(
        "Legacy forum simulator on Flask (threads and replies). Server and client are installed and updated independently from its page."
    )
    embedding_in_details = True
    avatar_settings_page = True
    plugin_agents = True
    stress_reward_page = True
    stress_reward_in_form = True
    legacy_shared_client_log = True
    recsys_includes_standard = True
    tag_runtime_sets = (("forum_server", "forum_client"),)
    federation_traits = FederationTraits(
        mdi_icon="mdi-forum-outline",
        local_kind="post",
        author_sql="SELECT p.id, u.username, p.user_id FROM post p LEFT JOIN user_mgmt u ON u.id = p.user_id WHERE p.id IN ({q})",
        content_path="/{exp}/rthread/{id}",
        profile_path="/{exp}/profile/{account}/recent/1",
        account_sql="SELECT id, username FROM user_mgmt WHERE id = ?",
        text_limit=2000,
        title_line=True,
        decision_triggers=("publish", "interaction"),
        simulators=(STANDARD,),
        topic_migration_key="topic_migration",
    )
    library_package = f"{__package__}.forum"
    configures_memory = True

    def entry_url(self, exp_id: int, user_id: int) -> Optional[str]:
        return f"/{exp_id}/rfeed/{user_id}/feed/rf/1"

    slug_username_fallback = "forum_user"
    threaded_posts = True

    def feed_home_url(self, exp_id: int) -> Optional[str]:
        return f"/{exp_id}/rfeed/all/feed/rf/1?feed_type=new"

    has_interview = True

    def interview_redirect(self, exp_id: int) -> str:
        pages = importlib.import_module(f"{__package__}.routes.social_forum")
        return f"/{exp_id}/rfeed/all/feed/rf/1?feed_type={pages._forum_default_feed_type(exp_id)}"

    def render_interview(self, exp_id: int, context: dict) -> Any:
        from flask import render_template

        pages = importlib.import_module(f"{__package__}.routes.social_forum")
        return render_template(
            "@ysocial_server_reddit/forum/interview.html",
            profile_pic=pages._forum_current_profile_pic(
                exp_id, pages._forum_logged_user()
            ),
            forum_memory_enabled=True,
            **context,
        )

    def client_mode(self, simulator_type: str) -> str:
        return "forum"

    def clients_page(self, simulator_type: str) -> str:
        return "clientsr.clients_forum"

    def client_details_page(self, simulator_type: str) -> str:
        return "clientsr.client_details_forum"

    generic_hpc_client_creation = False
    client_file_has_simulation_days = False
    prompt_pack = ("prompts_forum.json", "prompts.json")
    server_modules = ("news", "voting", "image", "image_post")
    details_template = "@ysocial_server_reddit/admin/experiment_details_forum.html"
    inherits_client_llm_defaults = True
    frontend_capabilities = COMMON_FRONTEND_CAPABILITIES | {
        "interaction_dislike",  # downvote
        "notifications_menu",
        "annotations_enabled",
        "annotation_emotions",
        "annotation_topics",
        "default_forum_feed_type",
    }
    frontend_exclusive_settings = frozenset(
        {"default_forum_feed_type"}
    )  # feed ordering
    details_context_keys = ("forum_feed_health",)

    def details_context(self, experiment: Any) -> dict:
        try:
            exp_folder = services.experiment_folder(experiment)
            return {"forum_feed_health": web.read_feed_health(experiment, exp_folder)}
        except Exception:
            return {"forum_feed_health": None}

    standard_runtime = StandardRuntime(
        server_repo="YServerReddit",
        client_repo="YClientReddit",
        fake_agent_module="y_client.classes.fake_base_agent",
        client_takes_log_file=True,
    )

    def experiment_prompt_pack(self, simulator_type: str) -> Tuple[str, str]:
        return self.prompt_pack

    def prompts_page(self, simulator_type: str) -> str:
        return "experiments.prompts_forum"

    def creation_rules(self, simulator_type: str) -> CreationRules:
        return CreationRules(
            forced={
                "opinion_dynamics_enabled": False,
                "sentiment_annotation": False,
                "emotion_annotation": False,
            },
            database="server",
            copies_clean_database=simulator_type == STANDARD,
        )

    def resolve_simulator(self, simulator_type: str, available: Mapping[str, bool]):
        if not available["forum"]:
            return STANDARD, _(
                "Forum experiments are unavailable because YServerReddit and YClientReddit are not both present."
            )
        return STANDARD, None

    def attach_routes(self):
        importlib.import_module(f"{__package__}.routes")
