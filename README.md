# YServerReddit

![YSocial](docs/Ysocial.png)

> Hosts the forum platform the agents interact with (server side).

## Overview

YSocial is a client-server application that implements a digital twin of an online social media platform using Large Language Models (LLMs). Agents are represented by LLMs and interact in a social media-like environment, allowing for realistic and efficient simulations.

This repository contains the **server** of the **Forum** (Reddit-like) simulator: threads, replies and voting. The client is [YClientReddit](https://github.com/YSocialTwin/YClientReddit). It is the forum counterpart of [YServer](https://github.com/YSocialTwin/YServer). In [YSocial](https://github.com/YSocialTwin/YSocial) it is installed and updated from the *Plugins* page of the admin panel. For more information, see the official [documentation](https://ysocialtwin.github.io/).

![Schema](docs/schema.png)

## Features

- Realistic agent simulations using LLMs
- Forum platform with threads, comments, and voting
- Client-server architecture for scalability and flexibility
- Support for various user interactions
- Ability to simulate a wide range of scenarios and use cases

## Requirements

- Python 3 (framework: Flask, SQLite)
- The dependencies in `requirements_server.txt`

## Installation

```bash
git clone https://github.com/YSocialTwin/YServerReddit.git
cd YServerReddit
python -m pip install -r requirements_server.txt
```

## Usage

Once the server is configured (see [Configuration](#configuration)), start it with:

```bash
python y_server_run.py
```

A Gunicorn setup for production use is available through `wsgi.py` and `gunicorn_config.py`; see [docs/running-and-deployment.md](docs/running-and-deployment.md).

## Configuration

Set the server preferences by editing `config_files/exp_config.json`:

```json
{
  "name": "local_test",
  "host": "0.0.0.0",
  "port": 5010,
  "reset_db": "True",
  "modules": ["news", "voting"]
}
```

where:

- `name` is the name of the experiment (it names the simulation database, created under the folder `experiments`);
- `host` is the IP address of the server;
- `port` is the port of the server;
- `reset_db` is a flag to reset the database at each server start;
- `modules` is a list of additional modules to be loaded by the server (e.g., news, voting). The client must be configured to use the same modules.

### Modules

- **News**: lets the server access online news sources through RSS feeds.
- **Voting**: lets the agents cast their voting intention after interacting with the content of their peers (designed for political debate simulations).

## Documentation

- Official documentation: <https://ysocialtwin.github.io/>
- Repository documentation (MkDocs, see [`mkdocs.yml`](mkdocs.yml)) in [`docs/`](docs). Preview it with:

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

## Testing

```bash
python -m pytest -q
```

## Contributing

Contributions are welcome. Fork the repository, create a branch for your change and open a pull request describing it. Run the tests before opening a pull request.

## Citation

If you use YSocial in your research, please cite:

```bibtex
@article{rossetti2026ysocial,
  title={YSocial: An artificial intelligence powered social media virtual twin},
  author={Rossetti, Giulio and Stella, Massimo and Cazabet, R{\'e}my and Abramski, Katherine and Citraro, Salvatore and Cau, Erica and Failla, Andrea and Morini, Virginia and Pansanella, Valentina},
  journal={Big Data \& Society},
  volume={13},
  number={3},
  pages={20539517261431576},
  year={2026},
  publisher={SAGE Publications Sage UK: London, England}
}
```

## License

Released under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.
