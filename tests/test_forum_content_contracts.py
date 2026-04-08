from pathlib import Path


def test_forum_comments_copy_thread_topics():
    source = Path(
        "/Users/rossetti/PycharmProjects/YWeb/external/YServerReddit/y_server/routes/content_management.py"
    ).read_text(encoding="utf-8")

    assert "Post_topics(post_id=new_post.id, topic_id=topic.topic_id)" in source


def test_image_posts_fallback_to_image_subreddit_topic():
    source = Path(
        "/Users/rossetti/PycharmProjects/YWeb/external/YServerReddit/y_server/routes/image_post_management.py"
    ).read_text(encoding="utf-8")

    assert 'subreddit = str(getattr(image_post, "subreddit", "") or "").strip()' in source
    assert "if not topics:" in source
    assert "topics = [subreddit]" in source
