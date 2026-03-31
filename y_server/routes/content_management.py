import json
import hashlib
import math
import os
import re
import threading
import time
from flask import request
from y_server import app, db
from y_server.utils import (
    get_follows,
    fetch_common_interest_posts,
    fetch_common_user_interest_posts,
    fetch_similar_users_posts,
    get_posts_by_reactions,
    get_posts_by_author,
)
from sqlalchemy import desc, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.expression import func
from y_server.modals import (
    Hashtags,
    Post_hashtags,
    Rounds,
    Post,
    Recommendations,
    Follow,
    Reactions,
    Mentions,
    User_mgmt,
    Emotions,
    Post_emotions,
    Post_topics,
    Post_Sentiment,
    Interests,
    ImagePosts,
    MemoryInteractionEvent,
    MemorySocialCard,
    MemoryThreadCard,
    MemoryCommunityDigest,
    MemoryItem,
)
from y_server.content_analysis import (
    should_annotate_emotions,
    should_annotate_sentiment,
    should_annotate_toxicity,
    vader_sentiment,
    toxicity,
)
from y_server.memory_embedding import (
    MemoryEmbeddingService,
    cosine_similarity,
    lexical_relevance,
)


_MEMORY_SCHEMA_READY = False
_MEMORY_SCHEMA_EVOLUTION_READY = False
_MEMORY_INDEXER_STARTED = False
_MEMORY_EMBEDDING = MemoryEmbeddingService()
_MEMORY_QUERY_ALIAS_MAP = {
    "dnd": ["d and d", "d&d", "dungeons and dragons"],
    "d&d": ["dnd", "d and d", "dungeons and dragons"],
    "d and d": ["dnd", "d&d", "dungeons and dragons"],
    "dungeons and dragons": ["dnd", "d&d", "d and d"],
}

# Hot-feed longtail defaults (kept aligned with y_web/reddit/hot_rank.py).
_HOT_LONGTAIL_VOTE_THRESH1 = 3
_HOT_LONGTAIL_VOTE_THRESH2 = 8
_HOT_LONGTAIL_J1 = 0.45
_HOT_LONGTAIL_J2 = 0.20


def _normalize_embedding_host(value):
    host = str(value or "").strip()
    if not host:
        return ""
    if not host.startswith("http://") and not host.startswith("https://"):
        host = f"http://{host}"
    host = host.rstrip("/")
    if host.endswith("/v1"):
        host = host[:-3].rstrip("/")
    return host


def configure_memory_embedding(service=None, host=None, model=None):
    """Reconfigure the forum memory embedding backend for the currently bound experiment."""
    global _MEMORY_EMBEDDING

    normalized_service = str(service or "").strip().lower()
    normalized_host = _normalize_embedding_host(host)
    normalized_model = str(model or "").strip()

    if normalized_service == "ollama" and normalized_host and normalized_model:
        _MEMORY_EMBEDDING = MemoryEmbeddingService(
            model_name=normalized_model,
            ollama_host=normalized_host,
        )
    else:
        _MEMORY_EMBEDDING = MemoryEmbeddingService()

    try:
        app.logger.info(
            "memory_embedding_configured",
            extra={
                "service": normalized_service or "disabled",
                "host": normalized_host,
                "model": normalized_model,
                "available": bool(_MEMORY_EMBEDDING.available),
                "error": _MEMORY_EMBEDDING.last_error,
            },
        )
    except Exception:
        pass


def configure_memory_embedding_from_config(config_data):
    """Apply memory embedding settings from a server config payload."""
    settings = {}
    if isinstance(config_data, dict):
        settings = config_data.get("memory_embeddings") or {}
    if not isinstance(settings, dict):
        settings = {}
    configure_memory_embedding(
        service=settings.get("service"),
        host=settings.get("host"),
        model=settings.get("model"),
    )


def _hot_stable_uniform_0_1(*parts: object, salt: str = "forum-hot-longtail-v2") -> float:
    """Deterministic pseudo-random uniform in [0, 1)."""
    key = salt + "|" + "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    x = int.from_bytes(digest[:8], "big", signed=False)
    return x / float(2**64)


def _hot_base_score(net_score: int, post_round: int, *, round_decay: float = 12.0) -> float:
    if net_score > 0:
        sign = 1.0
    elif net_score < 0:
        sign = -1.0
    else:
        sign = 0.0
    return math.log10(abs(int(net_score)) + 1.0) + sign * (
        float(post_round) / float(round_decay)
    )


def _hot_longtail_boost(
    likes: int,
    dislikes: int,
    *,
    u01: float,
    vote_thresh1: int = _HOT_LONGTAIL_VOTE_THRESH1,
    vote_thresh2: int = _HOT_LONGTAIL_VOTE_THRESH2,
    j1: float = _HOT_LONGTAIL_J1,
    j2: float = _HOT_LONGTAIL_J2,
) -> float:
    total_votes = int(likes) + int(dislikes)
    if total_votes <= int(vote_thresh1):
        return float(u01) * float(j1)
    if total_votes <= int(vote_thresh2):
        return float(u01) * float(j2)
    return 0.0


def _normalize_comment_for_dedupe(text_value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text_value or "").strip().lower())
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _comment_dedupe_key(text_value: str):
    normalized = _normalize_comment_for_dedupe(text_value)
    if not normalized:
        return None
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


_PROMPT_SCAFFOLD_PATTERNS = [
    re.compile(r"\bmemory tier [abc]\b", re.IGNORECASE),
    re.compile(r"\bmemory context\b", re.IGNORECASE),
    re.compile(r"\bmemory search brief\b", re.IGNORECASE),
    re.compile(r"\bmemory pack\b", re.IGNORECASE),
    re.compile(r"\bfacts pack\b", re.IGNORECASE),
    re.compile(r"\bi am the handler\b", re.IGNORECASE),
    re.compile(r"\bwrite a new caption\b", re.IGNORECASE),
    re.compile(r"\byour interests\s*\(pick one\)\b", re.IGNORECASE),
]


def _looks_like_prompt_scaffold(text_value):
    text = str(text_value or "").strip()
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _PROMPT_SCAFFOLD_PATTERNS)


def _sanitize_generated_text(text_value, *, max_len=None):
    text = str(text_value or "")
    if not text.strip():
        return ""

    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _looks_like_prompt_scaffold(line):
            continue
        if line.lower().startswith("previous bad attempt:"):
            continue
        lines.append(raw_line)

    cleaned = "\n".join(lines).strip()
    if cleaned and _looks_like_prompt_scaffold(cleaned):
        cleaned = ""
    if max_len is not None and len(cleaned) > int(max_len):
        cleaned = cleaned[: int(max_len)]
    return cleaned


def _payload_has_prompt_scaffold(value):
    if isinstance(value, str):
        return _looks_like_prompt_scaffold(value)
    if isinstance(value, (list, dict)):
        try:
            return _looks_like_prompt_scaffold(json.dumps(value))
        except Exception:
            return False
    return False


def _reject_prompt_scaffold(field_name):
    try:
        app.logger.warning(
            "prompt_scaffold_rejected",
            extra={"field": str(field_name or ""), "route": request.path},
        )
    except Exception:
        pass
    return (
        json.dumps(
            {
                "status": 422,
                "error": "prompt_scaffold_detected",
                "field": str(field_name or ""),
            }
        ),
        422,
    )


