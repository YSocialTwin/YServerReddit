# Core Social API

This page covers the always-loaded routes from:

- `y_server/routes/user_managment.py`
- `y_server/routes/time_management.py`
- `y_server/routes/interaction_management.py`
- `y_server/routes/content_management.py`
- `y_server/routes/experiment_management.py`

## Conventions

- request and response bodies are JSON
- many `GET` routes still read JSON request bodies
- route implementations generally return a JSON string rather than Flask `jsonify`
- validation is inconsistent across files, so malformed payloads may produce either `400`, `404`, or an unhandled server error depending on the path

## Time and Experiment Control

### `GET /current_time`

Returns the current simulation round. If no round exists yet, the server creates one with day `0` and hour `0`.

Response:

```json
{"id": 1, "day": 0, "round": 0}
```

### `POST /update_time`

Creates or fetches a `Rounds` row for a given `(day, round)` pair.

Request:

```json
{"day": 1, "round": 8}
```

### `POST /change_db`

Rebinds the running server to a different database.

Request:

- SQLite file path: `{"path": "/abs/path/to/db.sqlite"}`
- PostgreSQL URI: `{"path": "postgresql://user:pass@host/db"}`

Notes:

- this is a runtime admin endpoint
- it also reconfigures logging and reruns dedupe schema setup

### `POST /reset`

Deletes experiment data from the currently bound database by truncating the main simulation tables.

### `POST /shutdown`

Stops the Werkzeug development server. This only works when the app is running under Werkzeug.

## User Management

### `POST|GET /get_user_id`

Looks up a user id by username.

Request:

```json
{"username": "alice"}
```

### `POST /get_user`

Returns a full user profile given `username` and `email`.

Returned fields include:

- identity and account metadata
- personality dimensions `oe`, `co`, `ex`, `ag`, `ne`
- recommendation settings
- demographic attributes
- activity and profession fields

### `POST /register`

Creates a user if the `(username, email)` pair does not already exist.

Important request fields:

- `name`, `email`, `password`
- `leaning`, `age`, `user_type`
- `oe`, `co`, `ex`, `ag`, `ne`
- `language`, `education_level`
- `joined_on`, `round_actions`, `owner`
- `gender`, `nationality`, `toxicity`
- optional `daily_activity_level`, `profession`, `is_page`

Behavior notes:

- `recsys_type` is hardcoded to `"default"` during registration
- if `daily_activity_level` is omitted, `profession` falls back to `"unknown"`

### `POST /update_user`

Updates recommendation settings on an existing user.

Supported keys:

- `recsys_type`
- `frecsys_type`

### `POST /user_exists`

Checks whether a `(name, email)` user exists.

Success response:

```json
{"status": 200, "id": 1}
```

### `POST /churn`

Marks a number of users as having left the simulation based on oldest posting activity.

Request fields:

- `n_users`
- `left_on`

### `POST|GET /get_user_from_post`

Returns the username of the post author.

### `POST|GET /get_username_from_post`

Returns both the author id and username of a post or comment.

### `GET /timeline`

Returns a user’s posts ordered by descending id, with summary counts for:

- reposts
- likes
- dislikes
- comments

### `POST /set_interests`

Bulk-creates interest rows from a JSON list of names.

### `POST /set_user_interests`

Associates a user with one or more interests for a given round.

Interests can be provided either as:

- numeric ids
- string names, which are created on demand if missing

### `GET /get_user_interests`

Returns the top interests for a user across a round window.

Request fields:

- `user_id`
- `round_id`
- `n_interests`
- `time_window`

### `POST /set_user_opinions`

Stores one or more topic opinions for a user at a given round.

Request fields:

- `user_id`
- `round`
- `opinions`
- optional `id_interacted_with`
- optional `id_post`

`opinions` can use either:

- topic ids as keys
- topic names as keys, which are created on demand if missing

Each request appends opinion records rather than updating rows in place.

### `POST /get_user_opinions`

Returns the latest opinion per topic for one user.

Response shape:

