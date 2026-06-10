import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from y_server import app
from y_server.content_analysis import textual_data


def test_should_annotate_toxicity_defaults_false():
    assert textual_data.should_annotate_toxicity({}) is False
    assert textual_data.should_annotate_toxicity({"toxicity_annotation": False}) is False
    assert textual_data.should_annotate_toxicity({"toxicity_annotation": True}) is True


def test_should_annotate_sentiment_and_emotions_default_false():
    assert textual_data.should_annotate_sentiment({}) is False
    assert textual_data.should_annotate_sentiment({"sentiment_annotation": True}) is True
    assert textual_data.should_annotate_emotions({}) is False
    assert textual_data.should_annotate_emotions({"emotion_annotation": True}) is True


def test_toxicity_disabled_skips_detoxify(monkeypatch):
    called = {"detoxify": 0, "persist": 0}

    def _fake_detoxify(_text):
        called["detoxify"] += 1
        return {"toxicity": 0.1}

    def _fake_persist(_post_id, _db, _scores):
        called["persist"] += 1

    monkeypatch.setattr(textual_data, "_detoxify_scores", _fake_detoxify)
    monkeypatch.setattr(textual_data, "_persist_toxicity_scores", _fake_persist)

    textual_data.toxicity("hello", None, 1, db=None, enabled=False)

    assert called == {"detoxify": 0, "persist": 0}


def test_detoxify_scorer_is_cached(monkeypatch):
    created = {"count": 0}

    class _FakeDetoxify:
        def __init__(self, _name):
            created["count"] += 1

        def predict(self, _text):
            return {"toxicity": 0.2}

    monkeypatch.setattr(textual_data, "_DETOXIFY_SCORER", None)
    monkeypatch.setitem(sys.modules, "detoxify", type("_M", (), {"Detoxify": _FakeDetoxify})())

    first = textual_data._detoxify_scores("one")
    second = textual_data._detoxify_scores("two")

    assert first["toxicity"] == 0.2
    assert second["toxicity"] == 0.2
    assert created["count"] == 1


def test_wsgi_and_run_config_capture_toxicity_flag(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """{
  "perspective_api": null,
  "toxicity_annotation": true,
  "sentiment_annotation": false,
  "emotion_annotation": false
}""",
        encoding="utf-8",
    )

    import importlib
    import os

    os.environ["YSERVER_CONFIG"] = str(config_path)
    wsgi = importlib.import_module("wsgi")
    importlib.reload(wsgi)
    assert wsgi.app.config["toxicity_annotation"] is True


def test_reddit_routes_use_annotation_helpers():
    from pathlib import Path

    base = Path("/Users/rossetti/PycharmProjects/YWeb/external/YServerReddit/y_server/routes")
    files = {
        "image_management.py": base / "image_management.py",
        "news_management.py": base / "news_management.py",
        "content_management.py": base / "content_management.py",
        "image_post_management.py": base / "image_post_management.py",
    }

    image_text = files["image_management.py"].read_text(encoding="utf-8")
    news_text = files["news_management.py"].read_text(encoding="utf-8")
    content_text = files["content_management.py"].read_text(encoding="utf-8")
    image_post_text = files["image_post_management.py"].read_text(encoding="utf-8")

    assert "should_annotate_toxicity(app.config)" in image_text
    assert "should_annotate_sentiment(app.config)" in image_text
    assert "should_annotate_emotions(app.config)" in image_text

    assert "should_annotate_toxicity(app.config)" in news_text
    assert "should_annotate_sentiment(app.config)" in news_text
    assert "should_annotate_emotions(app.config)" in news_text

    assert "should_annotate_toxicity(app.config)" in content_text
    assert "should_annotate_sentiment(app.config)" in content_text
    assert "should_annotate_emotions(app.config)" in content_text

    assert "should_annotate_sentiment(app.config)" in image_post_text
    assert "should_annotate_emotions(app.config)" in image_post_text


def test_comment_cleanup_updates_new_post_not_parent():
    from pathlib import Path

    content_text = Path(
        "/Users/rossetti/PycharmProjects/YWeb/external/YServerReddit/y_server/routes/content_management.py"
    ).read_text(encoding="utf-8")

    assert "new_post.tweet = text.lstrip().rstrip()" in content_text
    assert "db.session.delete(new_post)" in content_text
    assert "post.tweet = text.lstrip().rstrip()" in content_text


def test_content_management_sanitizes_emotion_payloads():
    content_text = Path(
        "/Users/rossetti/PycharmProjects/YWeb/external/YServerReddit/y_server/routes/content_management.py"
    ).read_text(encoding="utf-8")

    assert "_looks_like_emotion_payload" in content_text
    assert "cleaned = \"\"" in content_text