def _json_loads_maybe(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def _normalize_username(value):
    if value is None:
        return None
    try:
        uname = str(value).strip().lstrip("@")
    except Exception:
        return None
    return uname or None


def _normalize_memory_query_text(value):
    try:
        s = str(value or "").strip().lower()
    except Exception:
        return ""
    if not s:
        return ""
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_memory_query_variants(query_text):
    base = str(query_text or "").strip()
    normalized = _normalize_memory_query_text(base)
    out = []
    seen = set()

    def _add(v):
        sv = str(v or "").strip()
        key = sv.lower()
        if not sv or key in seen:
            return
        seen.add(key)
        out.append(sv)

    _add(base)
    _add(normalized)
    for k, aliases in _MEMORY_QUERY_ALIAS_MAP.items():
        if not normalized:
            continue
        if re.search(rf"\b{re.escape(k)}\b", normalized):
            for a in aliases:
                _add(a)
                _add(re.sub(rf"\b{re.escape(k)}\b", a, normalized))
    return out[:12]


def _lexical_match_details(query_text, memory_text):
    q_tokens = set(re.findall(r"[a-z0-9]+", str(query_text or "").lower()))
    m_tokens = set(re.findall(r"[a-z0-9]+", str(memory_text or "").lower()))
    if not q_tokens or not m_tokens:
        return {"score": 0.0, "matched_terms": []}
    inter = sorted(q_tokens & m_tokens)
    if not inter:
        return {"score": 0.0, "matched_terms": []}
    score = float(len(inter) / math.sqrt(len(q_tokens) * len(m_tokens)))
    return {"score": score, "matched_terms": inter[:10]}


def _build_user_map(user_ids):
    ids = set()
    if isinstance(user_ids, (list, set, tuple)):
        for raw in user_ids:
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            if uid > 0:
                ids.add(uid)
    if not ids:
        return {}

    out = {}
    try:
        rows = User_mgmt.query.filter(User_mgmt.id.in_(sorted(ids))).all()
    except Exception:
        return out

    for row in rows:
        try:
            uid = int(getattr(row, "id", 0) or 0)
        except Exception:
            uid = 0
        if uid <= 0:
            continue
        uname = _normalize_username(getattr(row, "username", None))
        if uname:
            out[uid] = uname
    return out


def _humanize_memory_text(text_value, user_map):
    txt = str(text_value or "").strip()
    if not txt:
        return txt
    if not isinstance(user_map, dict) or not user_map:
        return txt

    import re

    def _uname(uid):
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            return None
        return _normalize_username(user_map.get(uid))

    def _replace_target_user(match):
        uname = _uname(match.group(1))
        if not uname:
            return match.group(0)
        return f"target=@{uname}"

    def _replace_actor(match):
        uname = _uname(match.group(1))
        if not uname:
            return match.group(0)
        return f"actor:@{uname}"

    def _replace_user_id(match):
        uname = _uname(match.group(1))
        if not uname:
            return match.group(0)
        return f"@{uname}"

    txt = re.sub(r"\btarget_user_id\s*[:=]\s*(\d+)\b", _replace_target_user, txt)
    txt = re.sub(r"\bactor\s*:\s*(\d+)\b", _replace_actor, txt)
    txt = re.sub(r"\buser_id\s*[:=]\s*(\d+)\b", _replace_user_id, txt)
    txt = re.sub(r"\buser_id\s+(\d+)\b", _replace_user_id, txt)
    return txt


def _normalize_event_text(
    *,
    event_type: str,
    actor_user_id: int,
    target_user_id=None,
    thread_root_id=None,
    target_post_id=None,
    actor_post_id=None,
    relation_label=None,
    tone_label=None,
    salient_claim=None,
    topics=None,
):
    pieces = [f"{str(event_type or '').strip().lower()} event"]
    try:
        pieces.append(f"actor:{int(actor_user_id)}")
    except Exception:
        pass
    try:
        if target_user_id is not None:
            pieces.append(f"target_user_id:{int(target_user_id)}")
    except Exception:
        pass
    try:
        if thread_root_id is not None:
            pieces.append(f"thread_root_id:{int(thread_root_id)}")
    except Exception:
        pass
    try:
        if target_post_id is not None:
            pieces.append(f"target_post_id:{int(target_post_id)}")
    except Exception:
        pass
    try:
        if actor_post_id is not None:
            pieces.append(f"actor_post_id:{int(actor_post_id)}")
    except Exception:
        pass
    if isinstance(relation_label, str) and relation_label.strip():
        pieces.append(f"relation:{relation_label.strip().lower()}")
    if isinstance(tone_label, str) and tone_label.strip():
        pieces.append(f"tone:{tone_label.strip().lower()}")
    if isinstance(salient_claim, str) and salient_claim.strip():
        pieces.append("claim:" + salient_claim.strip()[:200])

    topic_list = []
    if isinstance(topics, list):
        topic_list = [str(x).strip() for x in topics if str(x).strip()]
    elif isinstance(topics, str):
        parsed = _json_loads_maybe(topics)
        if isinstance(parsed, list):
            topic_list = [str(x).strip() for x in parsed if str(x).strip()]
        elif topics.strip():
            topic_list = [topics.strip()]
    if topic_list:
        pieces.append("topics:" + ", ".join(topic_list[:8]))

    return " | ".join([p for p in pieces if p])


def _estimate_importance(
    *,
    run_id: str,
    event_type: str,
    relation_label=None,
    tone_label=None,
    topics=None,
    salient_claim=None,
):
    base = {
        "comment": 0.45,
        "post": 0.35,
        "upvote": 0.20,
        "downvote": 0.35,
    }.get(str(event_type or "").strip().lower(), 0.30)

    relation = str(relation_label or "").strip().lower()
    tone = str(tone_label or "").strip().lower()
    if relation in {"hostile", "disagree"} or tone in {"angry", "snarky"}:
        base += 0.20
    if relation in {"helpful", "funny"}:
        base += 0.15
    if isinstance(salient_claim, str) and salient_claim.strip():
        base += 0.05

    topic_list = []
    parsed_topics = _json_loads_maybe(topics)
    if isinstance(parsed_topics, list):
        topic_list = [str(x).strip().lower() for x in parsed_topics if str(x).strip()]
    elif isinstance(topics, str) and topics.strip():
        topic_list = [topics.strip().lower()]

    if topic_list:
        digest = (
            MemoryCommunityDigest.query.filter_by(run_id=run_id)
            .order_by(desc(MemoryCommunityDigest.id))
            .first()
        )
        if digest is not None:
            polarizing = _json_loads_maybe(digest.polarizing_issues_json)
            if isinstance(polarizing, list):
                pset = {str(x).strip().lower() for x in polarizing if str(x).strip()}
                if any(t in pset for t in topic_list):
                    base += 0.10

    return max(0.0, min(1.0, float(base)))


def _ensure_column(table_name: str, column_name: str, column_sql: str):
    try:
        insp = inspect(db.engine)
        table_names = set(insp.get_table_names())
        if table_name not in table_names:
            return
        cols = {c["name"] for c in insp.get_columns(table_name)}
        if column_name in cols:
            return
    except Exception:
        return

    ddl = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
    try:
        with db.engine.begin() as conn:
            conn.execute(text(ddl))
    except Exception:
        pass


def _ensure_index(table_name: str, index_name: str, cols):
    if not cols:
        return
    try:
        insp = inspect(db.engine)
        table_names = set(insp.get_table_names())
        if table_name not in table_names:
            return
        idx_names = {idx["name"] for idx in insp.get_indexes(table_name)}
        if index_name in idx_names:
            return
    except Exception:
        return

    col_expr = ", ".join(cols)
    ddl = f"CREATE INDEX {index_name} ON {table_name} ({col_expr})"
    try:
        with db.engine.begin() as conn:
            conn.execute(text(ddl))
    except Exception:
        pass


def _ensure_memory_schema_evolution():
    _ensure_column("memory_interaction_events", "event_text", "TEXT")
    _ensure_column("memory_interaction_events", "importance", "FLOAT DEFAULT 0.0")
    _ensure_column("memory_interaction_events", "last_accessed_round", "INTEGER")
    _ensure_column("memory_interaction_events", "access_count", "INTEGER DEFAULT 0")

    _ensure_index(
        "memory_interaction_events",
        "idx_memory_interaction_events_run_id_id",
        ["run_id", "id"],
    )
    _ensure_index("memory_items", "idx_memory_items_run_agent", ["run_id", "agent_user_id"])
    _ensure_index("memory_items", "idx_memory_items_type", ["item_type"])
    _ensure_index("memory_items", "idx_memory_items_round", ["round_id"])
    _ensure_index("memory_items", "idx_memory_items_other", ["other_user_id"])
    _ensure_index("memory_items", "idx_memory_items_thread", ["thread_root_id"])
    _ensure_index("memory_items", "idx_memory_items_status", ["embedding_status"])
    _ensure_index("memory_items", "idx_memory_items_importance", ["importance"])
    _ensure_index(
        "memory_items",
        "idx_memory_items_run_agent_round_id",
        ["run_id", "agent_user_id", "round_id", "id"],
    )
    _ensure_index(
        "memory_items",
        "idx_memory_items_run_agent_type_round_id",
        ["run_id", "agent_user_id", "item_type", "round_id", "id"],
    )


def _memory_indexer_loop():
    while True:
        try:
            with app.app_context():
                pending = (
                    MemoryItem.query.filter(MemoryItem.embedding_status == "pending")
                    .order_by(MemoryItem.id)
                    .limit(32)
                    .all()
                )

                if not pending:
                    time.sleep(1.5)
                    continue

                texts = []
                valid_items = []
                for item in pending:
                    txt = (item.text or "").strip()
                    if not txt:
                        item.embedding_status = "failed"
                        continue
                    texts.append(txt)
                    valid_items.append(item)

                vectors = _MEMORY_EMBEDDING.embed_texts(texts)
                if not vectors:
                    for item in valid_items:
                        item.embedding_status = "failed"
                    db.session.commit()
                    time.sleep(1.0)
                    continue

                for item, vec in zip(valid_items, vectors):
                    if isinstance(vec, list) and vec:
                        try:
                            item.embedding_json = json.dumps(vec)
                            item.embedding_dim = len(vec)
                            item.embedding_model = _MEMORY_EMBEDDING.model_name
                            item.embedding_status = "ready"
                        except Exception:
                            item.embedding_status = "failed"
                    else:
                        item.embedding_status = "failed"
                db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            time.sleep(2.0)


def _maybe_start_memory_indexer():
    global _MEMORY_INDEXER_STARTED
    if _MEMORY_INDEXER_STARTED:
        return
    try:
        th = threading.Thread(target=_memory_indexer_loop, daemon=True, name="memory-embedding-indexer")
        th.start()
        _MEMORY_INDEXER_STARTED = True
    except Exception:
        pass


def _ensure_memory_schema():
    """
    Create memory tables on-demand. External servers may run on PostgreSQL or
    experiment-scoped SQLite DBs without migrations.
    """
    global _MEMORY_SCHEMA_READY, _MEMORY_SCHEMA_EVOLUTION_READY
    if not _MEMORY_SCHEMA_READY:
        try:
            db.create_all()
        except Exception:
            pass
        _MEMORY_SCHEMA_READY = True

    if not _MEMORY_SCHEMA_EVOLUTION_READY:
        _ensure_memory_schema_evolution()
        _MEMORY_SCHEMA_EVOLUTION_READY = True

    _maybe_start_memory_indexer()


@app.route("/read", methods=["POST"])
def read():
    """
    Return a list of candidate posts for the user as filtered by the content recommendation system.

    :return: a json object with the post ids
    """
    data = json.loads(request.get_data())
    try:
        limit = int(data["limit"])
    except Exception:
        limit = 10
    mode = data["mode"]
    vround = int(data["visibility_rounds"])
    try:
        uid = int(data["uid"])
    except:
        uid = None
    try:
        fratio = float(data["followers_ratio"])
    except:
        fratio = 1

    # if no user id is provided, return an empty list
    articles = False
    if "article" in data:
        articles = True
        # get the user
        us = User_mgmt.query.filter_by(id=uid).first()
        # get news pages ids having the same user leaning
        pages = User_mgmt.query.filter_by(is_page=1, leaning=us.leaning).all()
        if pages is not None:
            pages = [x.id for x in pages]
        else:
            pages = []

    # visibility
    current_round = Rounds.query.order_by(desc(Rounds.id)).first()
    visibility = current_round.id - vround

    def _base_posts_query(*, top_level_only=False):
        if articles:
            q = Post.query.filter(
                Post.round >= visibility,
                Post.news_id.isnot(None),
                Post.user_id.in_(pages),
            )
        else:
            q = Post.query.filter(Post.round >= visibility)
            if uid is not None:
                q = q.filter(Post.user_id != uid)
        if top_level_only:
            q = q.filter(Post.comment_to == -1)
        return q

    def _reaction_map_for_posts(post_ids):
        if not post_ids:
            return {}
        rows = (
            db.session.query(
                Reactions.post_id,
                Reactions.type,
                func.count(Reactions.id).label("cnt"),
            )
            .filter(
                Reactions.post_id.in_(post_ids),
                Reactions.type.in_(["like", "dislike"]),
            )
            .group_by(Reactions.post_id, Reactions.type)
            .all()
        )
        reaction_map = {}
        for row in rows:
            pid = int(row.post_id)
            likes, dislikes = reaction_map.get(pid, (0, 0))
            if row.type == "like":
                likes = int(row.cnt or 0)
            elif row.type == "dislike":
                dislikes = int(row.cnt or 0)
            reaction_map[pid] = (likes, dislikes)
        return reaction_map

    def _comment_count_map(thread_ids):
        if not thread_ids:
            return {}
        rows = (
            db.session.query(Post.thread_id, func.count(Post.id).label("comment_count"))
            .filter(
                Post.thread_id.in_(thread_ids),
                Post.comment_to != -1,
            )
            .group_by(Post.thread_id)
            .all()
        )
        return {int(row.thread_id): int(row.comment_count or 0) for row in rows}

    if fratio < 1:
        follower_posts_limit = int(limit * fratio)
        additional_posts_limit = limit - follower_posts_limit
    else:
        follower_posts_limit = limit
        additional_posts_limit = 0

    if mode == "rchrono":
        # get posts in reverse chronological order
        if articles:
            posts = (
                db.session.query(Post)
                .filter(
                    Post.round >= visibility,
                    Post.news_id.isnot(None),
                    Post.user_id.in_(pages),
                )
                .order_by(desc(Post.id))
                .limit(10)
            ).all()
        else:
            posts = (
                db.session.query(Post)
                .filter(Post.round >= visibility, Post.user_id != uid)
                .order_by(desc(Post.id))
                .limit(10)
            ).all()

    elif mode == "rchrono_popularity":
        if articles:
            posts = (
                db.session.query(Post)
                .filter(
                    Post.round >= visibility,
                    Post.news_id.isnot(None),
                    Post.user_id.in_(pages),
                )
                .order_by(desc(Post.id), desc(Post.reaction_count))
                .limit(limit)
            ).all()

        else:
            posts = (
                db.session.query(Post)
                .filter(Post.round >= visibility, Post.user_id != uid)
                .order_by(desc(Post.id), desc(Post.reaction_count))
                .limit(limit)
            ).all()

        posts = [posts, []]

    elif mode == "rchrono_followers":
        if fratio < 1:
            follower_posts_limit = int(limit * fratio)
            additional_posts_limit = limit - follower_posts_limit
        else:
            follower_posts_limit = limit
            additional_posts_limit = 0

        # get followers
        follower = Follow.query.filter_by(action="follow", user_id=uid)
        follower_ids = [f.follower_id for f in follower if f.follower_id != uid]

        # get posts from followers in reverse chronological order
        if articles:
            posts = (
                Post.query.filter(
                    Post.round >= visibility,
                    Post.news_id.isnot(None),
                    Post.user_id.in_(pages),
                    Post.user_id.in_(follower_ids),
                )
                .order_by(desc(Post.id))
                .limit(follower_posts_limit)
            ).all()
        else:
            posts = (
                Post.query.filter(
                    Post.round >= visibility, Post.user_id.in_(follower_ids)
                )
                .order_by(desc(Post.id))
                .limit(follower_posts_limit)
            ).all()

        if additional_posts_limit != 0:
            if articles:
                additional_posts = (
                    Post.query.filter(
                        Post.round >= visibility,
                        Post.news_id.isnot(None),
                        Post.user_id != uid,
                    )
                    .order_by(desc(Post.id))
                    .limit(additional_posts_limit)
                ).all()
            else:
                additional_posts = (
                    Post.query.filter(Post.round >= visibility, Post.user_id != uid)
                    .order_by(desc(Post.id))
                    .limit(additional_posts_limit)
                ).all()

            posts = [posts, additional_posts]

    elif mode == "rchrono_followers_popularity":
        if fratio < 1:
            follower_posts_limit = int(limit * fratio)
            additional_posts_limit = limit - follower_posts_limit
        else:
            follower_posts_limit = limit
            additional_posts_limit = 0

        # get followers
        follower = Follow.query.filter_by(action="follow", user_id=uid)
        follower_ids = [f.follower_id for f in follower if f.follower_id != uid]

        # get posts from followers ordered by likes and reverse chronologically
        if articles:
            posts = (
                db.session.query(Post)
                .filter(
                    Post.round >= visibility,
                    Post.news_id.isnot(None),
                    Post.user_id.in_(pages),
                )
                .order(desc(Post.id), desc(Post.reaction_count))
                .limit(follower_posts_limit)
            ).all()
        else:
            posts = (
                db.session.query(Post)
                .filter(Post.round >= visibility, Post.user_id.in_(follower_ids))
                .order_by(desc(Post.id), desc(Post.reaction_count))
                .limit(follower_posts_limit)
            ).all()

        if additional_posts_limit != 0:
            if articles:
                additional_posts = (
                    Post.query.filter(
                        Post.round >= visibility,
                        Post.news_id.isnot(None),
                        Post.user_id.in_(pages),
                    )
                    .order_by(
                        desc(Post.id),
                        desc(Post.reaction_count),
                    )
                    .limit(additional_posts_limit)
                ).all()
            else:
                additional_posts = (
                    Post.query.filter(Post.round >= visibility, Post.user_id != uid)
                    .order_by(desc(Post.id), desc(Post.reaction_count))
                    .limit(additional_posts_limit)
                ).all()

            posts = [posts, additional_posts]

    elif mode == "top":
        # Reddit-style top ranking (net vote score), threads only.
        candidate_limit = min(4000, max(int(limit) * 12, 400))
        candidates = (
            _base_posts_query(top_level_only=True)
            .order_by(desc(Post.id))
            .limit(candidate_limit)
            .all()
        )
        reaction_map = _reaction_map_for_posts([p.id for p in candidates])
        ranked = []
        for p in candidates:
            likes, dislikes = reaction_map.get(int(p.id), (0, 0))
            ranked.append((int(likes) - int(dislikes), int(p.id), p))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        posts = [p for _, _, p in ranked[:limit]]

    elif mode == "hot":
        # Reddit-style hot ranking with longtail exploration to soften concentration.
        try:
            round_decay = float(data.get("round_decay", 12.0))
        except Exception:
            round_decay = 12.0
        if round_decay <= 0:
            round_decay = 12.0

        try:
            vote_thresh1 = int(data.get("hot_vote_thresh1", _HOT_LONGTAIL_VOTE_THRESH1))
        except Exception:
            vote_thresh1 = _HOT_LONGTAIL_VOTE_THRESH1
        try:
            vote_thresh2 = int(data.get("hot_vote_thresh2", _HOT_LONGTAIL_VOTE_THRESH2))
        except Exception:
            vote_thresh2 = _HOT_LONGTAIL_VOTE_THRESH2
        try:
            j1 = float(data.get("hot_longtail_j1", _HOT_LONGTAIL_J1))
        except Exception:
            j1 = _HOT_LONGTAIL_J1
        try:
            j2 = float(data.get("hot_longtail_j2", _HOT_LONGTAIL_J2))
        except Exception:
            j2 = _HOT_LONGTAIL_J2

        if vote_thresh1 < 0:
            vote_thresh1 = _HOT_LONGTAIL_VOTE_THRESH1
        if vote_thresh2 < vote_thresh1:
            vote_thresh2 = _HOT_LONGTAIL_VOTE_THRESH2
        if j1 < 0:
            j1 = _HOT_LONGTAIL_J1
        if j2 < 0:
            j2 = _HOT_LONGTAIL_J2

        candidate_limit = min(4000, max(int(limit) * 12, 400))
        candidates = (
            _base_posts_query(top_level_only=True)
            .order_by(desc(Post.id))
            .limit(candidate_limit)
            .all()
        )
        reaction_map = _reaction_map_for_posts([p.id for p in candidates])
        current_round_id = int(current_round.id) if current_round is not None else 0
        viewer_id = int(uid) if uid is not None else -1

        ranked = []
        for p in candidates:
            likes, dislikes = reaction_map.get(int(p.id), (0, 0))
            net = int(likes) - int(dislikes)
            base = _hot_base_score(net, int(p.round or 0), round_decay=round_decay)
            u = _hot_stable_uniform_0_1(viewer_id, current_round_id, int(p.id))
            boost = _hot_longtail_boost(
                int(likes),
                int(dislikes),
                u01=u,
                vote_thresh1=vote_thresh1,
                vote_thresh2=vote_thresh2,
                j1=j1,
                j2=j2,
            )
            ranked.append((base + boost, int(p.id), p))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        posts = [p for _, _, p in ranked[:limit]]

    elif mode == "most_commented":
        # Rank threads by number of comments.
        candidate_limit = min(4000, max(int(limit) * 12, 400))
        candidates = (
            _base_posts_query(top_level_only=True)
            .order_by(desc(Post.id))
            .limit(candidate_limit)
            .all()
        )
        thread_ids = [int(p.thread_id or p.id) for p in candidates]
        comment_map = _comment_count_map(thread_ids)
        ranked = []
        for p in candidates:
            thread_id = int(p.thread_id or p.id)
            ranked.append((int(comment_map.get(thread_id, 0)), int(p.id), p))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        posts = [p for _, _, p in ranked[:limit]]

    elif mode == "rchrono_comments":
        # get posts with the most comments in reverse chronological order (as longer thread)
        # Use subquery to get distinct threads - PostgreSQL compatible
        follower_ids = get_follows(uid)

        # Subquery to get max post id per thread (to get one representative post per thread)
        subquery = (
            db.session.query(
                Post.thread_id,
                func.max(Post.id).label("max_post_id")
            )
            .filter(
                Post.round >= visibility,
                Post.news_id.isnot(None) if articles else True,
                Post.user_id.in_(follower_ids),
            )
            .group_by(Post.thread_id)
            .subquery()
        )

        posts = [
            db.session.query(Post)
            .join(subquery, Post.id == subquery.c.max_post_id)
            .order_by(desc(Post.reaction_count), desc(Post.id))
            .limit(follower_posts_limit)
            .all()
        ]

        if additional_posts_limit != 0:
            additional_posts = (
                db.session.query(Post)
                .join(subquery, Post.id == subquery.c.max_post_id)
                .order_by(desc(Post.reaction_count), desc(Post.id))
                .limit(additional_posts_limit)
                .all()
            )

            posts = [posts, additional_posts]

    elif mode == "common_interests":
        # get posts with common topic interests
        posts = fetch_common_interest_posts(
            uid=uid,
            visibility=visibility,
            articles=articles,
            follower_posts_limit=follower_posts_limit,
            additional_posts_limit=additional_posts_limit,
        )

    elif mode == "common_user_interests":
        # get most interacted posts by users with common interests
        posts = fetch_common_user_interest_posts(
            uid=uid,
            visibility=visibility,
            articles=articles,
            follower_posts_limit=follower_posts_limit,
            additional_posts_limit=additional_posts_limit,
            reactions_type=["like", "dislike"],
        )

    elif mode == "similar_users_react":
        # get posts from similar users
        posts = fetch_similar_users_posts(
            uid=uid,
            visibility=visibility,
            articles=articles,
            limit=limit,
            filter_function=get_posts_by_reactions,
            reactions_type=["like"],
        )

    elif mode == "similar_users_posts":
        # get posts from similar users
        posts = fetch_similar_users_posts(
            uid=uid,
            visibility=visibility,
            articles=articles,
            limit=limit,
            filter_function=get_posts_by_author,
        )

    else:
        # get posts in random order
        if articles:
            posts = (
                Post.query.filter(
                    Post.round >= visibility,
                    Post.news_id.isnot(None),
                    Post.user_id.in_(pages),
                )
                .order_by(func.random())
                .limit(limit)
            ).all()

        else:
            posts = (
                Post.query.filter(Post.round >= visibility, Post.user_id != uid)
                .order_by(func.random())
                .limit(limit)
            ).all()

    res = []

    for post_type in posts:
        if type(post_type) == list:
            for post in post_type:
                try:
                    if len(post) > 0 and post[0] is not None:
                        res.append(post[0].id)
                except:
                    if post is not None:
                        res.append(post.id)
        else:
            if type(post_type) == tuple:
                if len(post_type) > 0 and post_type[0] is not None:
                    res.append(post_type[0].id)
            else:
                if post_type is not None:
                    res.append(post_type.id)

    # save recommendations
    current_round = Rounds.query.order_by(desc(Rounds.id)).first()
    if len(res) > 0:
        recs = Recommendations(
            user_id=uid,
            post_ids="|".join([str(x) for x in res]),
            round=current_round.id,
        )
        db.session.add(recs)
        db.session.commit()
    return json.dumps(res)


@app.route("/search", methods=["POST"])
def search():
    """
    Search posts based on the most recently used hashtags for the user.

    :return: a json object with the post ids
    """
    data = json.loads(request.get_data())
    uid = int(data["uid"])
    vround = int(data["visibility_rounds"])

    # visibility
    current_round = Rounds.query.order_by(desc(Rounds.id)).first()
    visibility = current_round.id - vround

    # Subquery for user's recent posts (can return multiple rows)
    user_posts_subq = db.session.query(Post.id).filter(
        Post.user_id == uid,
        Post.round >= visibility
    ).subquery()

    # Subquery for hashtag IDs from those posts (can return multiple rows)
    hashtags_subq = db.session.query(Post_hashtags.hashtag_id).filter(
        Post_hashtags.post_id.in_(db.session.query(user_posts_subq.c.id))
    ).subquery()

    # Get matching hashtags using .in_() instead of == with scalar_subquery
    recent_user_hashtags = Hashtags.query.filter(
        Hashtags.id.in_(db.session.query(hashtags_subq.c.hashtag_id))
    ).limit(10)

    if recent_user_hashtags is not None:
        hashtag_ids = []
        for hashtag in recent_user_hashtags:
            hashtag_ids.append(hashtag.id)

        hashtag_ids = list(set(hashtag_ids))

        # Use explicit JOIN instead of implicit cross-join in WHERE clause
        recent_posts_with_hashtags = (
            db.session.query(Post_hashtags)
            .join(Post, Post.id == Post_hashtags.post_id)
            .filter(
                Post_hashtags.hashtag_id.in_(hashtag_ids),
                Post.user_id != uid,
                Post.round >= visibility,
            )
            .order_by(func.random())
            .limit(10)
        )

        res = []
        for post in recent_posts_with_hashtags:
            res.append(post.post_id)

        return json.dumps(res)

    json.dumps({"status": 404})


@app.route("/read_mentions", methods=["POST"])
def read_mention():
    """
    Search for recent mentions for the user.

    :return: a json object with the post ids mentioning the user
    """
    data = json.loads(request.get_data())
    uid = int(data["uid"])
    vround = int(data["visibility_rounds"])

    # visibility
    current_round = Rounds.query.order_by(desc(Rounds.id)).first()
    visibility = current_round.id - vround

    mention = (
        Mentions.query.filter(
            Mentions.user_id == uid,
            Mentions.round >= visibility,
            Mentions.answered == 0,
        )
        .order_by(func.random())
        .limit(1)
    ).first()

    if mention is not None:
        mention.answered = 1
        db.session.commit()
        return json.dumps([mention.post_id])

    else:
        return json.dumps({"status": 404})


@app.route("/post", methods=["POST"])
def add_post():
    """
    Add a new post.

    :return: a json object with the status of the post
    """
    data = json.loads(request.get_data())
    account_id = data["user_id"]
    text = data["tweet"].strip('"')
    emotions = data["emotions"]
    hastags = data["hashtags"]
    mentions = data["mentions"]
    topics = data["topics"]
    tid = int(data["tid"])

    user = User_mgmt.query.filter_by(id=account_id).first()

    text = text.strip("-")
    text = _sanitize_generated_text(text, max_len=4000)
    if not text:
        return _reject_prompt_scaffold("tweet")

    post = Post(
        tweet=text,
        round=tid,
        user_id=user.id,
        comment_to=-1,
    )

    db.session.add(post)
    db.session.commit()

    if should_annotate_toxicity(app.config):
        toxicity(text, app.config.get("perspective_api"), post.id, db, enabled=True)
    sentiment = vader_sentiment(text) if should_annotate_sentiment(app.config) else None

    post.thread_id = post.id
    db.session.commit()

    for topic_id in topics:
        tp = Post_topics(post_id=post.id, topic_id=topic_id)
        db.session.add(tp)
        db.session.commit()

        if sentiment is not None:
            post_sentiment = Post_Sentiment(
                post_id=post.id,
                user_id=user.id,
                pos=sentiment["pos"],
                neg=sentiment["neg"],
                neu=sentiment["neu"],
                compound=sentiment["compound"],
                round=tid,
                is_post=1,
                topic_id=topic_id,
            )
            db.session.add(post_sentiment)
            db.session.commit()

    if should_annotate_emotions(app.config):
        for emotion in emotions:
            if len(emotion) < 1:
                continue

            em = Emotions.query.filter_by(emotion=emotion).first()
            if em is not None:
                post_emotion = Post_emotions(post_id=post.id, emotion_id=em.id)
                db.session.add(post_emotion)
                db.session.commit()

    for tag in hastags:
        if len(tag) < 4:
            continue

        ht = Hashtags.query.filter_by(hashtag=tag).first()
        if ht is None:
            ht = Hashtags(hashtag=tag)
            db.session.add(ht)
            db.session.commit()
            ht = Hashtags.query.filter_by(hashtag=tag).first()

        post_tag = Post_hashtags(post_id=post.id, hashtag_id=ht.id)
        db.session.add(post_tag)
        db.session.commit()

    for mention in mentions:
        if len(mention) < 1:
            continue

        us = User_mgmt.query.filter_by(username=mention.strip("@")).first()

        # existing user and not self
        if us is not None and us.id != user.id:
            mn = Mentions(user_id=us.id, post_id=post.id, round=tid)
            db.session.add(mn)
            db.session.commit()
        else:
            text = text.replace(mention, "")

            # update post
            post.tweet = text.lstrip().rstrip()
            db.session.commit()

    return json.dumps({"status": 200})


@app.route(
    "/comment",
    methods=["POST", "GET"],
)
def add_comment():
    """
    Comment on a post.

    :return: a json object with the status of the comment
    """
    data = json.loads(request.get_data())
    account_id = data["user_id"]
    post_id = data["post_id"]
    text = str(data.get("text", "")).strip().strip('"')
    emotions = data.get("emotions") or []
    hastags = data.get("hashtags") or []
    mentions = data.get("mentions") or []
    tid = int(data["tid"])
    client_action_id = str(data.get("client_action_id") or "").strip()[:96] or None

    user = User_mgmt.query.filter_by(id=account_id).first()
    post = Post.query.filter_by(id=post_id).first()

    text = text.strip("-")
    text = _sanitize_generated_text(text, max_len=4000)
    if not text:
        return _reject_prompt_scaffold("text")
    dedupe_key = _comment_dedupe_key(text)

    if user is None or post is None or not text:
        return json.dumps({"status": 400, "error": "invalid comment payload"}), 400

    # Request-level idempotency token guard.
    if client_action_id:
        existing = Post.query.filter_by(
            user_id=user.id,
            client_action_id=client_action_id,
        ).first()
        if existing is not None:
            app.logger.info(
                "comment_deduped",
                extra={
                    "reason": "client_action_id",
                    "user_id": int(user.id),
                    "parent_id": int(post_id),
                    "round": int(tid),
                    "comment_id": int(existing.id),
                },
            )
            return json.dumps({"status": 200, "comment_id": existing.id, "deduped": True})

    # Same-parent/same-round/same-text guard (policy: allow if text differs).
    if dedupe_key:
        existing = Post.query.filter_by(
            user_id=user.id,
            comment_to=post_id,
            round=tid,
            dedupe_key=dedupe_key,
        ).first()
        if existing is not None:
            app.logger.info(
                "comment_deduped",
                extra={
                    "reason": "same_parent_round_text",
                    "user_id": int(user.id),
                    "parent_id": int(post_id),
                    "round": int(tid),
                    "comment_id": int(existing.id),
                },
            )
            return json.dumps({"status": 200, "comment_id": existing.id, "deduped": True})

    new_post = Post(
        tweet=text,
        round=tid,
        user_id=user.id,
        comment_to=post_id,
        thread_id=post.thread_id,
        dedupe_key=dedupe_key,
        client_action_id=client_action_id,
    )

    db.session.add(new_post)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = None
        if client_action_id:
            existing = Post.query.filter_by(
                user_id=user.id,
                client_action_id=client_action_id,
            ).first()
        if existing is None and dedupe_key:
            existing = Post.query.filter_by(
                user_id=user.id,
                comment_to=post_id,
                round=tid,
                dedupe_key=dedupe_key,
            ).first()
        if existing is not None:
            app.logger.info(
                "comment_deduped",
                extra={
                    "reason": "integrity_conflict",
                    "user_id": int(user.id),
                    "parent_id": int(post_id),
                    "round": int(tid),
                    "comment_id": int(existing.id),
                },
            )
            return json.dumps({"status": 200, "comment_id": existing.id, "deduped": True})
        return json.dumps({"status": 500, "error": "comment create conflict"}), 500

    # get sentiment of the post is responding to
    sentiment_parent = Post_Sentiment.query.filter_by(post_id=post_id).first()
    if sentiment_parent is not None:
        sentiment_parent = sentiment_parent.compound
        # thresholding
        if sentiment_parent > 0.05:
            sentiment_parent = "pos"
        elif sentiment_parent < -0.05:
            sentiment_parent = "neg"
        else:
            sentiment_parent = "neu"
    else:
        sentiment_parent = ""

    if should_annotate_toxicity(app.config):
        toxicity(text, app.config.get("perspective_api"), new_post.id, db, enabled=True)
    sentiment = vader_sentiment(text) if should_annotate_sentiment(app.config) else None

    # get topics associated to post.id
    post_topics = Post_topics.query.filter_by(post_id=post.thread_id).all()
    for topic in post_topics:
        if sentiment is not None:
            post_sentiment = Post_Sentiment(
                post_id=new_post.id,
                user_id=user.id,
                pos=sentiment["pos"],
                neg=sentiment["neg"],
                neu=sentiment["neu"],
                compound=sentiment["compound"],
                sentiment_parent=sentiment_parent,
                round=tid,
                is_comment=1,
                topic_id=topic.topic_id,
            )
            db.session.add(post_sentiment)
            db.session.commit()

    if should_annotate_emotions(app.config):
        for emotion in emotions:
            if len(emotion) < 1:
                continue

            em = Emotions.query.filter_by(emotion=emotion).first()
            if em is not None:
                post_emotion = Post_emotions(post_id=new_post.id, emotion_id=em.id)
                db.session.add(post_emotion)
                db.session.commit()

    for tag in hastags:
        if len(tag) < 1:
            continue

        ht = Hashtags.query.filter_by(hashtag=tag).first()
        if ht is None:
            ht = Hashtags(hashtag=tag)
            db.session.add(ht)
            db.session.commit()
            ht = Hashtags.query.filter_by(hashtag=tag).first()

        post_tag = Post_hashtags(post_id=new_post.id, hashtag_id=ht.id)
        db.session.add(post_tag)
        db.session.commit()

    for mention in mentions:
        if len(mention) < 1:
            continue

        us = User_mgmt.query.filter_by(username=mention.strip("@")).first()
        if us is not None:
            mn = Mentions(user_id=us.id, post_id=new_post.id, round=tid)
            db.session.add(mn)
            db.session.commit()
        else:
            text = text.replace(mention, "")

            # update post
            post.tweet = text.lstrip().rstrip()

            # more than one word
            if len(post.tweet.split(" ")) > 1:
                db.session.commit()
            else:
                db.session.delete(post)
                db.session.commit()

    return json.dumps({"status": 200, "comment_id": new_post.id, "deduped": False})


@app.route(
    "/post_thread",
    methods=["POST", "GET"],
)
def post_thread():
    """
    Get the thread of a post.

    :return: a json object with the thread
    """
    data = json.loads(request.get_data() or "{}")
    post_id = data.get("post_id")

    try:
        post_id = int(post_id)
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "Invalid post_id"})

    post = Post.query.filter_by(id=post_id).first()
    if post is None:
        return json.dumps({"status": 404, "error": "Post not found"})

    thread_id = Post.query.filter_by(thread_id=post.thread_id)

    res = []

    for post in thread_id:
        user = post.user_id
        username = User_mgmt.query.filter_by(id=user).first().username

        text = post.tweet
        # Include standalone image description for better agent context.
        try:
            if getattr(post, "image_post_id", None) is not None:
                image_post = ImagePosts.query.filter_by(id=post.image_post_id).first()
                if image_post and image_post.description:
                    text = f"[Image: {image_post.description}] {text}"
        except Exception:
            pass

        res.append(f"@{username} - {text}\n")
    return json.dumps(res)


