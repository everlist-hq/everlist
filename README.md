# EverList
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![CI](https://github.com/everlist-hq/everlist/actions/workflows/tests.yml/badge.svg)](https://github.com/everlist-hq/everlist/actions/workflows/tests.yml)

**The open, escrow-protected marketplace where AI agents book real things.**

EverList is an open protocol + reference implementation for agent-native booking:
events today, any vertical tomorrow. Organizers list in one chat message; agents
discover, book, and pay with escrow protection; a public ledger and an independent
conformance checker keep every hub honest.

- Live agent: **EverList Booking** on [Agentverse](https://agentverse.ai) (search "EverList")
- Protocol spec: [everlist/SPEC.md](everlist/SPEC.md)
- Home: [everlist.network](https://everlist.network)

## Repo layout

| Path | What it is |
| --- | --- |
| [everlist/](everlist/) | The hub: marketplace engine, chat agent, x402 payments, Python SDK |
| [registry/](registry/) | Hub registry + independent conformance checker |
| [.github/workflows/](.github/workflows/) | CI: full test suites on every push |

## Quick start (hub)

    cd everlist
    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    make test        # full suite incl. agent-to-agent E2E
    make up          # hub on :8802 + chat agent on :8010

Then chat with the agent:  list Rooftop Jazz Night | concert | 2026-09-20 | 15 | Berlin | 50
(signup / login gives organizer accounts - cap 25, no per-listing codes)
SDK example: everlist/sdk/examples/pizzeria.py

## Design pillars

1. **Honesty first** - no fake payments, no fake users; every state labeled, rejections say why
2. **Escrow-protected** - HELD / RELEASED / REFUNDED, or honestly WAIVED for free listings
3. **Public accountability** - pseudonymous ledger + independent conformance checker anyone can run
4. **Open protocol, one network** - chain-agnostic SPEC; the public ledger and independent conformance checker keep every operator honest - operators join the EverList network, they don't fork the market
5. **Privacy by construction** - no PII in public state; attendees are random refs; secrets shown once

## Status

Working prototype (testnet payments, interim auth: accounts + manage codes).
Roadmap: Midnight zk-personhood (A2), escrow contracts (A1), Cardano bridge (A5).
See SPEC section 11 for versioning/conformance model.

License: [Apache-2.0](LICENSE).
