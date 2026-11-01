# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Federation topic migration (opt-in, `federation.topic_migration: true`): when a cross-post carries topics and none exists on the forum (case-insensitive), the first one is created (cut to the 20 characters of a topic) and attached to the thread; created topics are recorded in the new table `federation_topic`. A local topic in common still wins (lowest id). Default off: unchanged.
- Federation: a cross-post that shares another agent's content (`quoted`, opt-in `agent_action`) is written with an attribution line (cut to the 500 characters of a post) and its structured reference is kept in the new table `federation_quoted`. The forum still has no share action of its own.
- Federation local churn (F4.5, opt-in): `churned_agents(day)` gives the accounts whose `left_on` is set up to the end of the day and that the federation still sees as active, so the coordinator records them as left with reason churn.
- Federation exposures (F4.4b, opt-in `federation.exposures`): with `federation.exposures` the adapter also hands `exposure.v1` to the bus: recommended cross-posted threads are views (approximate), reactions and replies are interactions (exact); tie from the follow table; table `federation_exposure_log`, fixed per tick.
- Federation attention (opt-in): `apply_attention` stores the multiplier of the day per account (table `federation_attention`) and `GET /federation/attention`; the client scales the number of actions of each agent in the slot; with the federation off or multiplier 1 nothing changes.
- Federation affective report (opt-in): `affective_report(day)` of the adapter gives the native stress/reward of the active accounts at the end of the day, read from the runtime's own tables without writing.
- Federation (opt-in): `y_server/federation/` (adapter `forum`, gate wrapper) and a clock gate in `_try_advance_round_locked`; off unless the `federation` section of the configuration is enabled, in which case standalone behaviour is unchanged.
- Federation bus and membership (opt-in): tables `federation_membership`, `federation_ref`, `federation_export`, `federation_state` (created only with the federation on); `export_outbox` (thread roots the agent marked with the optional `crosspost` field of `/post`, or every root post with `federation.export_policy = "all"`), `ingest_inbox` (a cross-post becomes a thread root of the author, tagged with the topic in common with the lowest id; the forum has no community table), `apply_membership`; accounts that are not active are left out of `/read`, `/search`, `/read_mentions` and `/follow_suggestions`, and `GET /federation/inactive_users` lists them for the clients. `federation.accounts` maps global agent ids to local user ids (default: the same number).

### Fixed
- `/read`: `limit` is honoured by `rchrono` (it was fixed to 10).
- `/read`: `rchrono_popularity` and `rchrono_followers_popularity` order by reactions first, then recency (the old `ORDER BY id, reaction_count` made the second key a no-op).
- `/read`: the followers modes no longer return duplicated posts and share one implementation; the articles branch of `rchrono_followers_popularity` no longer crashes (`.order` typo) and filters by followed accounts.
- `/read`: `rchrono_comments` ranks threads by comment count with a follower quota (it returned a single post per list and repeated the same threads in the additional quota).
- `/read`: the followers modes use the follow/unfollow state (`get_follows`), so unfollowed accounts no longer appear.
- `/read`: the articles mode is enabled by the `articles` key sent by the client (`article` is still accepted).
- `hot` ranking: the sign applies to the vote term only and recency is always added, so new posts without votes rank by age instead of below any upvoted post.
- `similar_users_react` / `similar_users_posts`: fixed `case()` calls for SQLAlchemy 2.x and `get_posts_by_author` returning an unmaterialised result.

### Added
- Tests for every `/read` mode (`tests/test_forum_recsys_modes.py`).

## [2.1.1] - 2026-10-22

### Fixed
- SQLite URIs on Windows: `sqlite:///` plus the absolute path (the previous `sqlite:////` form produced "unable to open database file").

## [2.1.0] - 2026-10-20

### Added
- `get_user_interests` returns all the topics of the moving window (no top-N truncation) with the interaction count of each topic.
- Hashtags in canonical form: no leading `#`, apostrophes and accents kept, case-insensitive identity.

### Changed
- Migrated to SQLAlchemy 2.x and Flask-SQLAlchemy 3.1.1.
- Requirements aligned with the library versions shipped by YWeb.
- Shorter, purpose-oriented plugin description in `meta/info.json`.
- License: GPL-3.0-only, declared in `LICENSE`, `meta/info.json` and the README.
- README restructured on the common layout; citation updated to the published YSocial reference (Big Data & Society, 2026).

### Fixed
- `add_post` guards hashtags, emotions, mentions and topics against `None`.
- Tests run on an isolated temporary database and with SQLAlchemy 2 compatible fakes.

[Unreleased]: https://github.com/YSocialTwin/YServerReddit/compare/v2.1.1...HEAD
[2.1.1]: https://github.com/YSocialTwin/YServerReddit/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/YSocialTwin/YServerReddit/releases/tag/v2.1.0
