import json
import re
from flask import request
from y_server import app, db
from y_server.modals import (
    Images,
    Post,
    Emotions,
    Post_emotions,
    Hashtags,
    Post_hashtags,
    Post_Sentiment,
)

from y_server.content_analysis import (
    should_annotate_emotions,
    should_annotate_sentiment,
    should_annotate_toxicity,
    vader_sentiment,
    toxicity,
)


_PROMPT_SCAFFOLD_PATTERNS = [
    re.compile(r"\bmemory tier [abc]\b", re.IGNORECASE),
    re.compile(r"\bmemory context\b", re.IGNORECASE),
    re.compile(r"\bmemory search brief\b", re.IGNORECASE),
    re.compile(r"\bi am the handler\b", re.IGNORECASE),
    re.compile(r"\bwrite a new caption\b", re.IGNORECASE),
]


def _is_prompt_scaffold(text_value):
    text = str(text_value or "").strip()
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return any(pattern.search(normalized) for pattern in _PROMPT_SCAFFOLD_PATTERNS)


def _sanitize_generated_text(text_value):
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
        if _is_prompt_scaffold(line):
            continue
        if line.lower().startswith("previous bad attempt:"):
            continue
        lines.append(raw_line)
    cleaned = "\n".join(lines).strip()
    if cleaned and _is_prompt_scaffold(cleaned):
        return ""
    return cleaned


@app.route("/comment_image", methods=["POST"])
def post_image():
    """
    Comment on an image.

    :return:
    """
    data = json.loads(request.get_data())
    account_id = int(data["user_id"])
    text = _sanitize_generated_text(data["text"].strip('"'))
    if not text:
        return json.dumps({"status": 422, "error": "prompt_scaffold_detected", "field": "text"}), 422
    emotions = data["emotions"]
    hashtags = data["hashtags"]
    tid = int(data["tid"])
    image_url = data["image_url"]
    image_description = data["image_description"]
    try:
        article_id = int(data["article_id"])
    except:
        article_id = None

    # check if image exists
    image = Images.query.filter_by(url=image_url).first()
    if image is None:
        image = Images(
            url=image_url,
            description=image_description,
            article_id=article_id,
        )
        db.session.add(image)
        db.session.commit()

    # get image id
    image_id = Images.query.filter_by(url=image_url).first()

    post = Post(
        tweet=text,
        round=tid,
        user_id=account_id,
        image_id=image_id.id,
        comment_to=-1,
    )

    db.session.add(post)
    db.session.commit()

    if should_annotate_toxicity(app.config):
        toxicity(text, app.config.get("perspective_api"), post.id, db, enabled=True)
    if should_annotate_sentiment(app.config):
        sentiment = vader_sentiment(text)
        post_sentiment = Post_Sentiment(
            post_id=post.id,
            user_id=account_id,
            pos=sentiment["pos"],
            neg=sentiment["neg"],
            neu=sentiment["neu"],
            compound=sentiment["compound"],
            round=tid,
            is_post=1,
            topic_id=-1,
        )
        db.session.add(post_sentiment)
        db.session.commit()

    post.thread_id = post.id
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

    for tag in hashtags:
        if len(tag) < 1:
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

    return json.dumps({"status": 200})
