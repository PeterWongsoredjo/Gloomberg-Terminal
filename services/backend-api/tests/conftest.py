"""Suite-wide env: neutralize deploy knobs so tests control their own world.

The real operator token and rate ceiling live in the repo-root .env; without these
overrides the suite would 401 its own dispatches and 429 its own polling loops.
Tests that need a token or a ceiling set them explicitly.
"""

import os

os.environ["GLOOMBERG_API_TOKEN"] = ""
os.environ["GLOOMBERG_RATE_LIMIT"] = ""