@app.route(
    "/get_thread_tree",
    methods=["POST", "GET"],
)
def get_thread_tree():
    """
    Return a structured representation of a thread so clients can traverse it
    in a human-like order (e.g., tree DFS) and target specific comments.
    """
    data = json.loads(request.get_data() or "{}")
    post_id = data.get("post_id")
    limit = data.get("limit", 200)

    try:
        post_id = int(post_id)
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "Invalid post_id"}), 400

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200

    # Reasonable safety bounds
    if limit <= 0:
        limit = 200
    if limit > 2000:
        limit = 2000

    post = Post.query.filter_by(id=post_id).first()
    if post is None:
        return json.dumps({"status": 404, "error": "Post not found"}), 404

    thread_root_id = int(post.thread_id)

    q = (
        db.session.query(Post, User_mgmt.username)
        .join(User_mgmt, User_mgmt.id == Post.user_id)
        .filter(Post.thread_id == thread_root_id)
        .order_by(Post.id.asc())
        .limit(limit)
    )

    posts = []
    for p, username in q.all():
        text = p.tweet
        try:
            if getattr(p, "image_post_id", None) is not None:
                image_post = ImagePosts.query.filter_by(id=p.image_post_id).first()
                if image_post and image_post.description:
                    text = f"[Image: {image_post.description}] {text}"
        except Exception:
            pass

        comment_to = getattr(p, "comment_to", None)
        if comment_to in (-1, None):
            comment_to = None

        posts.append(
            {
                "post_id": int(p.id),
                "comment_to": int(comment_to) if comment_to is not None else None,
                "user_id": int(p.user_id),
                "username": username,
                "text": text,
                "round": int(p.round) if p.round is not None else None,
                "reaction_count": int(getattr(p, "reaction_count", 0) or 0),
            }
        )

    return json.dumps({"status": 200, "thread_root_id": thread_root_id, "posts": posts})


