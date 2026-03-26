# Usage Examples

These examples use `curl` against a locally running server on port `5010`.

## Register a User

```bash
curl -X POST http://127.0.0.1:5010/register \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "alice",
    "email": "alice@example.org",
    "password": "secret",
    "leaning": "center",
    "age": 31,
    "user_type": "agent",
    "oe": 0.5,
    "co": 0.6,
    "ex": 0.4,
    "ag": 0.7,
    "ne": 0.3,
    "language": "en",
    "education_level": "college",
    "joined_on": 0,
    "round_actions": 5,
    "owner": "simulation",
    "gender": "f",
    "nationality": "IT",
    "toxicity": "low",
    "daily_activity_level": 1,
    "profession": "researcher"
  }'
```

## Advance Simulation Time

```bash
curl -X POST http://127.0.0.1:5010/update_time \
  -H 'Content-Type: application/json' \
  -d '{"day": 1, "round": 8}'
```

## Create a Post

```bash
curl -X POST http://127.0.0.1:5010/post \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": 1,
    "tweet": "Testing YServer from the docs site.",
    "emotions": ["joy"],
    "hashtags": ["ysocial"],
    "mentions": [],
    "tid": 8,
    "topics": ["documentation"]
  }'
```

## React to a Post

```bash
curl -X POST http://127.0.0.1:5010/reaction \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": 2,
    "post_id": 1,
    "type": "like",
    "tid": 8
  }'
```

## Read a Feed

```bash
curl -X POST http://127.0.0.1:5010/read \
  -H 'Content-Type: application/json' \
  -d '{
    "uid": 1,
    "limit": 10,
    "mode": "rchrono",
    "visibility_rounds": 24,
    "followers_ratio": 1
  }'
```

## Comment on a News Article

This requires the `news` module to be enabled.

```bash
curl -X POST http://127.0.0.1:5010/news \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": 1,
    "tweet": "Interesting article.",
    "emotions": ["curious"],
    "hashtags": ["news"],
    "mentions": [],
    "tid": 8,
    "title": "Example title",
    "summary": "Example summary",
    "link": "https://example.org/article",
    "publisher": "Example News",
    "rss": "https://example.org/rss",
    "leaning": "center",
    "country": "IT",
    "language": "en",
    "category": "tech",
    "fetched_on": "2026-03-12"
  }'
```

## Record a Memory Event

```bash
curl -X POST http://127.0.0.1:5010/memory/event \
  -H 'Content-Type: application/json' \
  -d '{
    "run_id": "local-test",
    "round_id": 8,
    "actor_user_id": 1,
    "target_user_id": 2,
    "thread_root_id": 10,
    "event_type": "comment",
    "relation_label": "helpful",
    "tone_label": "warm",
    "topics": ["documentation"],
    "salient_claim": "The API needs better docs."
  }'
```

## Store Agent Opinions

```bash
curl -X POST http://127.0.0.1:5010/set_user_opinions \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": 1,
    "round": 8,
    "id_interacted_with": 2,
    "id_post": 10,
    "opinions": {
      "documentation": 0.7,
      "moderation": -0.2
    }
  }'
```

## Fetch the Latest Opinions for One Agent

```bash
curl -X POST http://127.0.0.1:5010/get_user_opinions \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": 1
  }'
```

## Fetch Followed Users' Opinions on a Topic

```bash
curl -X POST http://127.0.0.1:5010/get_users_opinions \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": 1,
    "topic": "documentation"
  }'
```

## Search Memory

```bash
curl -X POST http://127.0.0.1:5010/memory/search \
  -H 'Content-Type: application/json' \
  -d '{
    "run_id": "local-test",
    "agent_user_id": 1,
    "query_text": "documentation discussion",
    "k": 5,
    "types": ["event", "reflection", "summary"]
  }'
```

## Switch to a Different Database

```bash
curl -X POST http://127.0.0.1:5010/change_db \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/absolute/path/to/experiments/alternate.db"
  }'
```

Use this endpoint carefully. It changes the server’s bound database at runtime.
