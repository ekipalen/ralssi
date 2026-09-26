"""Shared helper for loading the OpenAI API key used by ralssi's
embed_*.py / enrich_*.py scripts.

Reads ~/.config/ralssi/secrets.env (key OPENAI_API_KEY). This is ralssi's
OWN key — do NOT fall back to the voice-bot secrets file
(~/.config/voice-bot/secrets.env), ralssi must not depend on it.
"""

import os

SECRETS_PATH = os.path.expanduser("~/.config/ralssi/secrets.env")


def load_api_key():
    with open(SECRETS_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"OPENAI_API_KEY not found in {SECRETS_PATH}")