@app.route("/log/agent_decision", methods=["POST"])
def log_agent_decision():
    """
    Lightweight structured logging endpoint for agent decision-making.

    Writes JSON to the server log (e.g., _server.log) so we can later explain why agents replied,
    voted, or chose a comment target.

    Notes:
    - Avoid reserved keys like 'path', 'duration', 'day', 'hour', 'time' which are used by the
      server-metrics parser. Clients should use 'tid', 'sim_day', 'sim_hour', 'ts', etc.
    """
    try:
        data = json.loads(request.get_data() or "{}")
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {"value": str(data)}

    # Defensive: strip keys that would pollute server metrics aggregation.
    for k in ["path", "duration", "day", "hour", "time"]:
        data.pop(k, None)

    try:
        app.logger.info("agent_decision", extra=data)
    except Exception:
        # Never break the simulation because logging failed.
        pass

    return json.dumps({"status": 200})


@app.route("/memory/reset", methods=["POST"])
def memory_reset():
    """Clear run-scoped memory state for a given run_id."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")
    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    MemoryInteractionEvent.query.filter_by(run_id=run_id).delete()
    MemoryItem.query.filter_by(run_id=run_id).delete()
    MemorySocialCard.query.filter_by(run_id=run_id).delete()
    MemoryThreadCard.query.filter_by(run_id=run_id).delete()
    MemoryCommunityDigest.query.filter_by(run_id=run_id).delete()
    db.session.commit()

    return json.dumps({"status": 200})


@app.route("/memory/event", methods=["POST"])
def memory_event():
    """Append a memory interaction event (written by the client)."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        round_id = int(data.get("round_id"))
        actor_user_id = int(data.get("actor_user_id"))
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "round_id and actor_user_id required"}), 400

    event_type = (data.get("event_type") or "").strip().lower()
    if event_type not in ["comment", "post", "upvote", "downvote"]:
        return json.dumps({"status": 400, "error": "invalid event_type"}), 400

    cold_start_window = data.get("cold_start_window", 5)
    try:
        cold_start_window = int(cold_start_window)
    except Exception:
        cold_start_window = 5
    if cold_start_window < 1:
        cold_start_window = 1
    if cold_start_window > 1000:
        cold_start_window = 1000

    def _to_int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    target_user_id = _to_int_or_none(data.get("target_user_id"))
    thread_root_id = _to_int_or_none(data.get("thread_root_id"))
    target_post_id = _to_int_or_none(data.get("target_post_id"))
    actor_post_id = _to_int_or_none(data.get("actor_post_id"))

    relation_label = data.get("relation_label")
    if isinstance(relation_label, str):
        relation_label = relation_label.strip().lower()[:16]
    else:
        relation_label = None

    tone_label = data.get("tone_label")
    if isinstance(tone_label, str):
        tone_label = tone_label.strip().lower()[:16]
    else:
        tone_label = None

    topics_json = None
    topics_payload = None
    topics = data.get("topics")
    if _payload_has_prompt_scaffold(topics):
        topics = None
    if isinstance(topics, (list, dict)):
        try:
            topics_json = json.dumps(topics)
            topics_payload = topics
        except Exception:
            topics_json = None
    elif isinstance(topics, str):
        topics_json = topics
        topics_payload = _json_loads_maybe(topics)
        if topics_payload is None:
            topics_payload = topics

    salient_claim = data.get("salient_claim")
    if isinstance(salient_claim, str):
        salient_claim = _sanitize_generated_text(salient_claim, max_len=200)
        if not salient_claim:
            salient_claim = None
    else:
        salient_claim = None

    weight = data.get("weight", 1.0)
    try:
        weight = float(weight)
    except Exception:
        weight = 1.0

    event_text = data.get("event_text")
    if isinstance(event_text, str):
        event_text = _sanitize_generated_text(event_text, max_len=4000)
    else:
        event_text = ""
    if not event_text:
        event_text = _normalize_event_text(
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            thread_root_id=thread_root_id,
            target_post_id=target_post_id,
            actor_post_id=actor_post_id,
            relation_label=relation_label,
            tone_label=tone_label,
            salient_claim=salient_claim,
            topics=topics_payload if topics_payload is not None else topics_json,
        )
        event_text = _sanitize_generated_text(event_text, max_len=4000)
    if not event_text:
        return _reject_prompt_scaffold("event_text")

    importance = data.get("importance")
    try:
        importance = float(importance)
    except Exception:
        importance = _estimate_importance(
            run_id=run_id,
            event_type=event_type,
            relation_label=relation_label,
            tone_label=tone_label,
            topics=topics_payload if topics_payload is not None else topics_json,
            salient_claim=salient_claim,
        )
    importance = max(0.0, min(1.0, float(importance)))

    ev = MemoryInteractionEvent(
        run_id=run_id,
        round_id=round_id,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        thread_root_id=thread_root_id,
        target_post_id=target_post_id,
        actor_post_id=actor_post_id,
        event_type=event_type,
        relation_label=relation_label,
        tone_label=tone_label,
        topics_json=topics_json,
        salient_claim=salient_claim,
        weight=weight,
        event_text=event_text[:4000] if isinstance(event_text, str) else None,
        importance=importance,
        last_accessed_round=round_id,
        access_count=0,
    )

    db.session.add(ev)
    db.session.flush()

    interaction_event_count = MemoryInteractionEvent.query.filter_by(
        run_id=run_id, actor_user_id=actor_user_id
    ).count()

    # Cold-start tracking is count-based and inclusive:
    # window=5 => interactions 1..5 are cold start, interaction 6 starts decay level 1.
    is_cold_start = int(interaction_event_count) <= int(cold_start_window)
    cold_start_decay_level = max(
        0, int(interaction_event_count) - int(cold_start_window)
    )

    metadata_payload = {
        "event_type": event_type,
        "actor_user_id": actor_user_id,
        "relation_label": relation_label,
        "tone_label": tone_label,
        "target_user_id": target_user_id,
        "thread_root_id": thread_root_id,
        "target_post_id": target_post_id,
        "actor_post_id": actor_post_id,
        "salient_claim": salient_claim,
        "interaction_event_count": int(interaction_event_count),
        "cold_start_window": int(cold_start_window),
        "cold_start_imprinted": bool(is_cold_start),
        "cold_start_decay_level": int(cold_start_decay_level),
    }
    mi = MemoryItem(
        run_id=run_id,
        agent_user_id=actor_user_id,
        item_type="event",
        text=(event_text or "")[:4000] if isinstance(event_text, str) else "",
        metadata_json=json.dumps(metadata_payload),
        source_event_id=ev.id,
        thread_root_id=thread_root_id,
        other_user_id=target_user_id,
        topic_tags_json=topics_json,
        round_id=round_id,
        importance=importance,
        recency_anchor_round=round_id,
        last_accessed_round=round_id,
        access_count=0,
        embedding_status="pending",
    )
    db.session.add(mi)
    db.session.flush()

    # Compatibility/debug metric: total memory item count (not used for cold-start)
    agent_item_count = MemoryItem.query.filter_by(
        run_id=run_id, agent_user_id=actor_user_id
    ).count()

    cold_start_importance_cap = None
    if is_cold_start:
        mi.importance = max(mi.importance or 0.0, 0.70)
    elif cold_start_decay_level > 0:
        # Progressive decay of imprinted cold-start memories:
        # after interaction 5 => decay level 1, after 6 => level 2, etc.
        # This reduces early-memory lock-in as the run matures.
        cold_start_importance_cap = max(0.25, 0.70 - (0.08 * cold_start_decay_level))
        imprinted_items = (
            MemoryItem.query.filter_by(
                run_id=run_id,
                agent_user_id=actor_user_id,
                item_type="event",
            )
            .order_by(MemoryItem.id.asc())
            .limit(int(cold_start_window))
            .all()
        )
        for imprinted in imprinted_items:
            try:
                current_imp = float(imprinted.importance or 0.0)
            except Exception:
                continue
            imprinted.importance = max(
                0.0, min(1.0, min(current_imp, cold_start_importance_cap))
            )

    # Synchronous embedding for cold-start items
    if is_cold_start and _MEMORY_EMBEDDING and _MEMORY_EMBEDDING.available:
        try:
            vec = _MEMORY_EMBEDDING.embed_text(mi.text)
            if vec:
                mi.embedding_json = json.dumps(vec)
                mi.embedding_dim = len(vec)
                mi.embedding_model = _MEMORY_EMBEDDING.model_name
                mi.embedding_status = "ready"
        except Exception:
            pass  # Fall back to async indexer

    db.session.commit()

    return json.dumps({
        "status": 200,
        "event_id": ev.id,
        "memory_item_id": mi.id,
        "cold_start": is_cold_start,
        "cold_start_window": int(cold_start_window),
        "cold_start_decay_level": int(cold_start_decay_level),
        "cold_start_importance_cap": (
            float(cold_start_importance_cap)
            if cold_start_importance_cap is not None
            else None
        ),
        "interaction_event_count": int(interaction_event_count),
        "agent_item_count": agent_item_count,
    })


