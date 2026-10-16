import os

from pipeline.config import load_root_env

# use the real .env when there is one, like the app does
load_root_env()

# ci has no .env, so fill the missing creds with fakes
for _name in ("MINIO_KEY", "MINIO_SECRET", "POSTGRES_USER", "POSTGRES_PASSWORD"):
    os.environ.setdefault(_name, "test")
