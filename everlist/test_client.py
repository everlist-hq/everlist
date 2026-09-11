"""End-to-end test (H5): a client uAgent asks the hub wrapper uAgent for events,
then books through the wrapper. Proves agent-to-agent discovery + query + booking
through the Fetch ecosystem.

Supported topologies:
- embedded (default): wrapper agent + client in one Bureau (this file)
- standalone: run `python wrapper.py` separately, then set WRAPPER_ADDR env to its address

Bounded: exits 0 with assertions on success; exits 1 on timeout (no infinite wait).
"""
import asyncio, json, os, sys
from uagents import Agent, Context, Model, Bureau

# H5: resolve the wrapper address at runtime — no hardcoded address literals.
if os.environ.get("WRAPPER_ADDR"):  # standalone mode
    HUB_WRAPPER_ADDR = os.environ["WRAPPER_ADDR"]
    STANDALONE = True
else:  # embedded mode: import wrapper module without running its __main__
    import importlib.util
    os.environ.setdefault("HUB_AGENT_SEED", "test-suite-seed-public-never-funds")  # B4: public test seed, holds no funds
    os.environ.setdefault("HUB_LOG_FILE", "/tmp/wrapper_test.log")  # keep test logs out of repo dir
    # gate isolation: the embedded wrapper must target the gate hub (HUB_PORT),
    # never the default live-stack port - make up propagates the same var
    os.environ.setdefault("HUB_URL", "http://localhost:%s" % os.environ.get("HUB_PORT", "8802"))
    spec = importlib.util.spec_from_file_location(
        "wrapper", os.path.join(os.path.dirname(os.path.abspath(__file__)), "wrapper.py"))
    w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
    HUB_WRAPPER_ADDR = w.hub_agent.address
    STANDALONE = False


class HubRequest(Model):
    action: str
    q: str = ""
    listing_id: str = ""
    attendee: str = ""
    human_verified: bool = False


class HubResponse(Model):
    result: str


DONE = asyncio.Event()
RESULTS = {"search": None, "book": None}

client = Agent(name="test-client", seed="test-client-seed-1")


@client.on_message(HubResponse)
async def on_resp(ctx: Context, sender: str, msg: HubResponse):
    try:
        d = json.loads(msg.result)
    except Exception:
        ctx.logger.info(f"RESPONSE (non-JSON): {msg.result[:200]}")
        return
    if "listings" in d:
        titles = [l.get("title") for l in d.get("listings", [])]
        RESULTS["search"] = titles
        ctx.logger.info(f"SEARCH RESULT via uAgent: {d['count']} listings -> {titles}")
        # follow up with a booking through the wrapper (exercises /access + token path)
        # no hardcoded ids: book whatever the search actually found (H16 lesson:
        # demo-reset reseeded ids; built-in seed differs) — first result
        first = (d.get("listings") or [{}])[0]
        await ctx.send(HUB_WRAPPER_ADDR, HubRequest(action="book", listing_id=first.get("id", ""),
                                                    attendee="E2E-Human", human_verified=True))
    elif "id" in d and "escrow" in d:
        RESULTS["book"] = d
        ctx.logger.info(f"BOOKING RESULT via uAgent: {d['id']} escrow={d.get('escrow')} amount={d.get('amount')}")
        DONE.set()
    elif "error" in d:
        ctx.logger.info(f"ERROR ENVELOPE: {d}")
        RESULTS["book"] = d
        DONE.set()


@client.on_interval(period=3.0)
async def ask(ctx: Context):
    if RESULTS["search"] is None:
        await ctx.send(HUB_WRAPPER_ADDR, HubRequest(action="search", q="jazz"))


async def main():
    bureau = Bureau()
    if not STANDALONE:
        bureau.add(w.hub_agent)
    bureau.add(client)
    asyncio.ensure_future(bureau.run_async())  # uagents 0.25 async entrypoint
    try:
        await asyncio.wait_for(DONE.wait(), timeout=60)
    except asyncio.TimeoutError:
        print(f"E2E_TIMEOUT: results={RESULTS}")
        os._exit(1)  # hard exit inside loop: never cancel Bureau (teardown recursion)
    # assertions (not logging-only)
    assert RESULTS["search"] and len(RESULTS["search"]) >= 1, f"search failed: {RESULTS['search']}"
    assert "Rooftop Jazz Night" in RESULTS["search"], RESULTS["search"]
    # escrow must match the amount (H17 lesson: demo state contains free listings
    # whose honest outcome is WAIVED) - HELD iff amount > 0
    bk = RESULTS["book"]
    amt = float(bk.get("amount", -1)) if bk else -1
    exp = "HELD" if amt > 0 else "WAIVED"
    assert bk and bk.get("escrow") == exp, f"book failed: escrow={bk.get('escrow')} amount={amt}"
    print(f"E2E_PASSED: search={RESULTS['search']} booking={RESULTS['book']['id']} escrow={RESULTS['book']['escrow']}")
    os._exit(0)  # hard exit inside loop: bounded, no cancellation cascade


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise  # os._exit bypasses this entirely; kept for clarity
