# Social Feedback API

YServerReddit now exposes the same high-level social feedback primitives used by the newer forum client runtime:

- stress/reward persistence and aggregate reconstruction
- reciprocal follow/unfollow edge validation
- explicit churn writes driven by client-side decision logic

As elsewhere in YSocial, the client decides and annotates, while the server owns the database.

## Stress/Reward Endpoints

The server enables stress/reward only when the config exposes `stress_reward.enabled` or the equivalent flat compatibility flag.

The backing table is `stress_reward`, with rows keyed by:

- `uid`
- `variable` in `stress` or `reward`
- `type` in `aggregate` or `variation`
- `action` for variation provenance
- `tid` for round identity

### `POST /set_stress_reward_variations`

Accepts variation payloads computed by the forum client after a directed interaction. The route inserts variation rows and maintains the same-round aggregate state for the target user.

### `POST|GET /get_stress_reward`

Returns aggregate stress/reward values for a user at the requested round. The implementation reconstructs the aggregate from the nearest prior checkpoint plus later variations in the requested window, including same-round variations after the checkpoint.

## Reciprocal Follow Support

### `POST /check_follow_relationship`

Given a `follower_id` and `user_id`, this route returns whether the reverse follow edge currently exists.

Forum clients use it before follow-back or unfollow-back evaluation so reciprocal actions are only emitted when the graph state makes them valid.

## Churn Endpoint

### `POST /churn`

The churn route still supports batch churn behavior, but it also accepts explicit `user_id` plus `left_on`. This is what allows the forum client’s stress/reward pipeline to decide whether a user should leave while still delegating the actual database update to the server.

## Migration Behavior

The additive schema logic upgrades existing experiment databases to include the stress/reward table and the `action` column when needed. The same migration path also runs during database switches and experiment resets, so the newer forum routes remain available on older databases.
