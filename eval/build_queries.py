"""Splits, manifest and queries for the benchmark, built from the metadata the data_* scripts wrote.

Outputs (eval/data/): manifest.jsonl, queries.jsonl.  Committed summary: eval/datasets.json.
"""
import hashlib
import json
import random
import re
import subprocess
from collections import Counter

from common import DATA, EVAL, RAW, SEED, sha256_file, write_json

DEV_FRACTION = 0.3
NO_ANSWER_TARGET = 100

# --- keyword rule (fleurs_keyword) -------------------------------------------------------------
# Corpus = English reference sentences of ALL selected FLEURS clips (every language), one document
# each. Tokens: lowercase [a-z]+ words of length >= 3 (drops "s" from "'s" and similar fragments)
# minus STOPWORDS. IDF = ln(N / df). Take the highest-IDF word of the clip's own sentence (ties:
# longer word, then alphabetical). While more than one corpus sentence contains ALL chosen words
# and fewer than 3 are chosen, add the next highest-IDF word. Fully deterministic, no model involved.
STOPWORDS = set("""a about above after again against all also am an and any are aren as at be because been before being
below between both but by can cannot could couldn did didn do does doesn doing don down during each few for from further had
hadn has hasn have haven having he her here hers herself him himself his how i if in into is isn it its itself just let me
more most my myself no nor not now of off on once only or other ought our ours ourselves out over own same shan she should
shouldn so some such than that the their theirs them themselves then there these they this those through to too under until
up very was wasn we were weren what when where which while who whom why will with won would wouldn you your yours yourself
yourselves one two may many much often us said say says even still however upon within without among per via""".split())

WORD = re.compile(r"[a-z]+")


def tokens(text):
    return [w for w in WORD.findall(text.lower()) if len(w) >= 3 and w not in STOPWORDS]


def keyword_queries(sentences):
    """sentences: {key: english}. -> ({key: keyword query}, number of queries matching exactly one sentence)"""
    docs = {k: set(tokens(s)) for k, s in sentences.items()}
    df = Counter(w for d in docs.values() for w in d)
    out, unique = {}, 0
    for k, d in docs.items():
        ranked = sorted(d, key=lambda w: (df[w], -len(w), w))  # ascending df == descending IDF
        chosen = []
        for w in ranked:
            chosen.append(w)
            matches = sum(all(c in o for c in chosen) for o in docs.values())
            if len(chosen) == 3 or matches <= 1:
                break
        unique += matches == 1
        out[k] = " ".join(chosen)
    return out, unique


# --- no-answer queries ---------------------------------------------------------------------------
# Concepts present in the distractor photos (lake, food, cat, birthday cake, black puppy, beach,
# fireworks) plus close synonyms; a no-answer query must not mention any of them.
DISTRACTOR_WORDS = set("""lake pond water shore sea ocean sand wave coast beach seaside food meal dish dinner lunch breakfast pizza
burger sandwich pasta salad dessert snack cat kitten feline kitty cake birthday candle party cupcake puppy dog canine pup
black firework fireworks sparkler""".split())

