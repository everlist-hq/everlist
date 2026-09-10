"""agenthub — minimal Python SDK for Agent Hub Protocol (SPEC v2).

Stdlib-only. Credentials are sent ONLY as headers (X-Hub-Token), never in URLs
or bodies. Write calls send an Idempotency-Key automatically when not given.
"""
from .client import AgentHub, HubError, HubNetworkError
from .models import Booking, Listing

__all__ = ["AgentHub", "HubError", "HubNetworkError", "Booking", "Listing"]
__version__ = "0.1.0"
