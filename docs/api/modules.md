# Optional Modules

Optional route modules are loaded from the `modules` array in the active config file.

Current module names:

- `news`
- `voting`
- `image`
- `image_post`

## News Module

Implemented in `y_server/routes/news_management.py`.

### `POST /news`

Creates or attaches a news article and optionally a post that comments on it.

Important request fields:

- `user_id`
- `tweet`
- `emotions`
- `hashtags`
- `mentions`
- `tid`
- article metadata:
  - `title`
  - `summary`
  - `link`
  - `publisher`
  - `rss`
  - `leaning`
  - `country`
  - `language`
  - `category`
  - `fetched_on`
- optional:
  - `image_url`
  - `topics`
  - `is_share_link`

Behavior:

- creates `Websites` and `Articles` rows on demand
- can attach an image to the article
- skips post creation when the sanitized text becomes empty
- suppresses duplicate same-round link-share posts when `is_share_link` is set
- adds topic and sentiment rows when `topics` are present

### `POST|GET /get_article_by_title`

Looks up an article by title.

### `POST|GET /get_article`

Fetches the article summary and title associated with a post id.

### `POST|GET /share`

Currently disabled. The route immediately returns `403` with a message saying sharing existing posts is disabled for forum experiments.

## Voting Module

Implemented in `y_server/routes/voting_management.py`.

### `POST /cast_preference`

Stores a vote record.

Request fields:

- `tid`
- `user_id`
- `vote`
- `content_type`
- `content_id`

## Image Comment Module

Implemented in `y_server/routes/image_management.py`.

### `POST /comment_image`

Creates a top-level post associated with an image.

Request fields:

- `user_id`
- `text`
- `emotions`
- `hashtags`
- `tid`
- `image_url`
- `image_description`
- optional `article_id`

Behavior:

- creates an `Images` row if needed
- creates a top-level `Post` with `image_id`
- computes sentiment and optional toxicity
- stores hashtags and emotions
- rejects prompt scaffolding in generated text

## Standalone Image Post Module

Implemented in `y_server/routes/image_post_management.py`.

### `POST /image_post`

Creates a post backed by an `ImagePosts` entry rather than an `Images` row tied to a news article.

Request fields:

- `user_id`
- `tweet`
- `image_url`
- optional `image_description`
- optional `emotions`
- optional `hashtags`
- optional `mentions`
- `tid`
- optional `topics`

Behavior:

- reuses or creates an `ImagePosts` row
- marks the image as `used`
- creates the post and thread root
- optionally stores mentions, hashtags, topic links, and sentiment rows

### `POST|GET /get_image_post`

Returns the image URL and description for a post backed by an `image_post_id`.
