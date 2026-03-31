import json
import re
from flask import request
from y_server import app, db
from y_server.modals import (
    Post,
    User_mgmt,
    Emotions,
    Post_emotions,
    Hashtags,
    Post_hashtags,
    Mentions,
    Articles,
    Websites,
    Interests,
    Article_topics,
    Post_topics,
    Post_Sentiment,
    Images,
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


@app.route("/news", methods=["POST"])
def comment_news():
    """
    Comment on a news article.

    :return: a json object with the status of the comment
    """
    data = json.loads(request.get_data())
    account_id = data["user_id"]
    raw_text = data["tweet"].strip('"')
    text = _sanitize_generated_text(raw_text)
    if raw_text.strip() and not text:
        return json.dumps({"status": 422, "error": "prompt_scaffold_detected", "field": "tweet"}), 422
    emotions = data["emotions"]
    hastags = data["hashtags"]
    mentions = data["mentions"]
    tid = int(data["tid"])
    title = data["title"]
    summary = data["summary"]
    link = data["link"]
    publisher = data["publisher"]
    rss = data["rss"]
    leaning = data["leaning"]
    country = data["country"]
    language = data["language"]
    category = data["category"]
    fetched_on = data["fetched_on"]

    user = User_mgmt.query.filter_by(id=account_id).first()

    # check if website exists
    website = Websites.query.filter_by(rss=rss).first()
    if website is None:
        website = Websites(
            name=publisher,
            rss=rss,
            leaning=leaning,
            category=category,
            language=language,
            country=country,
            last_fetched=fetched_on,
        )
        db.session.add(website)
        db.session.commit()

    website_id = Websites.query.filter_by(rss=rss).first().id

    # check if article exists
    article = Articles.query.filter_by(link=link, website_id=website_id).first()
    if article is None:
        article = Articles(
            title=title,
            summary=summary,
            link=link,
            website_id=website_id,
            fetched_on=fetched_on,
        )
        db.session.add(article)
        db.session.commit()
    article_id = Articles.query.filter_by(link=link, website_id=website_id).first().id

    # Handle image_url if provided
    image_url = data.get("image_url")
    image_id = None
    if image_url:
        # Check if image already exists for this article
        existing_image = Images.query.filter_by(article_id=article_id).first()
        if existing_image is None:
            # Also check if image URL already exists (avoid duplicates)
            if Images.query.filter_by(url=image_url).first() is None:
                image = Images(url=image_url, article_id=article_id)
                db.session.add(image)
                db.session.commit()
                image_id = image.id
            else:
                # Image URL exists, get its ID
                image_id = Images.query.filter_by(url=image_url).first().id
        else:
            image_id = existing_image.id
    else:
        # No image_url provided, check if article already has an image
        existing_image = Images.query.filter_by(article_id=article_id).first()
        if existing_image:
            image_id = existing_image.id

    # add post only if the text is not empty
    # (this might happen if the method is called to save the article for image processing)
    if len(text) == 0:
        post = None
    else:
        # Idempotency for link shares: prevent accidental double-posting of the same link
        # by the same user within the same round.
        if data.get("is_share_link"):
            existing = Post.query.filter_by(
                user_id=user.id,
                round=tid,
                comment_to=-1,
                news_id=article_id,
            ).first()
            if existing is not None:
                return json.dumps(
                    {
                        "status": 200,
                        "article_id": article_id,
                        "post_id": existing.id,
                        "message": "duplicate_suppressed",
                    }
                )

        post = Post(
            tweet=text,
            round=tid,
            user_id=user.id,
            comment_to=-1,
            news_id=article_id,
            image_id=image_id,
        )

        db.session.add(post)
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
            if us is not None:
                mention = Mentions(user_id=us.id, post_id=post.id, round=tid)
                db.session.add(mention)
                db.session.commit()

    if post is not None and "topics" in data:
        if should_annotate_toxicity(app.config):
            toxicity(text, app.config.get("perspective_api"), post.id, db, enabled=True)
        sentiment = vader_sentiment(text) if should_annotate_sentiment(app.config) else None

        for topic in data["topics"]:
            if len(topic) < 1:
                continue

            interests = Interests.query.filter_by(interest=topic).first()
            if interests is None:
                interests = Interests(interest=topic)
                db.session.add(interests)
                db.session.commit()

            interests = Interests.query.filter_by(interest=topic).first()

            at = Article_topics.query.filter_by(
                article_id=article_id, topic_id=interests.iid
            ).first()
            if at is None:
                at = Article_topics(article_id=article_id, topic_id=interests.iid)
                db.session.add(at)

            pt = Post_topics(post_id=post.id, topic_id=interests.iid)
            db.session.add(pt)

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
                    topic_id=interests.iid,
                )
                db.session.add(post_sentiment)
                db.session.commit()

    resp = {"status": 200, "article_id": article_id}
    if post is not None:
        resp["post_id"] = post.id
    return json.dumps(resp)