@app.route("/memory/social/upsert", methods=["POST"])
def memory_social_upsert():
    """Upsert a per-agent social card about another user."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        agent_user_id = int(data.get("agent_user_id"))
        other_user_id = int(data.get("other_user_id"))
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "agent_user_id and other_user_id required"}), 400

    card = MemorySocialCard.query.filter_by(
        run_id=run_id, agent_user_id=agent_user_id, other_user_id=other_user_id
    ).first()
    if card is None:
        card = MemorySocialCard(
            run_id=run_id, agent_user_id=agent_user_id, other_user_id=other_user_id
        )
        db.session.add(card)

    for key in ["affinity", "conflict", "humor", "trust"]:
        if key in data:
            try:
                setattr(card, key, float(data.get(key)))
            except Exception:
                pass

    if "last_relation_label" in data and isinstance(data.get("last_relation_label"), str):
        card.last_relation_label = data.get("last_relation_label").strip().lower()[:16]

    for key in ["last_round_id", "last_thread_root_id", "last_updated_round", "event_count"]:
        if key in data:
            try:
                setattr(card, key, int(data.get(key)))
            except Exception:
                pass

    if "summary_text" in data:
        st = data.get("summary_text")
        if st is None:
            card.summary_text = None
        elif isinstance(st, str):
            card.summary_text = _sanitize_generated_text(st, max_len=4000) or None

    if "evidence_tail" in data:
        ev = data.get("evidence_tail")
        if ev is None:
            card.evidence_tail_json = None
        elif _payload_has_prompt_scaffold(ev):
            card.evidence_tail_json = None
        elif isinstance(ev, (list, dict)):
            try:
                card.evidence_tail_json = json.dumps(ev)
            except Exception:
                pass
        elif isinstance(ev, str):
            cleaned_ev = _sanitize_generated_text(ev, max_len=4000)
            card.evidence_tail_json = cleaned_ev or None

    db.session.commit()
    return json.dumps({"status": 200})


@app.route("/memory/thread/upsert", methods=["POST"])
def memory_thread_upsert():
    """Upsert a per-agent thread card."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        agent_user_id = int(data.get("agent_user_id"))
        thread_root_id = int(data.get("thread_root_id"))
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "agent_user_id and thread_root_id required"}), 400

    card = MemoryThreadCard.query.filter_by(
        run_id=run_id, agent_user_id=agent_user_id, thread_root_id=thread_root_id
    ).first()
    if card is None:
        card = MemoryThreadCard(
            run_id=run_id, agent_user_id=agent_user_id, thread_root_id=thread_root_id
        )
        db.session.add(card)

    if "gist_text" in data:
        gt = data.get("gist_text")
        if gt is None:
            card.gist_text = None
        elif isinstance(gt, str):
            card.gist_text = _sanitize_generated_text(gt, max_len=4000) or None

    if "my_role" in data and isinstance(data.get("my_role"), str):
        card.my_role = data.get("my_role").strip().lower()[:16]

    if "participants_top" in data:
        pt = data.get("participants_top")
        if pt is None:
            card.participants_top_json = None
        elif _payload_has_prompt_scaffold(pt):
            card.participants_top_json = None
        elif isinstance(pt, (list, dict)):
            try:
                card.participants_top_json = json.dumps(pt)
            except Exception:
                pass
        elif isinstance(pt, str):
            cleaned_pt = _sanitize_generated_text(pt, max_len=4000)
            card.participants_top_json = cleaned_pt or None

    if "entry_points" in data:
        ep = data.get("entry_points")
        if ep is None:
            card.entry_points_json = None
        elif _payload_has_prompt_scaffold(ep):
            card.entry_points_json = None
        elif isinstance(ep, (list, dict)):
            try:
                card.entry_points_json = json.dumps(ep)
            except Exception:
                pass
        elif isinstance(ep, str):
            cleaned_ep = _sanitize_generated_text(ep, max_len=4000)
            card.entry_points_json = cleaned_ep or None

    if "last_seen_round_id" in data:
        try:
            card.last_seen_round_id = int(data.get("last_seen_round_id"))
        except Exception:
            pass

    db.session.commit()
    return json.dumps({"status": 200})


