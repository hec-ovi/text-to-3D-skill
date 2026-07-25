#!/usr/bin/env python3
"""text2image blackbox: prompt in, PNG-by-reference out.

Renders with FLUX.2 [klein] on a running ComfyUI (the comfyui-strix-docker
stack, ROCm on gfx1151). Stdlib only, no third-party imports.

    python3 klein.py --prompt "a brass diving helmet" --out-dir /tmp/out
    python3 klein.py --request request.json          # envelope on stdin/file

Prints an ImageResult envelope on stdout, or an error envelope on stderr with
a non-zero exit. See ../CONTRACT.md.
"""

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
REQ_SCHEMA = os.path.join(LAYER, "schema", "image_request.json")
RES_SCHEMA = os.path.join(LAYER, "schema", "image_result.json")
TEMPLATE = os.path.join(LAYER, "templates", "flux2_klein_t2i.json")

CONTRACT_VERSION = "1.0"

# Graph node ids in templates/flux2_klein_t2i.json.
N_POSITIVE, N_LATENT, N_SCHEDULER, N_NOISE = "4", "6", "7", "9"

# TRELLIS reconstructs one object and removes the background first. A clean,
# centred, evenly lit subject on a flat backdrop is what survives that step;
# scenes, crops and cast shadows do not.
OBJECT_FRAMING = (
    "{subject}, a single complete object centred in frame, full object visible with "
    "nothing cropped, three-quarter view, even diffuse studio lighting, flat plain "
    "light grey background, no cast shadow on the background, no other objects, "
    "no text, no watermark, sharp focus, product photograph"
)

# A person is not a product. Two things go wrong when a character is framed like
# one: a three-quarter view hides half the face from the reconstruction, and an
# unspecified stance comes out mid-action, which the rig then binds as the rest
# pose so every clip plays on top of it. This asks for the pose a rigger wants
# and puts the face where the camera can resolve it.
#
# Every clause is load-bearing, and the second half of them were added after
# measuring what actually came back. Running layers/rig's pose report over the
# characters this stage had produced found, in order of how often and how badly:
#
#   a stride         feet 36% of the figure's height apart front to back, which
#                    an "A-pose" instruction alone does not prevent because a
#                    walking figure can still have its arms out
#   untraceable arms an arm holding or crossing something never separates from
#                    the torso in cross-section, and bone heat then bleeds its
#                    weights into the ribs
#   no shoulder line a cloak or hair across the shoulders means no band splits
#                    into torso-plus-two-arms, so the shoulders are a guess
#   a bent limb      a limb bowed more than a ninth of its own length off
#                    straight, baked into the bind pose for good
#
# So the stance is now spelled out as a *symmetric, static, weight-even* one
# rather than named, the hands are explicitly empty, and the shoulders are asked
# to be clear. The front view is what a face needs, because the reconstructor
# sees exactly one view and invents whatever the camera hid. The soft key light
# is a compromise: fully flat light bakes cleanly but removes the shading
# gradient a single view uses to infer brow, nose and cheek relief.
CHARACTER_FRAMING = (
    "{subject}, standing still in a neutral A-pose facing the camera directly, "
    "perfectly symmetrical stance with weight evenly on both feet, both feet flat on "
    "the ground side by side on the same line and shoulder width apart, neither foot "
    "stepped forward or back, legs straight and vertical, knees not bent, arms "
    "straight and lowered about 45 degrees away from the body with a clear open gap "
    "between each arm and the torso, elbows not bent, hands empty and open with palms "
    "facing in, nothing held or carried, arms not crossing the body, shoulders bare "
    "and unobstructed with no cape, cloak, hair or fabric draped over them, head level "
    "and looking straight ahead, face clearly visible and unobstructed, sharp detailed "
    "eyes, natural skin, full body visible from head to feet, standing at rest, not "
    "walking, not running, not posing, no action, soft key light from the front with "
    "gentle fill, plain light grey backdrop, empty background, sharp focus, character "
    "reference sheet, front view, T-pose reference orthographic style"
)