@app.route("/get_article_by_title", methods=["POST", "GET"])
def article_by_title():
    """
    Get the news article by title.

    :return: a json object with the article
    """
    data = json.loads(request.get_data())
    title = data["title"]

    # get article from title
    article = Articles.query.filter_by(title=title).first()
    if article is not None:
        return json.dumps({"article_id": article.news_id})
    else:
        return json.dumps({"status": 404})


@app.route(
    "/get_article",
    methods=["POST", "GET"],
)
def get_article():
    """
    Get the news article.

    :return: a json object with the article
    """
    data = json.loads(request.get_data())
    post_id = data["post_id"]

    # get article from post_id
    article = Post.query.filter_by(id=post_id).first().news_id
    article = Articles.query.filter_by(id=article).first()
    if article is not None:
        return json.dumps({"summary": article.summary, "title": article.title})
    else:
        return json.dumps({"status": 404})


@app.route(
    "/share",
    methods=["POST", "GET"],
)
def share():
    """
    Share a post containing a news article.

    :return: a json object with the status of the share
    """
    return (
        json.dumps(
            {
                "status": 403,
                "message": "Sharing existing posts is disabled for forum experiments.",
            }
        ),
        403,
    )

    data = json.loads(request.get_data())
    account_id = data["user_id"]
    post_id = data["post_id"]
    text = data["text"].strip('"')
    emotions = data["emotions"]
    hastags = data["hashtags"]
    mentions = data["mentions"]
    tid = int(data["tid"])

    user = User_mgmt.query.filter_by(id=account_id).first()
    original_post = Post.query.filter_by(id=post_id).first()

    # Check if user already shared this post (deduplication)
    existing_share = Post.query.filter_by(
        user_id=user.id,
        shared_from=post_id
    ).first()
    if existing_share:
        return json.dumps({"status": 200, "message": "Already shared", "id": existing_share.id})

    post = Post(
        tweet=text,
        round=tid,
        user_id=user.id,
        shared_from=post_id,
        news_id=original_post.news_id,
    )

    db.session.add(post)
    db.session.commit()

    post.thread_id = post.id
    db.session.commit()

    if should_annotate_toxicity(app.config):
        toxicity(text, app.config.get("perspective_api"), post.id, db, enabled=True)
    sentiment = vader_sentiment(text) if should_annotate_sentiment(app.config) else None

    topics = Post_topics.query.filter_by(post_id=post_id).all()

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

    for topic in topics:
        if sentiment is not None:
            post_sentiment = Post_Sentiment(
                post_id=post.id,
                user_id=user.id,
                pos=sentiment["pos"],
                neg=sentiment["neg"],
                neu=sentiment["neu"],
                compound=sentiment["compound"],
                sentiment_parent=sentiment_parent,
                round=tid,
                is_post=1,
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
                post_emotion = Post_emotions(post_id=post.id, emotion_id=em.id)
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

        post_tag = Post_hashtags(post_id=post.id, hashtag_id=ht.id)
        db.session.add(post_tag)
        db.session.commit()

    for mention in mentions:
        if len(mention) < 1:
            continue

        us = User_mgmt.query.filter_by(username=mention.strip("@")).first()
        if us is not None:
            mention = Mentions(user_id=us.id, post_id=post.id, round=tid)
            db.session.add(mention)
            db.session.commit()

    return json.dumps({"status": 200})