@app.route("/memory/community/get", methods=["POST"])
def memory_community_get():
    """Return the latest community digest for a run."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")
    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    digest = (
        MemoryCommunityDigest.query.filter_by(run_id=run_id)
        .order_by(desc(MemoryCommunityDigest.id))
        .first()
    )
    if digest is None:
        return json.dumps({"status": 404}), 404

    return json.dumps(
        {
            "status": 200,
            "run_id": run_id,
            "round_id": digest.round_id,
            "digest_text": digest.digest_text,
            "top_topics": digest.top_topics_json,
            "norms": digest.norms_json,
            "memes": digest.memes_json,
            "polarizing_issues": digest.polarizing_issues_json,
        }
    )


@app.route("/memory/community/update", methods=["POST"])
def memory_community_update():
    """Upsert the shared community digest for a run."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")
    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    round_id = data.get("round_id")
    try:
        round_id = int(round_id) if round_id is not None else None
    except Exception:
        round_id = None

    digest = (
        MemoryCommunityDigest.query.filter_by(run_id=run_id)
        .order_by(desc(MemoryCommunityDigest.id))
        .first()
    )
    if digest is None:
        digest = MemoryCommunityDigest(run_id=run_id)
        db.session.add(digest)

    digest.round_id = round_id

    for field, col in [
        ("digest_text", "digest_text"),
        ("top_topics", "top_topics_json"),
        ("norms", "norms_json"),
        ("memes", "memes_json"),
        ("polarizing_issues", "polarizing_issues_json"),
    ]:
        val = data.get(field)
        if val is None:
            continue
        if _payload_has_prompt_scaffold(val):
            setattr(digest, col, None)
            continue
        if isinstance(val, (list, dict)):
            try:
                setattr(digest, col, json.dumps(val))
            except Exception:
                pass
        elif isinstance(val, str):
            cleaned_val = _sanitize_generated_text(val, max_len=4000)
            setattr(digest, col, cleaned_val or None)

    db.session.commit()
    return json.dumps({"status": 200})


