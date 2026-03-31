import json
import os


def start_server(config):
    """
    Start the app
    """
    from y_server import app
    import nltk

    nltk.download("vader_lexicon")

    debug = False
    app.config["perspective_api"] = config["perspective_api"]
    app.config["toxicity_annotation"] = config.get("toxicity_annotation", False)
    app.config["sentiment_annotation"] = config.get("sentiment_annotation", False)
    app.config["emotion_annotation"] = config.get("emotion_annotation", False)
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
