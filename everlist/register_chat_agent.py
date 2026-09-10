#!/usr/bin/env python
"""B3: register the hub as an Agentverse chat agent (one-shot, idempotent).

Requires:
  AGENT_SEED_PHRASE  - canonical seed (auto-loaded from .secrets/agent_seed)
  AGENTVERSE_KEY     - API key from Agentverse launch wizard (auto-loaded from
                       .secrets/agentverse_key)

Both files are gitignored; the key is short-lived (1h) and never committed.
"""

import os
import sys
from pathlib import Path

SEEDFILE = Path(__file__).parent / ".secrets" / "agent_seed"
KEYFILE = Path(__file__).parent / ".secrets" / "agentverse_key"

os.environ.setdefault("AGENT_SEED_PHRASE", SEEDFILE.read_text().strip())
os.environ.setdefault("AGENTVERSE_KEY", KEYFILE.read_text().strip())

from uagents_core.utils.registration import (  # noqa: E402
    RegistrationRequestCredentials,
    register_chat_agent,
)

result = register_chat_agent(
    "EverList Booking",
    "https://agentverse.ai/v2/agents/mailbox/submit",
    active=True,
    credentials=RegistrationRequestCredentials(
        agentverse_api_key=os.environ["AGENTVERSE_KEY"],
        agent_seed_phrase=os.environ["AGENT_SEED_PHRASE"],
    ),
)
print("registration result:", result)
sys.exit(0)
