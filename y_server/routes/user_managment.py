import json
from flask import request
from y_server import app, db
from sqlalchemy import desc, func, inspect
from y_server.modals import (
    Agent_Custom_Feature,
    Agent_Opinion,
    Follow,
    Interests,
    Post,
    Reactions,
    Rounds,
    User_interest,
    User_mgmt,
)


def _normalize_custom_features_payload(raw_features):
    normalized = []
    if isinstance(raw_features, dict):
        for key, value in raw_features.items():
            feature_key = str(key or "").strip()
            if not feature_key:
                continue
            normalized.append(
                {
                    "feature_type": "custom",
                    "key": feature_key,
                    "value": "" if value is None else str(value),
                }
            )
        return normalized
    if not isinstance(raw_features, list):
        return normalized
    for item in raw_features:
        if not isinstance(item, dict):
            continue
        feature_key = str(item.get("key") or "").strip()
        if not feature_key:
            continue
        normalized.append(
            {
                "feature_type": str(item.get("feature_type") or "custom").strip() or "custom",
                "key": feature_key,
                "value": "" if item.get("value") is None else str(item.get("value")),
            }
        )
    return normalized


def _normalize_stubborn_topics(raw_stubborn_topics):
    if isinstance(raw_stubborn_topics, dict):
        return {
            str(topic).strip()
            for topic, is_stubborn in raw_stubborn_topics.items()
            if str(topic).strip() and bool(is_stubborn)
        }
    if isinstance(raw_stubborn_topics, (list, tuple, set)):
        return {str(topic).strip() for topic in raw_stubborn_topics if str(topic).strip()}
    return set()


def _latest_agent_opinion(agent_id, topic_id):
    return (
        Agent_Opinion.query.filter_by(agent_id=agent_id, topic_id=topic_id)
        .order_by(Agent_Opinion.tid.desc(), Agent_Opinion.id.desc())
        .first()
    )


def _ensure_agent_opinion_schema():
    try:
        table_names = set(inspect(db.engine).get_table_names())
        if "agent_opinion" in table_names:
            return
    except Exception:
        pass

    try:
        db.create_all()
    except Exception:
        pass


@app.route("/get_user_id", methods=["GET", "POST"])
def get_user_id():
    """
    Get the user id.

    :return: a json object with the user id
    """
    data = json.loads(request.get_data())
    username = data["username"]

    user = User_mgmt.query.filter_by(username=username).first()
    if user is None:
        return json.dumps({"id": None})

    return json.dumps({"id": user.id})


@app.route("/get_user", methods=["POST"])
def get_user():
    """
    Get user information.

    :return: a json object with the user information
    """
    data = json.loads(request.get_data())
    username = data["username"]
    email = data["email"]

    user = User_mgmt.query.filter_by(username=username, email=email).first()
    
    if user is None:
        return json.dumps({"error": "User not found"}), 404

    return json.dumps(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "leaning": user.leaning,
            "age": int(user.age),
            "user_type": user.user_type,
            "password": user.password,
            "ag": user.ag,
            "ne": user.ne,
            "ex": user.ex,
            "co": user.co,
            "oe": user.oe,
            "rec_sys": user.recsys_type,
            "language": user.language,
            "education_level": user.education_level,
            "joined_on": user.joined_on,
            "owner": user.owner,
            "round_actions": user.round_actions,
            "frec_sys": user.frecsys_type,
            "gender": user.gender,
            "nationality": user.nationality,
            "toxicity": user.toxicity,
            "is_page": user.is_page,
            "daily_activity_level": user.daily_activity_level if user.daily_activity_level is not None else 1,
            "profession": user.profession if hasattr(user, 'profession') else "",
        }
    )


