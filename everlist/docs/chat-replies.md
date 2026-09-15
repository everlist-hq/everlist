# EverList chat — reply guidance (binding)

Created 2026-09-15 (owner call: *the chat declines off-topic; its sole job is this site*).
This is the voice contract for every reply the chat produces — in the webchat, the CLI,
and the Agentverse wrapper. The LLM never writes user-visible prose; chatlib does.
All wording lives in `chatlib.py` and must follow this document.

## The one law

The chat does exactly one thing: **help people find, book, and list real-world things.**
Everything else gets declined — friendly, short, and with a way back in.

- Never answer general knowledge, news, weather, homework, code, translation, chit-chat.
- Never role-play or adopt personas beyond "the EverList assistant".
- Decline even when the LLM router is down (the deterministic screen in `_boundary_or_none`
  guarantees this — no flake may leak an off-topic answer).
- Identity questions ("who are you") are NOT off-topic: they're about this site. Answer
  them with `_WHOAMI`, then steer straight back to the job.

## How to reply (voice)

| Rule | Why | Example |
| --- | --- | --- |
| **Lead with the answer** | people scan | `✅ Booked! 'Rooftop Jazz Night' — booking bk-…` |
| **One job per reply** | no walls of text | a search reply offers booking, not a lecture |
| **Always give the next move** | momentum | end with what to say next: `Say 'book 1'` |
| **Plain words** | everyone, any device | "money is held until the event ends", not "collateralized settlement" |
| **Show fees and escrow, never bury them** | the moat is trust | price + escrow state on every booking surface |
| **Emoji as status only** | scannable, not decoration | ✅ success · ⏳ pending · 🔑 one-time secret · ❌ rejection |
| **Short on mobile** | most visitors are on phones | 1–3 lines unless listing details require more |
| **Honest failure** | trust beats face-saving | `Booking rejected: <real reason>` + what would fix it |
| **Numbers stay exact** | money | `€15`, `72h refund window`, `12 / 12 spots` — never "about" |

## Decline template (off-topic)

1. Decline in one sentence (no apology theater).
2. Restate the job in half a sentence.
3. Offer one concrete way back.

> I can't help with that — I only do EverList: finding, booking, and listing real-world
> things. Tell me what you're looking for — e.g. 'free yoga this weekend'.

## Board rule (webchat only)

When a search opens results as UI on the board, the transcript says ONE short line and
shuts up: `I found 3 matches — they're open on the board for you. Say 'book <n>' to book
one.` The ASCII table stays for agents/CLI (they have no board); the web replaces it with
the board.

## Session budget

The webchat transcript is capped at **50 000 characters** (oldest messages trimmed, a
one-time note is shown). The brain is stateless per message — there is no hidden LLM
conversation that can grow — so this bounds only the visible session.
