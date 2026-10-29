# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
- `/read`: `limit` is honoured by `rchrono` (it was fixed to 10).
- `/read`: `rchrono_popularity` and `rchrono_followers_popularity` order by reactions first, then recency (the old `ORDER BY id, reaction_count` made the second key a no-op).
- `/read`: the followers modes no longer return duplicated posts and share one implementation; the articles branch of `rchrono_followers_popularity` no longer crashes (`.order` typo) and filters by followed accounts.
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