@app.route("/register", methods=["POST"])
def register():
    """
    Register a new user.

    :return: a json object with the status of the registration
    """
    data = json.loads(request.get_data())

    username = data["name"]
    email = data["email"]
    password = data["password"]

    leaning = data["leaning"]
    age = int(data["age"])
    user_type = data["user_type"]
    oe = data["oe"]
    co = data["co"]
    ex = data["ex"]
    ag = data["ag"]
    ne = data["ne"]
    recsys_type = "default"
    language = data["language"]
    education_level = data["education_level"]
    joined_on = int(data["joined_on"])
    round_actions = int(data["round_actions"])
    owner = data["owner"]
    gender = data["gender"]
    nationality = data["nationality"]
    toxicity = data["toxicity"]
    if "daily_activity_level" in data: # @todo: implement this in YRedditClient
        daily_activity_level = data["daily_activity_level"]
    else:
        daily_activity_level = 1

    if "daily_activity_level" in data:  # @todo: implement this in YRedditClient
        profession = data["profession"]
    else:
        profession = "unknown"

    if "is_page" in data:
        is_page = data["is_page"]
    else:
        is_page = 0

    user = User_mgmt.query.filter_by(username=data["name"], email=data["email"]).first()

    if user is None:
        user = User_mgmt(
            username=username,
            email=email,
            password=password,
            leaning=leaning,
            age=age,
            user_type=user_type,
            oe=oe,
            co=co,
            ex=ex,
            ag=ag,
            ne=ne,
            recsys_type=recsys_type,
            language=language,
            education_level=education_level,
            joined_on=joined_on,
            round_actions=round_actions,
            owner=owner,
            gender=gender,
            nationality=nationality,
            toxicity=toxicity,
            is_page=is_page,
            daily_activity_level=daily_activity_level,
            profession=profession,
        )
        db.session.add(user)
        try:
            db.session.commit()
        except:
            return json.dumps({"status": 404})

    return json.dumps({"status": 200})


@app.route("/churn", methods=["POST"])
def churn_agents():
    """
    Churn users that do not post for a while.

    :return:
    """

    data = json.loads(request.get_data())
    left_on = data["left_on"]

    if "user_id" in data:
        user = User_mgmt.query.filter_by(id=data["user_id"]).first()
        if user is None:
            return json.dumps({"status": 404, "removed": {}})
        user.left_on = left_on
        db.session.commit()
        return json.dumps({"status": 200, "removed": {str(user.id): None}})

    n_users = data["n_users"]

    #  get the max round value from the post table for each user
    query = (
        (
            db.session.query(Post.user_id, db.func.max(Post.round))
            .join(User_mgmt, Post.user_id == User_mgmt.id)
            .filter(User_mgmt.left_on.is_(None), User_mgmt.is_page == 0)
            .group_by(Post.user_id)
        )
        .order_by(db.func.max(Post.round).asc())
        .limit(n_users)
    )

    results = query.all()

    removed = {}
    for user_id, _ in results:
        user = User_mgmt.query.filter_by(id=user_id).first()
        user.left_on = left_on
        db.session.commit()
        removed[user_id] = None

    return json.dumps({"status": 200, "removed": removed})


@app.route("/update_user", methods=["POST"])
def update_user():
    """
    Update user information.

    :return: a json object with the status of the update
    """
    data = json.loads(request.get_data())

    user = User_mgmt.query.filter_by(
        username=data["username"], email=data["email"]
    ).first()

    if user is not None:
        if "recsys_type" in data:
            recsys_type = data["recsys_type"]
            user.recsys_type = recsys_type
            db.session.commit()

        if "frecsys_type" in data:
            frecsys_type = data["frecsys_type"]
            user.frecsys_type = frecsys_type
            db.session.commit()

    return json.dumps({"status": 200})


@app.route("/user_exists", methods=["POST"])
def user_exists():
    """
    Check if the user exists.

    :return: a json object with the status of the user
    """
    data = json.loads(request.get_data())
    user = User_mgmt.query.filter_by(username=data["name"], email=data["email"]).first()

    if user is None:
        return json.dumps({"status": 404})

    return json.dumps({"status": 200, "id": user.id})


@app.route(
    "/get_user_from_post",
    methods=["POST", "GET"],
)
def get_user_from_post():
    """
    Get the author (username) of a post.

    :return: a json string with the author's username
    """
    data = json.loads(request.get_data())
    post_id = data["post_id"]
    post = Post.query.filter_by(id=post_id).first()

    if post is None:
        return json.dumps({"error": "Post not found", "status": 404})

    # Return username instead of user_id so agents can address each other by name
    user = User_mgmt.query.filter_by(id=post.user_id).first()
    if user is None:
        return json.dumps({"error": "User not found", "status": 404})

    return json.dumps(user.username)


@app.route(
    "/get_username_from_post",
    methods=["POST", "GET"],
)
def get_username_from_post():
    """
    Get the author (user id + username) of a post/comment.

    :return: a json object with status, user_id, username
    """
    data = json.loads(request.get_data())
    post_id = data.get("post_id")

    try:
        post_id = int(post_id)
    except (TypeError, ValueError):
        return json.dumps({"error": "Invalid post_id", "status": 400})

    post = Post.query.filter_by(id=post_id).first()
    if post is None:
        return json.dumps({"error": "Post not found", "status": 404})

    user = User_mgmt.query.filter_by(id=post.user_id).first()
    if user is None:
        return json.dumps({"error": "User not found", "status": 404})

    return json.dumps({"status": 200, "user_id": int(user.id), "username": user.username})