# Words that mean the subject is a person or creature that will be rigged. The
# cost of a wrong guess is asymmetric: character framing on a prop only wastes a
# few tokens, product framing on a person hides the face and bakes in a pose.
CHARACTER_WORDS = (
    "man", "male", "woman", "female", "boy", "girl", "person", "human", "guy",
    "lady", "child", "kid", "character", "warrior", "knight", "soldier",
    "wizard", "mage", "witch", "sorcerer", "necromancer", "druid", "shaman",
    "bard", "elf", "orc", "dwarf", "goblin", "troll", "ogre", "giant", "gnome",
    "hero", "heroine", "villain", "ninja", "samurai", "pirate", "viking",
    "barbarian", "berserker", "gladiator", "archer", "ranger", "rogue",
    "assassin", "mercenary", "paladin", "templar", "crusader", "monk",
    "priest", "nun", "king", "queen", "prince", "princess", "guard",
    "adventurer", "explorer", "astronaut", "pilot", "sailor", "cowboy",
    "detective", "blacksmith", "hunter", "swordsman", "spearman", "robot",
    "android", "cyborg", "mutant", "alien", "zombie", "skeleton", "demon",
    "devil", "angel", "fairy", "vampire", "werewolf", "mermaid", "centaur",
    "monster", "creature", "beast", "figure", "statue",
)


# Things a character owns or wears. These matter because a character word used
# as an adjective describes a prop, not a person: "a knight's helmet" is a
# helmet. The cost of getting that backwards went up when the stance
# instructions above got insistent, because a full-body A-pose prompt applied to
# a helmet does not produce a helmet, it produces someone wearing one.
WORN_OR_CARRIED = (
    "helmet", "helm", "mask", "visor", "crown", "hat", "hood", "armour", "armor",
    "breastplate", "cuirass", "pauldron", "gauntlet", "glove", "boot", "shoe",
    "belt", "cloak", "cape", "robe", "shield", "sword", "blade", "axe", "spear",
    "lance", "bow", "arrow", "dagger", "knife", "hammer", "mace", "staff",
    "wand", "gun", "rifle", "pistol", "banner", "flag", "ring", "amulet",
    "pendant", "necklace", "bracer", "backpack", "pack", "ship", "horse",
    "throne", "tomb", "statue", "bust", "head", "skull",
)


def _tokens(subject):
    """Lowercase word tokens, with a possessive "s" dropped.

    "a knight's helmet" splits into knight / s / helmet, and that stray s is
    what would otherwise stop the two words from reading as adjacent.
    """
    return [w for w in re.findall(r"[a-z]+", subject.lower()) if w != "s"]


def _singular(word):
    return word.rstrip("s")


def looks_like_a_character(subject):
    """True when the subject reads as something with a face and limbs.

    A trailing s is stripped as well as matched, so "two warriors" gets the
    stance instructions. TRELLIS reconstructs one subject regardless, but a
    plural prompt is already going to disappoint and it should at least
    disappoint in a rigged A-pose.

    A character word directly in front of something worn or carried is an
    adjective and loses: "a samurai sword" is a sword. Anything less direct
    still wins, so "a warrior with a sword" is a warrior.
    """
    words = _tokens(subject)
    found = False
    for index, word in enumerate(words):
        if not (word in CHARACTER_WORDS or _singular(word) in CHARACTER_WORDS):
            continue
        following = words[index + 1] if index + 1 < len(words) else ""
        if following in WORN_OR_CARRIED or _singular(following) in WORN_OR_CARRIED:
            continue
        found = True
    return found


def frame(subject, kind="auto"):
    """Wrap a subject in the framing its kind needs. Returns (prompt, kind)."""
    if kind == "auto":
        kind = "character" if looks_like_a_character(subject) else "object"
    template = CHARACTER_FRAMING if kind == "character" else OBJECT_FRAMING
    return template.format(subject=subject), kind


class RenderError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


def seed_for(text):
    """Deterministic 63-bit seed so the same prompt reproduces the same image."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16) >> 1


def png_size(data):
    """(width, height) from a PNG IHDR, or a PNG_INVALID error."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise RenderError("RENDER_FAILED", "backend returned bytes that are not a PNG")
    return struct.unpack(">II", data[16:24])


def build_graph(template, prompt, seed, width, height, steps):
    """Deep-copies the template and injects prompt/seed/dims/steps. Never mutates it."""
    graph = json.loads(json.dumps(template))
    try:
        graph[N_POSITIVE]["inputs"]["text"] = prompt
        graph[N_NOISE]["inputs"]["noise_seed"] = seed
        for node in (N_LATENT, N_SCHEDULER):
            graph[node]["inputs"]["width"] = width
            graph[node]["inputs"]["height"] = height
        graph[N_SCHEDULER]["inputs"]["steps"] = steps
    except (KeyError, TypeError, IndexError) as exc:
        raise RenderError(
            "INVALID_REQUEST",
            "the ComfyUI template is missing the expected flux2_klein nodes",
            str(exc),
        )
    return graph