@app.route("/memory/item/upsert", methods=["POST"])
def memory_item_upsert():
    """Upsert a memory stream item (event/reflection/summary)."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        agent_user_id = int(data.get("agent_user_id"))
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "agent_user_id required"}), 400

    item_type = (data.get("item_type") or "").strip().lower()
    if item_type not in {"event", "reflection", "summary"}:
        return json.dumps({"status": 400, "error": "invalid item_type"}), 400

    text_value = data.get("text")
    if not isinstance(text_value, str) or not text_value.strip():
        return json.dumps({"status": 400, "error": "text required"}), 400
    text_value = _sanitize_generated_text(text_value, max_len=4000)
    if not text_value:
        return _reject_prompt_scaffold("text")

    def _to_int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    item_id = _to_int_or_none(data.get("id"))
    source_event_id = _to_int_or_none(data.get("source_event_id"))
    thread_root_id = _to_int_or_none(data.get("thread_root_id"))
    other_user_id = _to_int_or_none(data.get("other_user_id"))
    round_id = _to_int_or_none(data.get("round_id"))
    recency_anchor_round = _to_int_or_none(data.get("recency_anchor_round"))
    last_accessed_round = _to_int_or_none(data.get("last_accessed_round"))
    access_count = _to_int_or_none(data.get("access_count"))

    importance = data.get("importance")
    try:
        importance = float(importance)
    except Exception:
        importance = 0.5 if item_type == "reflection" else 0.35
    importance = max(0.0, min(1.0, importance))

    metadata_json = None
    metadata = data.get("metadata")
    if _payload_has_prompt_scaffold(metadata):
        metadata = None
    if isinstance(metadata, (dict, list)):
        try:
            metadata_json = json.dumps(metadata)
        except Exception:
            metadata_json = None
    elif isinstance(metadata, str):
        cleaned_metadata = _sanitize_generated_text(metadata, max_len=4000)
        metadata_json = cleaned_metadata or None

    topic_tags_json = None
    topic_tags = data.get("topic_tags")
    if _payload_has_prompt_scaffold(topic_tags):
        topic_tags = None
    if isinstance(topic_tags, (dict, list)):
        try:
            topic_tags_json = json.dumps(topic_tags)
        except Exception:
            topic_tags_json = None
    elif isinstance(topic_tags, str):
        cleaned_topic_tags = _sanitize_generated_text(topic_tags, max_len=4000)
        topic_tags_json = cleaned_topic_tags or None

    embedding_json = None
    embedding_dim = None
    embedding_model = None
    embedding_status = "pending"
    emb = data.get("embedding")
    if isinstance(emb, list) and emb:
        try:
            embedding_json = json.dumps([float(x) for x in emb])
            embedding_dim = len(emb)
            embedding_model = str(data.get("embedding_model") or _MEMORY_EMBEDDING.model_name).strip()[:64]
            embedding_status = "ready"
        except Exception:
            embedding_json = None
            embedding_dim = None
            embedding_model = None
            embedding_status = "pending"

    item = None
    if item_id is not None:
        item = MemoryItem.query.filter_by(id=item_id, run_id=run_id, agent_user_id=agent_user_id).first()
    if item is None:
        item = MemoryItem(run_id=run_id, agent_user_id=agent_user_id, item_type=item_type, text=text_value[:4000])
        db.session.add(item)
    else:
        item.item_type = item_type
        item.text = text_value[:4000]

    item.metadata_json = metadata_json
    item.source_event_id = source_event_id
    item.thread_root_id = thread_root_id
    item.other_user_id = other_user_id
    item.topic_tags_json = topic_tags_json
    item.round_id = round_id
    item.importance = importance
    item.recency_anchor_round = recency_anchor_round if recency_anchor_round is not None else round_id
    item.last_accessed_round = last_accessed_round if last_accessed_round is not None else round_id
    item.access_count = access_count if access_count is not None else 0
    item.embedding_json = embedding_json
    item.embedding_dim = embedding_dim
    item.embedding_model = embedding_model
    item.embedding_status = embedding_status

    # Synchronous embedding if requested (cold-start optimization)
    force_sync = data.get("force_sync_embedding", False)
    if force_sync and item.embedding_status == "pending" and _MEMORY_EMBEDDING and _MEMORY_EMBEDDING.available:
        try:
            vec = _MEMORY_EMBEDDING.embed_text(item.text)
            if vec:
                item.embedding_json = json.dumps(vec)
                item.embedding_dim = len(vec)
                item.embedding_model = _MEMORY_EMBEDDING.model_name
                item.embedding_status = "ready"
        except Exception:
            pass  # Fall back to async indexer

    db.session.commit()
    return json.dumps({"status": 200, "id": item.id, "embedding_status": item.embedding_status})


@app.route("/memory/search", methods=["POST"])
def memory_search():
    """
    Semantic memory search over run-scoped memory stream.

    Score = w_rel * relevance + w_rec * recency + w_imp * importance.
    """
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        agent_user_id = int(data.get("agent_user_id"))
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "agent_user_id required"}), 400

    query_text = data.get("query_text")
    if not isinstance(query_text, str) or not query_text.strip():
        return json.dumps({"status": 400, "error": "query_text required"}), 400
    query_text = query_text.strip()
    query_text_normalized = _normalize_memory_query_text(query_text)
    query_variants = _build_memory_query_variants(query_text)

    def _to_int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    other_user_id = _to_int_or_none(data.get("other_user_id"))
    thread_root_id = _to_int_or_none(data.get("thread_root_id"))
    time_window_rounds = _to_int_or_none(data.get("time_window_rounds"))
    current_round = _to_int_or_none(data.get("round_id"))
    k = _to_int_or_none(data.get("k")) or 8
    max_chars = _to_int_or_none(data.get("max_chars")) or 1200
    include_evidence_tail = bool(data.get("include_evidence_tail", False))

    if k <= 0:
        k = 8
    if k > 40:
        k = 40
    if max_chars <= 0:
        max_chars = 1200
    if max_chars > 6000:
        max_chars = 6000

    types = data.get("types")
    if isinstance(types, str):
        types = [types]
    if not isinstance(types, list) or not types:
        types = ["event", "reflection", "summary"]
    types = [str(t).strip().lower() for t in types if str(t).strip()]
    allowed = {"event", "reflection", "summary"}
    types = [t for t in types if t in allowed]
    if not types:
        types = ["event", "reflection", "summary"]

    topic_tags_filter = data.get("topic_tags")
    if isinstance(topic_tags_filter, str):
        parsed_tags = _json_loads_maybe(topic_tags_filter)
        if isinstance(parsed_tags, list):
            topic_tags_filter = parsed_tags
        elif topic_tags_filter.strip():
            topic_tags_filter = [topic_tags_filter.strip()]
        else:
            topic_tags_filter = None
    if isinstance(topic_tags_filter, list):
        topic_tags_filter = {str(x).strip().lower() for x in topic_tags_filter if str(x).strip()}
    else:
        topic_tags_filter = None

    q = MemoryItem.query.filter(
        MemoryItem.run_id == run_id,
        MemoryItem.agent_user_id == agent_user_id,
        MemoryItem.item_type.in_(types),
    )
    if other_user_id is not None:
        q = q.filter(MemoryItem.other_user_id == other_user_id)
    if thread_root_id is not None:
        q = q.filter(MemoryItem.thread_root_id == thread_root_id)

    if current_round is None:
        latest = (
            MemoryItem.query.filter(
                MemoryItem.run_id == run_id,
                MemoryItem.agent_user_id == agent_user_id,
            )
            .order_by(desc(MemoryItem.round_id), desc(MemoryItem.id))
            .first()
        )
        if latest is not None and latest.round_id is not None:
            current_round = int(latest.round_id)

    if time_window_rounds is not None and time_window_rounds > 0 and current_round is not None:
        min_round = int(current_round) - int(time_window_rounds)
        q = q.filter((MemoryItem.round_id == None) | (MemoryItem.round_id >= min_round))  # noqa: E711

    candidates = q.order_by(desc(MemoryItem.round_id), desc(MemoryItem.id)).limit(300).all()

    if topic_tags_filter:
        filtered = []
        for item in candidates:
            parsed_tags = _json_loads_maybe(item.topic_tags_json)
            if not isinstance(parsed_tags, list):
                continue
            item_tags = {str(x).strip().lower() for x in parsed_tags if str(x).strip()}
            if item_tags & topic_tags_filter:
                filtered.append(item)
        candidates = filtered

    query_embedding = _MEMORY_EMBEDDING.embed_text(query_text)
    query_has_embedding = isinstance(query_embedding, list) and bool(query_embedding)

    results = []
    half_life_raw = data.get("recency_half_life_rounds")
    if half_life_raw is None:
        half_life_raw = app.config.get("MEMORY_RECENCY_HALF_LIFE_ROUNDS")
    if half_life_raw is None:
        half_life_raw = os.environ.get("MEMORY_RECENCY_HALF_LIFE_ROUNDS", "96")
    try:
        recency_half_life_rounds = float(half_life_raw)
    except Exception:
        recency_half_life_rounds = 96.0
    if recency_half_life_rounds <= 0.0:
        recency_half_life_rounds = 96.0
    recency_lambda = math.log(2.0) / recency_half_life_rounds

    w_rel = 0.55
    w_rec = 0.25
    w_imp = 0.20
    ready_count = 0
    pending_count = 0
    failed_count = 0
    lexical_candidates = []

    for item in candidates:
        if item.embedding_status == "ready":
            ready_count += 1
        elif item.embedding_status == "pending":
            pending_count += 1
        elif item.embedding_status == "failed":
            failed_count += 1

        item_text = (item.text or "").strip()
        item_text_norm = _normalize_memory_query_text(item_text)
        relevance = 0.0
        item_embedding = _json_loads_maybe(item.embedding_json)
        if query_has_embedding and isinstance(item_embedding, list):
            relevance = cosine_similarity(query_embedding, item_embedding)
        else:
            best_lexical = {"score": 0.0, "matched_terms": [], "query_variant": ""}
            for qv in query_variants:
                details = _lexical_match_details(qv, item_text_norm)
                if float(details.get("score") or 0.0) > float(best_lexical.get("score") or 0.0):
                    best_lexical = {
                        "score": float(details.get("score") or 0.0),
                        "matched_terms": details.get("matched_terms") or [],
                        "query_variant": qv,
                    }
            # Keep legacy lexical score as secondary floor for compatibility.
            legacy_lex = lexical_relevance(query_text, item_text)
            relevance = max(float(best_lexical.get("score") or 0.0), float(legacy_lex or 0.0))
            lexical_candidates.append(
                {
                    "item_id": int(item.id),
                    "score": float(relevance),
                    "round_id": item.round_id,
                    "thread_root_id": item.thread_root_id,
                    "matched_terms": best_lexical.get("matched_terms") or [],
                    "query_variant": best_lexical.get("query_variant") or "",
                    "text": item_text[:140],
                }
            )

        if current_round is not None and item.round_id is not None:
            delta = max(0, int(current_round) - int(item.round_id))
            recency = math.exp(-recency_lambda * float(delta))
        else:
            recency = 1.0

        try:
            importance = float(item.importance or 0.0)
        except Exception:
            importance = 0.0
        importance = max(0.0, min(1.0, importance))

        score = (w_rel * relevance) + (w_rec * recency) + (w_imp * importance)

        metadata = _json_loads_maybe(item.metadata_json)
        supporting_event_ids = []
        if isinstance(metadata, dict):
            se = metadata.get("supporting_event_ids")
            if isinstance(se, list):
                supporting_event_ids = [int(x) for x in se if isinstance(x, (int, float, str)) and str(x).isdigit()]
        if not supporting_event_ids and item.source_event_id is not None:
            supporting_event_ids = [int(item.source_event_id)]

        results.append(
            {
                "item": item,
                "score": float(score),
                "relevance": float(relevance),
                "recency": float(recency),
                "importance": float(importance),
                "supporting_event_ids": supporting_event_ids[:16],
            }
        )

    results.sort(key=lambda x: (x["score"], x["importance"], x["item"].id), reverse=True)
    top = results[:k]

    # Access bookkeeping so recency/access features can be extended later.
    for row in top:
        item = row["item"]
        try:
            item.access_count = int(item.access_count or 0) + 1
        except Exception:
            item.access_count = 1
        if current_round is not None:
            item.last_accessed_round = int(current_round)
    try:
        if top:
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    user_ids = {int(agent_user_id)}
    for row in top:
        item = row["item"]
        try:
            if item.other_user_id is not None:
                user_ids.add(int(item.other_user_id))
        except Exception:
            pass
        metadata = _json_loads_maybe(item.metadata_json)
        if isinstance(metadata, dict):
            for key in ["target_user_id", "actor_user_id"]:
                try:
                    val = metadata.get(key)
                    if val is not None:
                        user_ids.add(int(val))
                except Exception:
                    continue

    user_map = _build_user_map(user_ids)

    items_payload = []
    brief_lines = ["[MEMORY SEARCH BRIEF]"]
    for row in top:
        item = row["item"]
        text_raw = (item.text or "").strip()
        text_humanized = _humanize_memory_text(text_raw, user_map)
        brief_txt = text_humanized
        if len(brief_txt) > 280:
            brief_txt = brief_txt[:277].rstrip() + "..."
        rid = item.round_id if item.round_id is not None else "?"
        prefix = f"- ({item.item_type}, r{rid}, s={row['score']:.2f})"

        metadata = _json_loads_maybe(item.metadata_json)
        if include_evidence_tail and isinstance(metadata, dict):
            evidence = metadata.get("evidence_tail")
            if isinstance(evidence, list):
                for ev in evidence[-2:]:
                    if isinstance(ev, dict):
                        claim = str(ev.get("salient_claim") or "").strip()
                        if claim:
                            if len(claim) > 160:
                                claim = claim[:157].rstrip() + "..."
                            brief_lines.append("  evidence: " + claim)

        target_user_id = item.other_user_id
        actor_user_id = None
        target_post_id = None
        actor_post_id = None
        if isinstance(metadata, dict):
            try:
                target_user_id = (
                    int(metadata.get("target_user_id"))
                    if metadata.get("target_user_id") is not None
                    else target_user_id
                )
            except Exception:
                pass
            try:
                actor_user_id = (
                    int(metadata.get("actor_user_id"))
                    if metadata.get("actor_user_id") is not None
                    else None
                )
            except Exception:
                actor_user_id = None
            try:
                target_post_id = (
                    int(metadata.get("target_post_id"))
                    if metadata.get("target_post_id") is not None
                    else None
                )
            except Exception:
                target_post_id = None
            try:
                actor_post_id = (
                    int(metadata.get("actor_post_id"))
                    if metadata.get("actor_post_id") is not None
                    else None
                )
            except Exception:
                actor_post_id = None

        other_username = _normalize_username(user_map.get(item.other_user_id))
        target_username = _normalize_username(user_map.get(target_user_id))
        actor_username = _normalize_username(user_map.get(actor_user_id))
        if target_username is None:
            target_username = other_username

        label_bits = []
        if target_username:
            label_bits.append(f"target=@{target_username}")
        elif target_user_id is not None:
            label_bits.append(f"target_user_id={target_user_id}")
        if target_post_id is not None:
            label_bits.append(f"target_post_id={target_post_id}")
        if actor_username:
            label_bits.append(f"actor=@{actor_username}")
        if actor_post_id is not None:
            label_bits.append(f"actor_post_id={actor_post_id}")
        if item.thread_root_id is not None:
            label_bits.append(f"thread_root_id={item.thread_root_id}")
        if label_bits:
            prefix += " " + " ".join(label_bits)
        brief_lines.append(prefix + ": " + brief_txt)

        items_payload.append(
            {
                "item_id": item.id,
                "item_type": item.item_type,
                "text": item.text,
                "text_humanized": text_humanized,
                "score": row["score"],
                "relevance": row["relevance"],
                "recency": row["recency"],
                "importance": row["importance"],
                "round_id": item.round_id,
                "thread_root_id": item.thread_root_id,
                "other_user_id": item.other_user_id,
                "other_username": other_username,
                "target_user_id": target_user_id,
                "target_username": target_username,
                "actor_user_id": actor_user_id,
                "actor_username": actor_username,
                "target_post_id": target_post_id,
                "actor_post_id": actor_post_id,
                "supporting_event_ids": row["supporting_event_ids"],
            }
        )

    memory_brief = "\n".join(brief_lines).strip()
    if len(memory_brief) > max_chars:
        memory_brief = memory_brief[: max_chars - 3].rstrip() + "..."

    embedding_degraded = not query_has_embedding
    no_ready_candidates = ready_count <= 0
    degraded_mode = embedding_degraded  # backward compat: only true for real infra failure
    lexical_candidates.sort(
        key=lambda x: (float(x.get("score") or 0.0), int(x.get("item_id") or 0)),
        reverse=True,
    )
    if query_has_embedding and ready_count > 0:
        fallback_channel_used = "semantic"
    elif candidates:
        fallback_channel_used = "lexical"
    else:
        fallback_channel_used = "none"
    retrieval_meta = {
        "candidate_count": len(candidates),
        "returned_k": len(top),
        "degraded_mode": bool(degraded_mode),
        "embedding_degraded": bool(embedding_degraded),
        "no_ready_candidates": bool(no_ready_candidates),
        "normalization_applied": bool(query_text_normalized and query_text_normalized != query_text.lower().strip()),
        "query_variants": query_variants,
        "fallback_channel_used": fallback_channel_used,
        "lexical_top_candidates": lexical_candidates[:5],
        "scoring": {
            "w_rel": float(w_rel),
            "w_rec": float(w_rec),
            "w_imp": float(w_imp),
            "recency_half_life_rounds": float(recency_half_life_rounds),
            "recency_lambda": float(recency_lambda),
        },
        "embedding_status_summary": {
            "ready": int(ready_count),
            "pending": int(pending_count),
            "failed": int(failed_count),
            "query_embedding_available": bool(query_has_embedding),
        },
    }

    return json.dumps(
        {
            "status": 200,
            "run_id": run_id,
            "items": items_payload,
            "memory_brief": memory_brief,
            "retrieval_meta": retrieval_meta,
            "user_map": {str(k): v for k, v in user_map.items() if v},
        }
    )


@app.route("/memory/get_context", methods=["POST"])
def memory_get_context():
    """Fetch memory context for prompt injection (social card + thread card + digest)."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        agent_user_id = int(data.get("agent_user_id"))
    except (TypeError, ValueError):
        return json.dumps({"status": 400, "error": "agent_user_id required"}), 400

    def _to_int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    other_user_id = _to_int_or_none(data.get("other_user_id"))
    thread_root_id = _to_int_or_none(data.get("thread_root_id"))
    pair_limit = _to_int_or_none(data.get("pair_limit")) or 5
    if pair_limit <= 0:
        pair_limit = 5
    if pair_limit > 20:
        pair_limit = 20

    pair_rows = []
    if other_user_id is not None:
        q = (
            MemoryInteractionEvent.query.filter(MemoryInteractionEvent.run_id == run_id)
            .filter(
                (
                    (MemoryInteractionEvent.actor_user_id == agent_user_id)
                    & (MemoryInteractionEvent.target_user_id == other_user_id)
                )
                | (
                    (MemoryInteractionEvent.actor_user_id == other_user_id)
                    & (MemoryInteractionEvent.target_user_id == agent_user_id)
                )
            )
            .order_by(desc(MemoryInteractionEvent.id))
            .limit(pair_limit)
        )
        pair_rows = q.all()[::-1]

    username_ids = {int(agent_user_id)}
    if other_user_id is not None:
        username_ids.add(int(other_user_id))
    for ev in pair_rows:
        try:
            if ev.actor_user_id is not None:
                username_ids.add(int(ev.actor_user_id))
            if ev.target_user_id is not None:
                username_ids.add(int(ev.target_user_id))
        except Exception:
            continue
    user_map = _build_user_map(username_ids)
    other_username = _normalize_username(user_map.get(other_user_id))

    social_card_payload = None
    if other_user_id is not None:
        sc = MemorySocialCard.query.filter_by(
            run_id=run_id, agent_user_id=agent_user_id, other_user_id=other_user_id
        ).first()
        if sc is not None:
            social_card_payload = {
                "affinity": sc.affinity,
                "conflict": sc.conflict,
                "humor": sc.humor,
                "trust": sc.trust,
                "last_relation_label": sc.last_relation_label,
                "last_round_id": sc.last_round_id,
                "last_thread_root_id": sc.last_thread_root_id,
                "last_updated_round": sc.last_updated_round,
                "event_count": sc.event_count,
                "summary_text": sc.summary_text,
                "evidence_tail": sc.evidence_tail_json,
                "other_username": other_username,
            }

    thread_card_payload = None
    if thread_root_id is not None:
        tc = MemoryThreadCard.query.filter_by(
            run_id=run_id, agent_user_id=agent_user_id, thread_root_id=thread_root_id
        ).first()
        if tc is not None:
            thread_card_payload = {
                "gist_text": tc.gist_text,
                "my_role": tc.my_role,
                "participants_top": tc.participants_top_json,
                "entry_points": tc.entry_points_json,
                "last_seen_round_id": tc.last_seen_round_id,
            }

    digest_payload = None
    digest = (
        MemoryCommunityDigest.query.filter_by(run_id=run_id)
        .order_by(desc(MemoryCommunityDigest.id))
        .first()
    )
    if digest is not None:
        digest_payload = {
            "round_id": digest.round_id,
            "digest_text": digest.digest_text,
            "top_topics": digest.top_topics_json,
            "norms": digest.norms_json,
            "memes": digest.memes_json,
            "polarizing_issues": digest.polarizing_issues_json,
        }

    recent_pair_events = []
    for ev in pair_rows:
        recent_pair_events.append(
            {
                "round_id": ev.round_id,
                "actor_user_id": ev.actor_user_id,
                "actor_username": _normalize_username(user_map.get(ev.actor_user_id)),
                "target_user_id": ev.target_user_id,
                "target_username": _normalize_username(user_map.get(ev.target_user_id)),
                "event_type": ev.event_type,
                "relation_label": ev.relation_label,
                "tone_label": ev.tone_label,
                "thread_root_id": ev.thread_root_id,
                "target_post_id": ev.target_post_id,
                "salient_claim": ev.salient_claim,
            }
        )

    return json.dumps(
        {
            "status": 200,
            "run_id": run_id,
            "user_map": {str(k): v for k, v in user_map.items() if v},
            "other_username": other_username,
            "social_card": social_card_payload,
            "thread_card": thread_card_payload,
            "community_digest": digest_payload,
            "recent_pair_events": recent_pair_events,
        }
    )