# Candidates are deliberately about rare subjects; most still get rejected by the vocabulary filter
# (everyday verbs like "eating" are all over Charades), and that is the point of the filter.
CANDIDATES = """
a giraffe eating leaves
snowboarding down a mountain
an astronaut floating in a space station
a penguin waddling on ice
a surgeon performing an operation
a volcano erupting at night
a samurai swinging a katana
a submarine surfacing in the ocean
a kangaroo hopping across the outback
a hot air balloon drifting over fields
a violinist playing in an orchestra
a skateboarder performing a kickflip
a lion roaring on a rock
a train crossing a viaduct
a tractor plowing a field
an octopus squeezing through a gap
a flamingo standing on one leg
a rocket launching from a pad
a blacksmith hammering glowing metal
a chess grandmaster moving a piece
a dolphin leaping out of the water
a fencer lunging with a foil
an elephant spraying itself with a trunk
a tornado ripping through a town
a trombone player marching in a parade
a zebra crossing a savanna
a sailboat tilting in strong wind
a ballerina spinning on pointe
a bear catching salmon in a river
a diver exploring a coral reef
a helicopter landing on a rooftop
an archer drawing a longbow
a glacier calving into the sea
a camel walking through dunes
a cowboy riding a bucking bronco
a monk ringing a temple bell
a lighthouse beam sweeping the coast
a sheepdog herding a flock
a gondola gliding through a canal
a pilot adjusting cockpit instruments
a peacock spreading its feathers
a potter shaping clay on a wheel
a firefighter spraying a burning building
a rhinoceros charging through grass
a drummer performing a solo
a squirrel burying an acorn
a blimp floating above a stadium
a mechanic repairing a jet engine
a parrot mimicking human speech
a hockey goalie blocking a shot
a jellyfish pulsing in the dark
a windmill turning in a breeze
a skier jumping off a ramp
an owl swooping down at dusk
a surfer riding a barrel wave
a welder sparking in a workshop
a gorilla beating its chest
a kayak shooting rapids
a tightrope walker crossing a canyon
a falcon diving for prey
a magician pulling a rabbit from a hat
a bulldozer flattening rubble
an acrobat swinging on a trapeze
a seal balancing a ball on its nose
a lumberjack felling a tree
a snowman melting in the sun
a motorcycle racing around a track
a hummingbird hovering at a flower
an ice sculptor carving a swan
a javelin thrower releasing a spear
a beekeeper inspecting a hive
a crocodile snapping at a bird
a harpist plucking strings
a weightlifter hoisting a barbell overhead
an ambulance racing through traffic
a goat climbing a cliff
a baker kneading dough
a cheetah sprinting after a gazelle
a tennis player serving an ace
a sloth hanging from a branch
a bagpiper playing on a hill
an igloo glowing at twilight
a paraglider soaring above a valley
a raccoon rummaging in a bin
a rodeo clown distracting a bull
a locksmith picking a lock
a chameleon changing color
a conductor raising a baton
a hippopotamus yawning in mud
a rower pulling oars at dawn
a space shuttle re-entering the atmosphere
a bat flying out of a cave
a sumo wrestler stomping in a ring
a seagull stealing a sandwich
an excavator digging a trench
a toucan perched in a rainforest
a rugby scrum collapsing
a pelican scooping fish
a wolf howling at the moon
a glassblower shaping molten glass
an alligator gliding through a swamp
a cyclist climbing an alpine pass
a lobster crawling on the seabed
a tuba rehearsal in a band room
a dragon boat race on a river
a koala sleeping in a eucalyptus tree
a trapeze artist catching a partner
a whale breaching at sunrise
a bullfighter waving a cape
a yak grazing on a plateau
an accordion player busking
a lantern festival floating on a river
a mime pretending to be trapped in a box
a pogo stick jumping contest
a sled dog team racing through snow
a javelina crossing a desert road
a ferris wheel spinning at a fair
a hedgehog curling into a ball
a weaver working at a loom
a catapult hurling a boulder
a stingray gliding over sand flats
a monkey swinging through the jungle
a bobsled thundering down an ice track
an eagle landing on a branch
a rollercoaster plunging down a drop
a sword swallower at a circus
a llama spitting at a visitor
a pirate ship sailing in a storm
a mongoose fighting a cobra
a cuckoo clock striking noon
a trawler hauling in nets
a unicyclist juggling flaming torches
a meerkat standing guard
a tank rolling across a field
a banjo player on a porch
a manta ray swimming past a diver
a gymnast flipping on a balance beam
a dinosaur skeleton in a museum
a tugboat pulling a freighter
a squid glowing in deep water
a falconer releasing a hawk
a skyscraper being demolished
an otter floating on its back
a chandelier swaying during an earthquake
a bison thundering across a prairie
a hovercraft crossing a bay
a lamp-post knocked over by a storm
a medieval knight jousting
a trumpeter playing on a balcony
a pangolin rolling up
a fisherman casting a net from a boat
a paratrooper jumping from a plane
an iceberg drifting past a ship
a chimpanzee using a stick to fish for termites
a cellist tuning her instrument
a snail inching across a leaf
a sheep being sheared
a monorail gliding over a city
a comet streaking across the sky
a ladybug crawling up a stem
a cowbell clanging in an alpine meadow
a stagecoach racing across a desert
a bartender flipping a cocktail shaker
a walrus hauling out onto ice
a barn burning in a field
a thunderstorm over a wheat field
a typewriter clacking in an office
a gladiator fighting in an arena
a hawk circling above a canyon
a dentist drilling a tooth
a roller derby jam
a skydiver freefalling over a desert
a polar bear swimming between ice floes
a candlelit procession through a cathedral
a taxidermy fox in a glass case
a pinball machine lighting up
a street sweeper cleaning cobblestones
a seahorse clinging to seagrass
a mountain goat leaping between ledges
a mannequin falling in a shop window
a lunar rover driving across the moon
a cobbler stitching a boot
a tuk-tuk weaving through traffic
an ostrich sprinting across a plain
a tapestry being unrolled in a hall
a vulture waiting on a fence post
a harmonica solo in a dim bar
a moose wading through a marsh
a typhoon battering a pier
a jackhammer breaking asphalt
a gecko clinging to a ceiling
a snowplow clearing a highway
a pigeon landing on a statue
a mosaic being assembled from tiles
a hyena laughing in the grass
a rickshaw pulled along a street
a puffin carrying fish in its beak
a tarantula crawling over a hand
a treehouse swaying in the wind
a violin maker carving a scroll
a bonfire crackling on a moor
a cormorant diving under a pier
a bagel shop opening at dawn
a ski lift carrying passengers
a grasshopper leaping through a meadow
a dune buggy skidding in sand
a scarecrow in a cornfield
a starfish clinging to a rock
a bungee jumper leaping off a bridge
a lemur sunbathing on a branch
a clown car pulling into a ring
a cable car crossing a gorge
a swordfish leaping from the sea
a fiddler playing at a barn dance
a mule carrying packs up a trail
a porcupine raising its quills
an anvil ringing under a hammer
a stalactite dripping in a cave
a hamster running on a wheel
a gargoyle perched on a cathedral
a quail scurrying through brush
a pelota match in a fronton
a hot spring steaming in the snow
a sundial casting a shadow
a manatee grazing in a spring
a waterwheel turning at a mill
an alpaca being brushed
a lava lamp bubbling
a cuckoo calling in a forest
a trebuchet launching a stone
a tapir wading in a river
a unicycle race
an abacus being used by a shopkeeper
a barista pouring latte art
a telescope pointed at a nebula
a spelunker rappelling into a cavern
a sandcastle washed away by the tide
a harpoon thrown at a whale
a pretzel vendor on a corner
a bonsai being pruned
a stunt plane trailing smoke
a seaplane taking off from a fjord
a ferret darting through a tube
a kimono being folded
a gramophone playing a record
a jaguar stalking through undergrowth
an origami crane being folded
a cactus blooming in the desert
a topiary hedge shaped like a rabbit
a vending machine dispensing a can
a weather balloon rising into the stratosphere
a metronome ticking on a piano
a cuckoo bird laying an egg
a giraffe nibbling acacia leaves
a pair of penguins waddling
an armadillo scurrying through brush
a narwhal surfacing among floes
a platypus paddling in a creek
a wombat burrowing under a log
an axolotl drifting in a tank
a capybara soaking in a hot pool
an okapi browsing in the rainforest
a pangolin foraging at night
a tortoise plodding across a garden
a heron stalking a frog
a stork nesting on a chimney
an anteater probing a termite mound
a lynx prowling through snowy woods
a cockatoo screeching from a perch
a mandrill baring its teeth
a beaver gnawing a log
a seal pup basking on a boulder
a chinchilla bathing in dust
a woodpecker hammering a trunk
a gazelle bounding through tall grass
a vole darting between roots
a barn swallow feeding chicks
a dragonfly hovering above a pond
a firefly blinking in a dark meadow
a scorpion raising its stinger
a praying mantis catching a moth
a hermit crab dragging its shell
a barracuda cruising past a wreck
a clownfish hiding in an anemone
a moray eel poking out of a crevice
a piranha tearing at bait
a trout leaping up a weir
a marmot whistling on a ridge
a reindeer pulling a sleigh
a husky howling in a blizzard
a bighorn sheep butting heads
a wildebeest migration at a river crossing
a warthog trotting with its tail up
an ibis wading through reeds
a kookaburra laughing in a gum tree
a cassowary striding through jungle
a tamarin leaping between vines
a gibbon singing at dawn
a swan cygnet riding on its mother
a quokka smiling at the camera
a samovar steaming on a table
a sextant being used on a ship deck
a zeppelin docking at a mast
a periscope rising from the waves
a gazebo covered in wisteria
a minaret at sunrise
a pagoda reflected in a moat
a drawbridge being raised
a portcullis crashing down
a catamaran skimming across a bay
a gondolier singing
a carousel horse spinning
a pickaxe striking a seam of coal
a telegraph operator tapping a key
a printing press stamping a page
a spinning wheel turning wool
a yurt in a steppe
a totem pole being raised
a sarcophagus being opened
a monolith in a desert
a bagpipe band marching
a didgeridoo player at a festival
a tabla duet
a sitar recital
a xylophone being struck
a kazoo band
a theremin glowing
a bassoon rehearsal
a clarinet solo in a jazz club
a tambourine shaken in a street
a gong struck by a mallet
a kite surfer jumping a swell
a luge athlete hurtling down a chute
a curling stone sliding across a sheet
a biathlon skier aiming a rifle
a pole vaulter clearing a bar
a shot putter spinning in a circle
a lacrosse player cradling a stick
a cricket bowler delivering a ball
a polo rider swinging a mallet
a dressage horse trotting sideways
a mountaineer planting an ice axe
a stuntman falling from a ledge
a crepe being flipped in a pan
a wok flaming in a street stall
a fondue pot bubbling
a ramen chef straining noodles
a pastry chef piping eclairs
a sommelier decanting wine
an aurora shimmering above a cabin
a meteor shower over a desert
a rainbow arching over a waterfall
a sandstorm engulfing a caravan
an avalanche rumbling down a slope
a geyser erupting at dawn
a swarm of locusts descending on crops
a murmuration of starlings
a mirage above a salt flat
a wildfire racing up a hillside
a blizzard burying a village
a hailstorm shredding leaves
a drone delivering a parcel
a humanoid robot climbing stairs
a satellite dish rotating
a circuit board being soldered
a hologram projected above a desk
a centrifuge spinning in a lab
a wind turbine blade being lifted
an oil rig at sunset
a forklift stacking pallets
a cement mixer turning
""".strip().splitlines()


