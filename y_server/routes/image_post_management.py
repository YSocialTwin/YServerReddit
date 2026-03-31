import json

from flask import request

from y_server import app, db
from y_server.content_analysis import (
    should_annotate_emotions,
    should_annotate_sentiment,
    vader_sentiment,
)
from y_server.modals import (
    Emotions,
    Hashtags,
    ImagePosts,
    Interests,
    Mentions,
    Post,
    Post_emotions,
    Post_hashtags,
    Post_Sentiment,
    Post_topics,
    User_mgmt,
)


@app.route("/image_post", methods=["POST"])
def create_image_post():
    """Create a post backed by a standalone image."""
    data = json.loads(request.get_data())
    account_id = data["user_id"]
    text = str(data.get("tweet", "")).strip().strip('"')
    image_url = data["image_url"]
    image_description = data.get("image_description", "")
    emotions = data.get("emotions", [])
    hashtags = data.get("hashtags", [])
    mentions = data.get("mentions", [])
    tid = int(data["tid"])

    if len(text) < 3:
        return json.dumps({"status": 400, "error": "empty_post"})

    user = User_mgmt.query.filter_by(id=account_id).first()
    if user is None:
        return json.dumps({"status": 404, "error": "User not found"})

    image_post = ImagePosts.query.filter_by(url=image_url).first()
    if image_post is None:
        image_post = ImagePosts(
            url=image_url,
            description=image_description,
            used=True,
        )
        db.session.add(image_post)
        db.session.commit()
    else:
        image_post.used = True
        if image_description and not image_post.description:
            image_post.description = image_description
        db.session.commit()

    post = Post(
        tweet=text,
        round=tid,
        user_id=user.id,
        comment_to=-1,
        image_post_id=image_post.id,
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
                db.session.add(Post_emotions(post_id=post.id, emotion_id=em.id))
                db.session.commit()

    for tag in hashtags:
        if len(tag) < 4:
            continue
        ht = Hashtags.query.filter_by(hashtag=tag).first()
        if ht is None:
            ht = Hashtags(hashtag=tag)
            db.session.add(ht)
            db.session.commit()
            ht = Hashtags.query.filter_by(hashtag=tag).first()
        db.session.add(Post_hashtags(post_id=post.id, hashtag_id=ht.id))
        db.session.commit()

    for mention in mentions:
        if len(mention) < 1:
            continue
        us = User_mgmt.query.filter_by(username=mention.strip("@")).first()
        if us is not None:
            db.session.add(Mentions(user_id=us.id, post_id=post.id, round=tid))
            db.session.commit()

    if "topics" in data:
        sentiment = vader_sentiment(text) if should_annotate_sentiment(app.config) else None
        for topic in data["topics"]:
            if len(topic) < 1:
                continue
            interest = Interests.query.filter_by(interest=topic).first()
            if interest is None:
                interest = Interests(interest=topic)
                db.session.add(interest)
                db.session.commit()
                interest = Interests.query.filter_by(interest=topic).first()

            db.session.add(Post_topics(post_id=post.id, topic_id=interest.iid))
            if sentiment is not None:
                db.session.add(
                    Post_Sentiment(
                        post_id=post.id,
                        user_id=user.id,
                        pos=sentiment["pos"],
                        neg=sentiment["neg"],
                        neu=sentiment["neu"],
                        compound=sentiment["compound"],
                        round=tid,
                        is_post=1,
                        topic_id=interest.iid,
                    )
                )
                db.session.commit()

    return json.dumps(
        {"status": 200, "post_id": post.id, "image_post_id": image_post.id}
    )


@app.route("/get_image_post", methods=["POST", "GET"])
def get_image_post():
    """Get image-post details by post id."""
    data = json.loads(request.get_data())
    post_id = data["post_id"]

    post = Post.query.filter_by(id=post_id).first()
    if post is None or post.image_post_id is None:
        return json.dumps({"status": 404, "error": "Image post not found"})

    image_post = ImagePosts.query.filter_by(id=post.image_post_id).first()
    if image_post is None:
        return json.dumps({"status": 404, "error": "Image not found"})

    return json.dumps(
        {
            "status": 200,
            "image_url": image_post.url,
            "description": image_post.description,
        }
    )
