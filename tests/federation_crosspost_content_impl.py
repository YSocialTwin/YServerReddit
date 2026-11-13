"""P13: with crosspost.content including "news" the content of a post about an article carries the article link (forum)."""

import pytest

from federation_bus_impl import client  # noqa: F401  (fixture)
from y_server import app, db
from y_server.federation.adapter import YServerRedditAdapter
from y_server.modals import Articles, Post

pytest.importorskip("ysocial_federation")


def _content(policy):
    a = YServerRedditAdapter("forum", 1, accounts={"g1": 1}, crosspost_content=policy)
    with app.app_context():
        art = Articles(title="t", summary="s", website_id=1, link="https://example.org/story", fetched_on=0)
        db.session.add(art)
        db.session.flush()
        news = Post(tweet="headline without url", round=0, user_id=1, news_id=str(art.id), comment_to=-1)
        plain = Post(tweet="no article", round=0, user_id=1, comment_to=-1)
        db.session.add_all([news, plain])
        db.session.commit()
        return a._content(news), a._content(plain)


def test_the_link_travels_only_when_the_policy_asks_for_news(client):  # noqa: F811
    news, plain = _content(["news"])
    assert news["link"] == "https://example.org/story" and "link" not in plain


@pytest.mark.parametrize("policy", [None, ["all"], ["images"], ["own_content"]])
def test_without_news_the_message_is_unchanged(client, policy):  # noqa: F811
    news, plain = _content(policy)
    assert "link" not in news and "link" not in plain


def test_an_invalid_policy_is_refused(client):  # noqa: F811
    with pytest.raises(ValueError):
        YServerRedditAdapter("forum", 1, crosspost_content=["video"])