def norm(word):
    """Crude plural strip, applied identically to vocabulary and candidates."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def variants(word):
    """norm() plus -ing/-ed stripped forms, so 'snowboarding' is caught by a vocabulary 'snowboard'."""
    base = norm(word)
    out = {base}
    for suf in ("ing", "ed"):
        if base.endswith(suf) and len(base) > len(suf) + 2:
            out.add(base[:-len(suf)])
    return out


def content_words(text):
    return [w for w in WORD.findall(text.lower()) if w not in STOPWORDS and len(w) >= 3]


def filter_no_answer(candidates, vocab):
    vocab_norm = {norm(w) for w in vocab}
    kept, rejected = [], Counter()
    seen = set()
    for c in candidates:
        c = c.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        words = content_words(c)
        if any(v in DISTRACTOR_WORDS or norm(v) in DISTRACTOR_WORDS for w in words for v in variants(w)):
            rejected["distractor_concept"] += 1
        elif any(v in vocab_norm for w in words for v in variants(w)):
            rejected["in_dataset_vocabulary"] += 1
        else:
            kept.append(c)
    return kept, rejected


# --- assembly --------------------------------------------------------------------------------------
def split_ids(ids, rng):
    """Shuffle a sorted id list and cut off the first 30% as dev."""
    ids = sorted(ids)
    rng.shuffle(ids)
    n_dev = round(len(ids) * DEV_FRACTION)
    return {i: ("dev" if k < n_dev else "test") for k, i in enumerate(ids)}


def jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    cha = json.load(open(DATA / "charades_meta.json"))
    fl = json.load(open(DATA / "fleurs_meta.json"))
    rng = random.Random(SEED)  # one stream, consumed in a fixed order below

    # Charades: split by video id.
    cha_clips = {c["video_id"]: c for c in cha["clips"]}
    cha_split = split_ids(cha_clips, rng)
    manifest = [{"file": f"clips/charades/{v}.mp4", "name": f"{v}.mp4", "source": "charades", "lang": None,
                 "split": cha_split[v], "duration": cha_clips[v]["duration"], "video_id": v}
                for v in sorted(cha_clips)]

    # FLEURS: split by sentence id, separately per language so every language keeps a 30/70 mix.
    fl_clips = []
    for lang in ("hi_in", "mr_in", "en_us"):
        group = [c for c in fl["clips"] if c["lang"] == lang]
        sp = split_ids([c["sentence_id"] for c in group], rng)
        for c in sorted(group, key=lambda c: int(c["sentence_id"])):
            c["split"] = sp[c["sentence_id"]]
            fl_clips.append(c)
            manifest.append({"file": f"clips/fleurs/{lang}_{c['sentence_id']}.mp4",
                             "name": f"{lang}_{c['sentence_id']}.mp4", "source": "fleurs", "lang": lang,
                             "split": c["split"], "duration": c["duration"], "video_id": None,
                             "sentence_id": c["sentence_id"], "english": c["english"], "native": c["native"]})
    jsonl(DATA / "manifest.jsonl", manifest)

    queries = []
    # charades_sentence: qid is the line number in the test annotation file, so it is traceable.
    ann = (RAW / "charades" / "charades_sta_test.txt").read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(ann):
        head, sent = line.split("##", 1)
        vid, start, end = head.split(" ")
        if vid in cha_clips:
            queries.append({"qid": f"cha-{n:06d}", "query": sent.strip().rstrip(".").rstrip(),
                            "type": "charades_sentence", "split": cha_split[vid], "gold": f"{vid}.mp4",
                            "window": [float(start), float(end)], "lang": None})

    kw, kw_unique = keyword_queries({c["sentence_id"]: c["english"] for c in fl_clips})
    for c in fl_clips:
        gold = f"{c['lang']}_{c['sentence_id']}.mp4"
        base = {"split": c["split"], "gold": gold, "window": [0, c["duration"]], "lang": c["lang"]}
        queries.append({"qid": f"flsent-{int(c['sentence_id']):05d}", "query": c["english"],
                        "type": "fleurs_sentence", **base})
        queries.append({"qid": f"flkw-{int(c['sentence_id']):05d}", "query": kw[c["sentence_id"]],
                        "type": "fleurs_keyword", **base})

    # No-answer vocabulary: Charades train+test sentences, and English text of every FLEURS tsv.
    vocab = set()
    for name in ("charades_sta_train.txt", "charades_sta_test.txt"):
        for line in (RAW / "charades" / name).read_text(encoding="utf-8").splitlines():
            vocab.update(WORD.findall(line.split("##", 1)[1].lower()))
    for lang in ("hi_in", "mr_in", "en_us"):
        for line in (RAW / "fleurs" / "data" / lang / "test.tsv").read_text(encoding="utf-8").splitlines():
            vocab.update(WORD.findall(" ".join(line.split("\t")[2:4]).lower()))
    kept, rejected = filter_no_answer(CANDIDATES, vocab)
    if len(kept) > NO_ANSWER_TARGET:  # keep ~100: seeded sample, original order preserved
        keep = set(random.Random(SEED).sample(range(len(kept)), NO_ANSWER_TARGET))
        rejected["surplus_trimmed"] = len(kept) - NO_ANSWER_TARGET
        kept = [q for i, q in enumerate(kept) if i in keep]
    sp = split_ids(range(len(kept)), rng)
    for k, q in enumerate(kept):
        queries.append({"qid": f"noans-{k:03d}", "query": q, "type": "no_answer", "split": sp[k],
                        "gold": None, "window": None, "lang": None})
    jsonl(DATA / "queries.jsonl", queries)

    write_datasets_json(cha, fl, manifest, queries, keyword_stats=(len(fl_clips), kw_unique),
                        no_answer={"candidates": len(set(c.strip() for c in CANDIDATES if c.strip())),
                                   "rejected": dict(rejected), "kept": len(kept),
                                   "vocabulary_words": len(vocab)})
    print_summary(manifest, queries)


def count(rows, *keys):
    c = Counter(" / ".join(str(r[k]) for k in keys) for r in rows)
    return dict(sorted(c.items()))


def write_datasets_json(cha, fl, manifest, queries, keyword_stats, no_answer):
    n_docs, kw_unique = keyword_stats
    hours = {}
    for m in manifest:
        hours[m["source"]] = hours.get(m["source"], 0) + m["duration"]
    write_json(EVAL / "datasets.json", {
        "note": "Metadata only. Data rows live in eval/data/ (gitignored) and are rebuilt by `make eval-data`.",
        "seed": SEED,
        "split": {"dev_fraction": DEV_FRACTION,
                  "rule": "shuffle sorted ids with random.Random(2026), first round(0.3*n) are dev. Charades by video id; "
                          "FLEURS by sentence id, separately per language; no_answer queries shuffled likewise."},
        "tools": {"ffmpeg": fl["ffmpeg"]},
        "sources": {
            "charades_sta": {
                "description": "Charades-STA moment annotations (Gao et al., TALL, ICCV 2017) over Charades videos (Sigurdsson et al., 2016)",
                "annotations": cha["annotations"],
                "videos": cha["videos"],
                "license": "Charades videos and annotations are for non-commercial research use only "
                           "(https://prior.allenai.org/projects/charades). Scripts are committed; clips are never redistributed.",
                "selection": f"{cha['n_videos']} distinct video ids out of {cha['n_test_videos_available']} in the test annotation file, "
                             "random.Random(2026).sample over the sorted id list; ALL queries of those videos are kept.",
                "clips_fingerprint_sha256": fingerprint(c["video_id"] + c["sha256"] for c in cha["clips"]),
            },
            "fleurs": {
                "description": "FLEURS (Conneau et al., 2022), test split, one recording per FLoRes sentence id",
                **fl["sources"],
                "license": "CC BY 4.0 (https://huggingface.co/datasets/google/fleurs)",
                "selection": f"{fl['ids_in_all_languages']} sentence ids occur in the test tsv of hi_in, mr_in and en_us; "
                             "sorted numerically, shuffled with random.Random(2026), cut into 3 disjoint equal sets "
                             f"({fl['n_per_lang']} each, cap 200): hi_in, mr_in, en_us (in-language control). "
                             "Recording = first row for the id in tsv order. English reference = en_us raw_transcription "
                             "for the same id; native = that language's raw_transcription.",
                "wrap_cmd": fl["ffmpeg_cmd"],
                "clips_fingerprint_sha256": fingerprint(c["lang"] + c["sentence_id"] + c["sha256"] for c in fl["clips"]),
            },
        },
        "counts": {
            "clips": count(manifest, "source", "lang", "split"),
            "queries": count(queries, "type", "split"),
            "queries_by_lang": count([q for q in queries if q["type"].startswith("fleurs")], "type", "lang", "split"),
        },
        "total_duration_seconds": {k: round(v, 1) for k, v in hours.items()},
        "query_types": {
            "charades_sentence": "Charades-STA sentence, trailing period stripped; window = annotated [start, end] seconds; "
                                 "qid number = line index in charades_sta_test.txt.",
            "fleurs_sentence": "full English reference sentence; window = [0, clip duration]; lang = clip language.",
            "fleurs_keyword": {"rule": "see build_queries.py header: 1-3 words by IDF over the English sentences of all "
                                       "selected FLEURS clips (lowercase [a-z]{3,}, hardcoded stopword list), adding words "
                                       "until the combination is contained in at most one corpus sentence.",
                               "corpus_documents": n_docs,
                               "queries_matching_exactly_one_sentence": kw_unique},
            "no_answer": {"rule": "hand-written candidates, rejected if any content word (crude plural/-ing/-ed normalisation) "
                                  "occurs in Charades-STA train+test sentences or in the text of the three FLEURS test tsvs, "
                                  "or names a distractor-photo concept; gold and window are null.",
                          **no_answer},
        },
        "outputs_sha256": {n: sha256_file(DATA / n) for n in ("manifest.jsonl", "queries.jsonl")},
    })


def fingerprint(parts):
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()


def print_summary(manifest, queries):
    print("clips per source/lang/split:")
    for k, v in count(manifest, "source", "lang", "split").items():
        print(f"  {k}: {v}")
    print("queries per type/split:")
    for k, v in count(queries, "type", "split").items():
        print(f"  {k}: {v}")
    out = subprocess.run(["du", "-sh", str(DATA)], capture_output=True, text=True).stdout.split()[0]
    print("eval/data size:", out)


if __name__ == "__main__":
    main()