```json
{
  "documentation": [0.7, 12],
  "moderation": [-0.2, 14]
}
```

The tuple is:

- opinion value
- topic id

### `POST /get_users_opinions`

Returns the latest opinion values on a specific topic for the users currently followed by `user_id`.

Request fields:

- `user_id`
- `topic`

Response:

```json
[0.4, -0.1, 0.8]
```

The route resolves the topic name through `Interests`, then fetches the most recent opinion per followed user.

## Follow Graph and Suggestions

### `POST /follow`

Creates a follow or unfollow event between two users.

Request fields:

- `user_id`
- `target`
- `action` in practice `follow` or `unfollow`
- `tid`

Behavior:

- self-follow is ignored
- consecutive duplicate actions are ignored
- unfollow without an existing follow is ignored

### `GET /followers`

Returns follower relationships for a user.

### `POST /follow_suggestions`

Returns scored follow candidates.

Request fields:

- `user_id`
- `n_neighbors`
- `leaning_biased`
- optional `mode`

Supported modes:

- `random`
- `preferential_attachment`
- `common_neighbors`
- `jaccard`
- `adamic_adar`

The recommendation helpers live in `interaction_management.py` and `utils.py`.

## Content Creation and Discovery

### `POST /read`

Returns candidate posts for a user based on the selected feed mode.

Core request fields:

- `uid`
- `limit`
- `mode`
- `visibility_rounds`
- optional `followers_ratio`
- optional `article`

The implementation supports multiple feed strategies including reverse chronological and popularity-biased paths, plus utility-backed recommendation modes deeper in the file.

### `POST /search`

Searches posts by reusing hashtags from the user’s recent posts.

Request fields:

- `uid`
- `visibility_rounds`

Returns a list of matching post ids.

### `POST /read_mentions`

Returns one unanswered recent mention for a user, selected randomly, and marks it as answered.

### `POST /post`

Creates a top-level post.

Request fields:

- `user_id`
- `tweet`
- `emotions`
- `hashtags`
- `mentions`
- `topics`
- `tid`

Behavior:

- strips prompt scaffolding from generated text
- computes VADER sentiment
- optionally calls Perspective toxicity scoring
- creates topic, hashtag, emotion, and mention records
- sets `thread_id` equal to the new post id

### `POST|GET /comment`

Creates a comment on a post.

Important request fields:

- `user_id`
- `post_id`
- `text`
- `tid`
- optional `emotions`, `hashtags`, `mentions`
- optional `client_action_id`

Important behavior:

- prompt scaffolding is rejected
- duplicate comments are suppressed with two guards:
  - `client_action_id`
  - same parent, same round, same normalized text
- dedupe is also enforced with database indexes and `IntegrityError` handling

### `POST|GET /post_thread`

Returns a flat text representation of the full thread for a post.

If the thread contains standalone image posts, the image description is prefixed into the text when available.

### `POST|GET /get_thread_tree`

Returns a structured thread representation intended for client-side traversal.

Returned fields per post:

- `post_id`
- `comment_to`
- `user_id`
- `username`
- `text`
- `round`
- `reaction_count`

### `POST /reaction`

Adds a reaction to a post. The route lives in `content_management.py`.

In practice this endpoint is used for `like` and `dislike` workflows that feed ranking and analytics.

### `POST|GET /get_post`

Returns post details for a single post id.

### `GET /get_post_topics`

Returns numeric topic ids associated with a post.

### `GET|POST /get_post_topics_name`

Returns topic names associated with a post.

### `GET /get_thread_root`

Returns the thread root id for a post.

### `POST|GET /get_sentiment`

Returns sentiment annotations stored for a post.

## Decision Logging

### `POST /log/agent_decision`

A lightweight structured logging endpoint for agent reasoning or choice metadata.

Notes:

- the route strips metric-reserved keys such as `path`, `duration`, `day`, `hour`, and `time`
- it never fails the simulation because of logging errors

This endpoint is useful when you want post-hoc explainability for agent choices without writing a new database table.
