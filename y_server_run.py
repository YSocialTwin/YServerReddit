import json
import os
from pathlib import Path


def _configure_model_cache_env():
    root = Path(os.environ.get("YSOCIAL_MODEL_CACHE_DIR", "~/.cache/ysocial_models")).expanduser()
    hf_home = root / "huggingface"
    transformers_cache = hf_home / "transformers"
    hub_cache = hf_home / "hub"
    torch_home = root / "torch"

    for path in (root, hf_home, transformers_cache, hub_cache, torch_home):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("YSOCIAL_MODEL_CACHE_DIR", str(root))
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(transformers_cache))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hub_cache))
    os.environ.setdefault("TORCH_HOME", str(torch_home))


def start_server(config):
    """
    Start the app
    """
    _configure_model_cache_env()
    from y_server import app
    import nltk

    nltk.download("vader_lexicon")

    debug = False
    app.config["perspective_api"] = config.get("perspective_api")
    app.config["toxicity_annotation"] = config.get("toxicity_annotation", False)
    app.config["sentiment_annotation"] = config.get("sentiment_annotation", False)
    app.config["emotion_annotation"] = config.get("emotion_annotation", False)
    app.config["sync_timeout_seconds"] = config.get("sync_timeout_seconds", 300)
    app.run(debug=debug, port=int(config["port"]), host=config["host"])


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()

    parser.add_argument(
        "-c",
        "--config_file",
        default=f"config_files{os.sep}exp_config.json",
        help="JSON file describing the simulation configuration",
    )
    args = parser.parse_args()

    config_file = os.path.abspath(args.config_file)
    # y_server reads config at import time, so provide the path before importing app.
    os.environ["YSERVER_CONFIG"] = config_file
    config = json.load(open(config_file, "r"))

    start_server(config)