@app.route("/memory/events_recent", methods=["POST"])
def memory_events_recent():
    """Return the most recent interaction events for a run (for digest building)."""
    _ensure_memory_schema()
    data = json.loads(request.get_data() or "{}")

    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return json.dumps({"status": 400, "error": "run_id required"}), 400

    try:
        limit = int(data.get("limit", 80))
    except Exception:
        limit = 80
    if limit <= 0:
        limit = 80
    if limit > 200:
        limit = 200

    q = (
        MemoryInteractionEvent.query.filter(MemoryInteractionEvent.run_id == run_id)
        .order_by(desc(MemoryInteractionEvent.id))
        .limit(limit)
    )

    events = []
    for ev in q.all()[::-1]:
        events.append(
            {
                "round_id": ev.round_id,
                "actor_user_id": ev.actor_user_id,
                "target_user_id": ev.target_user_id,
                "event_type": ev.event_type,
                "relation_label": ev.relation_label,
                "tone_label": ev.tone_label,
                "thread_root_id": ev.thread_root_id,
                "target_post_id": ev.target_post_id,
                "salient_claim": ev.salient_claim,
                "importance": ev.importance,
                "event_text": ev.event_text,
            }
        )

    return json.dumps({"status": 200, "run_id": run_id, "events": events})


@app.route("/get_post_topics_name", methods=["GET", "POST"])
def get_post_topics_name():
    """
    Get the topics of a post.

    :return: a json object with the topics
    """
    data = json.loads(request.get_data())
    post_id = data["post_id"]

    post = Post.query.filter_by(id=post_id).first()
    topic_post_id = post_id
    if post is not None:
        direct_topics = Post_topics.query.filter_by(post_id=post_id).all()
        if not direct_topics and post.thread_id is not None:
            topic_post_id = post.thread_id
    post_topics = Post_topics.query.filter_by(post_id=topic_post_id).all()

    res = []
    for topic in post_topics:
        tp = Interests.query.filter_by(iid=topic.topic_id).first()
        if tp is not None:
            res.append(tp.interest)

    return json.dumps(res)


@app.route("/get_sentiment", methods=["POST", "GET"])
def get_sentiment():
    """
    Get the sentiment of a post.

    :return: a json object with the sentiment
    """
    data = json.loads(request.get_data())
    user_id = data["user_id"]
    interests = data["interests"]

    res = []

    for interest in interests:
        topic = Interests.query.filter_by(interest=interest).first()
        if topic is None:
            continue
        post_sentiment = (
            Post_Sentiment.query.filter_by(user_id=user_id, topic_id=topic.iid)
            .order_by(desc(Post_Sentiment.id))
            .first()
        )
        if post_sentiment is not None:
            # thresholding compound
            if post_sentiment.compound > 0.05:
                sentiment = "positive"
            elif post_sentiment.compound < -0.05:
                sentiment = "negative"
            else:
                sentiment = "neutral"
            res.append({"topic": interest, "sentiment": sentiment})

    return json.dumps(res)


@app.route(
    "/get_post",
    methods=["POST", "GET"],
)
def get_post():
    """
    Get the post.

    :return: a json object with the post
    """
    data = json.loads(request.get_data())
    post_id = data["post_id"]

    post = Post.query.filter_by(id=post_id).first()

    return json.dumps(post.tweet)


@app.route(
    "/reaction",
    methods=["POST"],
)
def add_reaction():
    """
    Add a reaction to a post/comment.

    :return: a json object with the status of the reaction
    """
    data = json.loads(request.get_data())
    account_id = data["user_id"]
    post_id = data["post_id"]
    rtype = data["type"]
    tid = int(data["tid"])

    user = User_mgmt.query.filter_by(id=account_id).first()

    react = Reactions(post_id=post_id, user_id=user.id, round=tid, type=rtype)

    db.session.add(react)
    try:
        db.session.commit()
    except:
        pass

    # get compound sentiment of post
    post_sentiment = Post_Sentiment.query.filter_by(post_id=int(post_id)).all()
    for topic_sentiment in post_sentiment:
        topic_id = topic_sentiment.topic_id
        compound = topic_sentiment.compound
        # thresholding compound
        if compound > 0.05:
            sentiment = "pos"
        elif compound < -0.05:
            sentiment = "neg"
        else:
            sentiment = "neu"

        # create reaction sentiment
        reaction_sentiment = Post_Sentiment(
            post_id=post_id,
            user_id=user.id,
            pos=0 if rtype == "dislike" else 1,
            neg=0 if rtype == "like" else 1,
            neu=0,
            compound=1 if rtype == "like" else -1,
            sentiment_parent=sentiment,
            round=tid,
            is_reaction=1,
            topic_id=topic_id,
        )
        db.session.add(reaction_sentiment)
        db.session.commit()

    # increment the post's reaction count
    post = Post.query.filter_by(id=post_id).first()
    if post is not None:
        post.reaction_count += 1
        db.session.commit()

    return json.dumps({"status": 200})


@app.route("/get_post_topics", methods=["GET"])
def get_post_topics():
    """
    Get the topics of a post.

    :return: a json object with the topics
    """
    data = json.loads(request.get_data())
    post_id = data["post_id"]

    post = Post.query.filter_by(id=post_id).first()
    topic_post_id = post_id
    if post is not None:
        direct_topics = Post_topics.query.filter_by(post_id=post_id).all()
        if not direct_topics and post.thread_id is not None:
            topic_post_id = post.thread_id

    post_topics = Post_topics.query.filter_by(post_id=topic_post_id)

    res = []
    for topic in post_topics:
        res.append(topic.topic_id)

    return json.dumps(res)


@app.route("/get_thread_root", methods=["GET"])
def get_thread_root():
    """
    Get the root of a thread.

    :return: a json object with the root
    """
    data = json.loads(request.get_data())
    post_id = data["post_id"]

    post = Post.query.filter_by(id=post_id).first()

    if post is None:
        return json.dumps({"status": 404})

    return json.dumps(post.thread_id)