@app.route("/timeline", methods=["GET"])
def get_timeline():
    """
    Get the timeline of a user.

    :return: a json object with the timeline
    """
    data = json.loads(request.get_data())
    user_id = data["user_id"]

    user = User_mgmt.query.filter_by(id=user_id).first()
    all_posts = Post.query.filter_by(user_id=user.id).order_by(desc(Post.id))
    res = []
    for post in all_posts:
        res.append(
            {
                "post_id": post.id,
                "post": post.tweet,
                "round": post.round,
                "reposts": len(list(Post.query.filter_by(shared_from=post.id))),
                "likes": len(list(Reactions.query.filter_by(post_id=post.id, type="like"))),
                "dislikes": len(
                    list(Reactions.query.filter_by(post_id=post.id, type="dislike"))
                ),
                "comments": len(list(Post.query.filter_by(comment_to=post.id))),
            }
        )

    return json.dumps(res)


@app.route("/set_interests", methods=["POST"])
def set_interests():
    """
    Set the interests of a user.

    :return: a json object with the status of the update
    """
    data = json.loads(request.get_data())

    for interest in data:
        ints = Interests(
            interest=interest,
        )
        db.session.add(ints)
        db.session.commit()

    return json.dumps({"status": 200})


@app.route("/set_user_interests", methods=["POST"])
def set_user_interests():
    """
    Set the interests of a user.

    :return: a json object with the status of the update
    """
    data = json.loads(request.get_data())
    user_id = data["user_id"]
    interests = data["interests"]
    round_id = data["round"]

    for interest in interests:
        # check if the interest is specified as id or by name
        iid = None
        if isinstance(interest, str):
            try:
                iid = Interests.query.filter_by(interest=interest).first().iid
            except:
                # add interest to the interest table
                ints = Interests(
                    interest=interest,
                )
                db.session.add(ints)
                db.session.commit()
                iid = Interests.query.filter_by(interest=interest).first().iid

        else:
            iid = interest

        user_interest = User_interest(
            user_id=user_id, interest_id=iid, round_id=round_id
        )
        db.session.add(user_interest)
        db.session.commit()

    return json.dumps({"status": 200})


@app.route("/get_user_interests", methods=["GET"])
def get_user_interests():
    """
    Get the interests of a user.

    :return: a json object with the interests
    """
    data = json.loads(request.get_data())
    user_id = int(data["user_id"])
    round_id = int(data["round_id"])
    n_interests = int(data["n_interests"])
    time_window = int(data["time_window"])
    base_rounds = max(0, round_id - time_window)

    # get the top n_interests interests of the user in the time window
    interests = (
        db.session.query(
            User_interest.interest_id,
            Interests.interest,
            db.func.count(User_interest.interest_id).label("count"),
        )
        .join(Interests, User_interest.interest_id == Interests.iid)
        .filter(
            User_interest.user_id == user_id,
            User_interest.round_id >= base_rounds,
            User_interest.round_id <= round_id,
        )
        .group_by(User_interest.interest_id, Interests.interest)
        .order_by(db.desc(db.func.count(User_interest.interest_id)))
        .limit(n_interests)
        .all()
    )

    res = []
    for interest in interests:
        res.append({"id": int(interest[0]), "topic": interest.interest})

    return json.dumps(res)


@app.route("/get_user_opinions", methods=["POST"])
def get_user_opinions():
    """
    Get the latest opinions of a user mapped to interest names.

    :return: a json object with opinions {interest_name: [opinion_value, topic_id]}
    """
    _ensure_agent_opinion_schema()
    data = json.loads(request.get_data())
    user_id = int(data["user_id"])

    subq = (
        db.session.query(
            Agent_Opinion.topic_id,
            func.max(Agent_Opinion.tid).label("max_tid"),
        )
        .filter(Agent_Opinion.agent_id == user_id)
        .group_by(Agent_Opinion.topic_id)
        .subquery()
    )

    rows = (
        db.session.query(Interests.interest, Interests.iid, Agent_Opinion.opinion)
        .join(
            subq,
            (Agent_Opinion.topic_id == subq.c.topic_id)
            & (Agent_Opinion.tid == subq.c.max_tid),
        )
        .join(Interests, Agent_Opinion.topic_id == Interests.iid)
        .filter(Agent_Opinion.agent_id == user_id)
        .all()
    )

    res = {row.interest: [float(row.opinion), int(row.iid)] for row in rows}
    return json.dumps(res)


