# maze-crawler

Competitive agent for the Kaggle **Maze Crawler** (`crawl`) environment.

## Repository layout

- `main.py` — local agent entry point (`agent(obs, config)`)
- `test.py` — quick local match against the built-in `random` bot
- `requirements.txt` — Python dependencies for local development
- `setup_crawl_env.sh` — syncs the `crawl` environment into a local venv
- `docs/crawl-rules.md` — game rules and observation reference

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
./setup_crawl_env.sh
python3 test.py
```

## Kaggle submission

1. In a Kaggle notebook, write the agent to `submission.py` with `%%writefile submission.py`.
2. Upgrade `kaggle-environments` in the notebook if `make("crawl")` is unavailable.
3. Submit `submission.py` (not `__pycache__/*.pyc`) from `/kaggle/working`.
4. Track versions and public score on the competition **Submissions** tab.

## Development loop

1. Change `main.py`.
2. Run `test.py` and a small seed sweep against `random`.
3. Copy the same logic into the notebook `submission.py`.
4. Submit and compare the new public score to the previous one.
