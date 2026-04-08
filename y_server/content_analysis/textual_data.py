from __future__ import annotations

import threading

from nltk.sentiment import SentimentIntensityAnalyzer
from y_server.modals import Post_Toxicity


_DETOXIFY_SCORER = None
_DETOXIFY_LOCK = threading.Lock()


def _to_scalar(value):
    if isinstance(value, (list, tuple)):
        if not value:
            return 0.0
        value = value[0]
    try:
        return float(value)
    except Exception:
        return 0.0


def _persist_toxicity_scores(post_id, db, scores):
    post_toxicity = Post_Toxicity(
        post_id=post_id,
        toxicity=_to_scalar(scores.get("toxicity", scores.get("TOXICITY", 0.0))),
        severe_toxicity=_to_scalar(
            scores.get("severe_toxicity", scores.get("SEVERE_TOXICITY", 0.0))
        ),
        identity_attack=_to_scalar(
            scores.get("identity_attack", scores.get("IDENTITY_ATTACK", 0.0))
        ),
        insult=_to_scalar(scores.get("insult", scores.get("INSULT", 0.0))),
        profanity=_to_scalar(
            scores.get("obscene", scores.get("PROFANITY", 0.0))
        ),
        threat=_to_scalar(scores.get("threat", scores.get("THREAT", 0.0))),
        sexually_explicit=_to_scalar(
            scores.get("sexual_explicit", scores.get("SEXUALLY_EXPLICIT", 0.0))
        ),
        flirtation=_to_scalar(scores.get("FLIRTATION", 0.0)),
    )

    db.session.add(post_toxicity)
    db.session.commit()


def _get_detoxify_scorer():
    global _DETOXIFY_SCORER
    if _DETOXIFY_SCORER is not None:
        return _DETOXIFY_SCORER
    with _DETOXIFY_LOCK:
        if _DETOXIFY_SCORER is None:
            from detoxify import Detoxify

            _DETOXIFY_SCORER = Detoxify("original")
    return _DETOXIFY_SCORER


def _detoxify_scores(text):
    scorer = _get_detoxify_scorer()
    raw_scores = scorer.predict(str(text or ""))
    return {
        "toxicity": raw_scores.get("toxicity", 0.0),
        "severe_toxicity": raw_scores.get("severe_toxicity", raw_scores.get("toxicity", 0.0)),
        "identity_attack": raw_scores.get("identity_attack", 0.0),
        "insult": raw_scores.get("insult", 0.0),
        "obscene": raw_scores.get("obscene", 0.0),
        "threat": raw_scores.get("threat", 0.0),
        "sexual_explicit": raw_scores.get("sexual_explicit", 0.0),
        "FLIRTATION": 0.0,
    }


def vader_sentiment(text):
    sia = SentimentIntensityAnalyzer()
    sentiment = sia.polarity_scores(text)
    return sentiment


def should_annotate_toxicity(config):
    return bool((config or {}).get("toxicity_annotation", False))


def should_annotate_sentiment(config):
    return bool((config or {}).get("sentiment_annotation", False))


def should_annotate_emotions(config):
    return bool((config or {}).get("emotion_annotation", False))


def toxicity(text, api_key, post_id, db, enabled=True):
    try:
        if not enabled:
            return
        if api_key:
            from perspective import PerspectiveAPI

            p = PerspectiveAPI(api_key)
            scores = p.score(
                str(text or ""),
                tests=[
                    "TOXICITY",
                    "SEVERE_TOXICITY",
                    "IDENTITY_ATTACK",
                    "INSULT",
                    "PROFANITY",
                    "THREAT",
                    "SEXUALLY_EXPLICIT",
                    "FLIRTATION",
                ],
            )
        else:
            scores = _detoxify_scores(text)
        _persist_toxicity_scores(post_id, db, scores)
    except Exception as e:
        print(e)
        return