@app.route("/get_users_opinions", methods=["POST"])
def get_users_opinions():
    """
    Get the latest opinions of followed users for a given topic.

    :return: a json array with opinion values
    """
    _ensure_agent_opinion_schema()
    data = json.loads(request.get_data())
    user_id = int(data["user_id"])
    topic = str(data["topic"])

    interest = Interests.query.filter_by(interest=topic).first()
    if interest is None:
        return json.dumps([])
    target_topic_id = int(interest.iid)

    followee_ids = [
        f.follower_id
        for f in Follow.query.filter_by(user_id=user_id, action="follow").all()
    ]
    if not followee_ids:
        return json.dumps([])

    subq = (
        db.session.query(
            Agent_Opinion.agent_id,
            func.max(Agent_Opinion.tid).label("max_tid"),
        )
        .filter(
            Agent_Opinion.topic_id == target_topic_id,
            Agent_Opinion.agent_id.in_(followee_ids),
        )
        .group_by(Agent_Opinion.agent_id)
        .subquery()
    )

    rows = (
        db.session.query(Agent_Opinion.opinion)
        .join(
            subq,
            (Agent_Opinion.agent_id == subq.c.agent_id)
            & (Agent_Opinion.tid == subq.c.max_tid),
        )
        .filter(
            Agent_Opinion.topic_id == target_topic_id,
            Agent_Opinion.agent_id.in_(followee_ids),
        )
        .all()
    )

    return json.dumps([float(row.opinion) for row in rows])


@app.route("/set_user_opinions", methods=["POST"])
def set_user_opinions():
    """
    Store topic opinions for a user for a given round.

    :return: a json object with the status of the update
    """
    _ensure_agent_opinion_schema()
    data = json.loads(request.get_data())

    agent_id = int(data.get("user_id"))
    opinions = data.get("opinions", {})
    tid = int(data.get("round"))
    id_interacted_with = int(data.get("id_interacted_with", -1))
    id_post = int(data.get("id_post", -1))
    stubborn_topics = _normalize_stubborn_topics(data.get("stubborn_topics"))

    try:
        for topic_id, opinion_value in opinions.items():
            resolved_topic_id = topic_id
            if isinstance(topic_id, str):
                try:
                    resolved_topic_id = int(topic_id)
                    interest = Interests.query.filter_by(iid=resolved_topic_id).first()
                    if interest is None:
                        raise ValueError(f"Interest ID {resolved_topic_id} does not exist.")
                except Exception:
                    interest = Interests.query.filter_by(interest=topic_id).first()
                    if interest is None:
                        interest = Interests(interest=topic_id)
                        db.session.add(interest)
                        db.session.commit()
                    resolved_topic_id = int(interest.iid)

            latest_opinion = _latest_agent_opinion(agent_id, int(resolved_topic_id))
            is_stubborn = bool(latest_opinion.stubborn) if latest_opinion is not None else False
            interest_name = Interests.query.filter_by(iid=int(resolved_topic_id)).with_entities(
                Interests.interest
            ).scalar()
            if interest_name and interest_name in stubborn_topics:
                is_stubborn = True
            stored_opinion = (
                float(latest_opinion.opinion)
                if latest_opinion is not None and bool(latest_opinion.stubborn)
                else float(opinion_value)
            )

            db.session.add(
                Agent_Opinion(
                    agent_id=agent_id,
                    tid=tid,
                    topic_id=int(resolved_topic_id),
                    id_interacted_with=id_interacted_with,
                    id_post=id_post,
                    opinion=stored_opinion,
                    stubborn=1 if is_stubborn else 0,
                )
            )

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return json.dumps({"status": 400, "error": str(exc)}), 400

    return json.dumps({"status": 200})


@app.route("/set_user_custom_features", methods=["POST"])
def set_user_custom_features():
    data = json.loads(request.get_data())
    user_id = int(data.get("user_id"))
    features = _normalize_custom_features_payload(data.get("custom_features"))

    try:
        Agent_Custom_Feature.query.filter_by(user_id=user_id).delete()
        for feature in features:
            db.session.add(
                Agent_Custom_Feature(
                    user_id=user_id,
                    feature_type=feature["feature_type"],
                    key=feature["key"],
                    value=feature["value"],
                )
            )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return json.dumps({"status": 400, "error": str(exc)}), 400

    return json.dumps({"status": 200})


@app.route("/get_user_custom_features", methods=["POST"])
def get_user_custom_features():
    data = json.loads(request.get_data())
    user_id = int(data.get("user_id"))
    rows = Agent_Custom_Feature.query.filter_by(user_id=user_id).all()
    return json.dumps(
        [
            {
                "feature_type": row.feature_type,
                "key": row.key,
                "value": row.value,
            }
            for row in rows
        ]
    )