def _http(method, url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RenderError(
            "BACKEND_UNREACHABLE",
            f"no ComfyUI at {url}",
            f"{exc}. Start it with: cd ../comfyui-strix-docker && docker compose up -d",
        )


def render(request, template_path=None):
    """Run one ImageRequest through ComfyUI. Returns a validated ImageResult."""
    req_schema = load(REQ_SCHEMA)
    try:
        validate(request, req_schema)
    except SchemaError as exc:
        raise RenderError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, req_schema)

    subject = req["prompt"].strip()
    if req.get("rawPrompt"):
        prompt, kind = subject, "raw"
    else:
        prompt, kind = frame(subject, req["framing"])
    seed = req.get("seed", seed_for(prompt))
    endpoint = req["endpoint"].rstrip("/")
    out_dir = req.get("outDir") or os.getcwd()

    template = load(template_path or TEMPLATE)
    graph = build_graph(template, prompt, seed, req["width"], req["height"], req["steps"])

    started = time.monotonic()
    client_id = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]
    status, body = _http(
        "POST",
        endpoint + "/prompt",
        data=json.dumps({"prompt": graph, "client_id": client_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    if status != 200:
        text = body.decode("utf-8", "replace")
        code = "MODEL_MISSING" if "not in list" in text or "value not in" in text else "GRAPH_REJECTED"
        raise RenderError(code, f"ComfyUI rejected the graph (HTTP {status})", text[:600])

    try:
        prompt_id = json.loads(body)["prompt_id"]
    except (ValueError, KeyError, TypeError):
        raise RenderError("GRAPH_REJECTED", "ComfyUI returned no prompt_id",
                          body.decode("utf-8", "replace")[:300])

    png = _await_image(endpoint, prompt_id, req["timeoutSeconds"])
    width, height = png_size(png)
    digest = hashlib.sha256(png).hexdigest()

    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{digest[:16]}.png")
        with open(path, "wb") as fh:
            fh.write(png)
    except OSError as exc:
        raise RenderError("OUTPUT_WRITE_FAILED", f"cannot write into {out_dir}", str(exc))

    result = {
        "contractVersion": CONTRACT_VERSION,
        "image": {
            "uri": os.path.abspath(path),
            "mediaType": "image/png",
            "byteSize": len(png),
            "checksum": {"sha256": digest},
            "width": width,
            "height": height,
        },
        "seed": seed,
        "framing": kind,
        "promptSent": prompt,
        "model": {
            "unet": template["1"]["inputs"]["unet_name"],
            "clip": template["2"]["inputs"]["clip_name"],
            "vae": template["3"]["inputs"]["vae_name"],
        },
        "steps": req["steps"],
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    validate(result, load(RES_SCHEMA))
    return result


def _await_image(endpoint, prompt_id, timeout_seconds):
    """Poll /history until the run produces an image, then fetch its bytes."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(2)
        _status, body = _http("GET", f"{endpoint}/history/{prompt_id}", timeout=30)
        try:
            history = json.loads(body)
        except ValueError:
            continue
        entry = history.get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RenderError("RENDER_FAILED", "ComfyUI reported an execution error",
                              json.dumps(status)[:600])
        for node in (entry.get("outputs") or {}).values():
            for image in node.get("images", []):
                query = urllib.parse.urlencode({
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                })
                _s, png = _http("GET", f"{endpoint}/view?{query}", timeout=120)
                return png
        if entry.get("status", {}).get("completed"):
            raise RenderError("RENDER_FAILED", "the run completed with no image output")
    raise RenderError("TIMEOUT", f"no image after {timeout_seconds}s")


def main(argv=None):
    parser = argparse.ArgumentParser(description="FLUX.2 klein text-to-image via ComfyUI")
    parser.add_argument("--prompt")
    parser.add_argument("--request", help="path to an ImageRequest JSON file, or - for stdin")
    parser.add_argument("--out-dir", default=os.getcwd())
    parser.add_argument("--seed", type=int)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--endpoint", default=os.environ.get("COMFY_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--framing", choices=["auto", "object", "character"], default="auto")
    parser.add_argument("--raw-prompt", action="store_true")
    parser.add_argument("--template")
    args = parser.parse_args(argv)

    if args.request:
        raw = sys.stdin.read() if args.request == "-" else open(args.request, encoding="utf-8").read()
        request = json.loads(raw)
    elif args.prompt:
        request = {
            "prompt": args.prompt,
            "outDir": args.out_dir,
            "width": args.width,
            "height": args.height,
            "steps": args.steps,
            "endpoint": args.endpoint,
        }
        if args.seed is not None:
            request["seed"] = args.seed
        if args.raw_prompt:
            request["rawPrompt"] = True
        if args.framing != "auto":
            request["framing"] = args.framing
    else:
        parser.error("one of --prompt or --request is required")

    try:
        print(json.dumps(render(request, args.template), indent=2))
    except RenderError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
