"""W5: regenerate the per-vertical OG share images via FLUX (fal.ai).

Usage: FAL_KEY=... python tools/gen_og.py
The key is read from the environment ONLY (or .secrets/fal_key) —
this file must never contain credentials. Sources land in .run/og-src/;
processed 1200x630 JPEGs are written to static/og/ by tools/og_post.py.
"""
import json, os, sys, time, urllib.request

def load_key():
    k = os.environ.get("FAL_KEY")
    if k:
        return k
    p = os.path.join(os.path.dirname(__file__), "..", ".secrets", "fal_key")
    if os.path.isfile(p):
        return open(p).read().strip()
    sys.exit("FAL_KEY env or .secrets/fal_key required")

OUT = os.path.join(os.path.dirname(__file__), "..", ".run", "og-src")
os.makedirs(OUT, exist_ok=True)
PROMPTS = {
 "events":   "moody rooftop jazz concert at dusk, city skyline bokeh, deep blue-black night tones with a single subtle green stage light, cinematic wide shot, photorealistic, no text, no words",
 "food":     "intimate sushi counter dinner, warm lantern glow against dark slate tones, chef hands placing nigiri, cinematic shallow depth of field, photorealistic, no text, no words",
 "services": "craftsman hands repairing a wooden chair in a dim workshop, single focused task lamp, dark slate tones with muted green accents, cinematic, photorealistic, no text, no words",
 "classes":  "small yoga class in a dark studio at golden hour, soft warm-green window light, calm focused atmosphere, cinematic, photorealistic, no text, no words",
 "p2p":      "night flea market stall with vinyl records and coffee cups, warm string lights against dark moody blue-black tones, cinematic, photorealistic, no text, no words",
 "default":  "abstract dark blue-black gradient background with faint flowing lines suggesting a calendar and handshake, subtle emerald green glow, minimal, elegant, no text, no words",
}
def gen(name, prompt):
    body = json.dumps({"prompt": prompt, "image_size": {"width": 1216, "height": 640}, "num_images": 1}).encode()
    req = urllib.request.Request("https://fal.run/fal-ai/flux/schnell", data=body,
        headers={"Authorization": "Key " + load_key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=150) as r:
        d = json.loads(r.read().decode())
    with urllib.request.urlopen(urllib.request.Request(d["images"][0]["url"]), timeout=60) as r:
        raw = r.read()
    open(os.path.join(OUT, name + ".src.png"), "wb").write(raw)
    print("GEN_OK", name, len(raw), flush=True)
if __name__ == "__main__":
    for name, prompt in PROMPTS.items():
        for attempt in (1, 2, 3):
            try:
                gen(name, prompt); break
            except Exception as e:
                print("GEN_RETRY", name, attempt, repr(e)[:120], flush=True)
                time.sleep(4 * attempt)
        else:
            sys.exit(f"GEN_FAIL {name}")
    print("ALL_GENERATED", flush=True)
