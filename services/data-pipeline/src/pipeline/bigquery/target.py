"""
Where the BigQuery mirror lives, read from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from google.cloud import bigquery
from google.oauth2 import service_account

from pipeline.config import load_root_env

GOLD_DATASET = "gloomberg_gold"
LAB_DATASET = "gloomberg_lab"
DEFAULT_LOCATION = "asia-southeast2"


@dataclass(frozen=True)
class BigQueryTarget:
    """One project and region the mirror publishes into."""

    project: str
    location: str
    credentials_path: str | None = None

    def client(self) -> bigquery.Client:
        """A client bound to this project and region."""
        credentials = (
            service_account.Credentials.from_service_account_file(self.credentials_path)  # type: ignore[no-untyped-call]
            if self.credentials_path
            else None
        )
        return bigquery.Client(project=self.project, location=self.location, credentials=credentials)

    def dataset_id(self, dataset: str) -> str:
        """The fully qualified id of a dataset in this project."""
        return f"{self.project}.{dataset}"


def target_from_env() -> BigQueryTarget | None:
    """The configured target, or nothing when BigQuery is switched off."""
    load_root_env()
    project = os.environ.get("BQ_PROJECT", "").strip()
    if not project:
        return None
    location = os.environ.get("BQ_LOCATION", "").strip() or DEFAULT_LOCATION
    # its own variable, so gemini never inherits these credentials
    credentials = os.environ.get("BQ_CREDENTIALS", "").strip() or None
    return BigQueryTarget(project=project, location=location, credentials_path=credentials)
