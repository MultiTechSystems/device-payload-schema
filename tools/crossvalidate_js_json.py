#!/usr/bin/env python3
"""crossvalidate_js_json.py - diff interpreter JSON against the generated TS013 codec.

Every other check in this repository compares decoded *values*. This compares the
serialized **JSON**, token by token, so a difference in representation shows up rather
than being normalized away by the comparison itself. `15` and `15.0` are the same
value and different JSON, and only the second matters to a consumer.

That is the acceptance test for replacing a deployed JS codec with another decoder: a
gateway that swaps codecs must not change its output. See CR-2026-008.

The Python side is normalized per CR-2026-008 rule 1 - an integral numeric value
renders without a fraction - so what this reports is what remains *after* that rule,
which is how the rule was shown to be sufficient. Measured 2026-08-10: 1117 identical,
14 value mismatches (all of them the idiv/mod convention of CR-2026-007, where the JS
generator truncates and the interpreter floors), 8 differing only in which keys are
present.

Those 8 are TS013 *generator* gaps, not interpreter gaps - the generator does not
implement `name_from`, drops `repeat` items, misses some TLV channels, and does not
emit all members of a nested `object`. Worth knowing before promising that a generated
codec and the interpreter are interchangeable.

Usage:
    .venv/bin/python tools/crossvalidate_js_json.py          # whole device corpus
Requires `node` on PATH.
"""
import json, subprocess, sys, glob, tempfile, os, collections
sys.path.insert(0, "tools")
import yaml
from schema_interpreter import SchemaInterpreter

def normalize(v):
    """Identity. The interpreter applies CR-2026-008 itself now.

    This used to apply the rules here, which is how their sufficiency was measured
    before they were implemented. Keeping it as a no-op rather than deleting the call
    sites makes the point explicit: what this tool reports is the interpreter's own
    output, unaltered, against the generated codec's.
    """
    return v

def tokens(js_text):
    """Map key -> literal token as written, so 15 and 15.0 are distinguishable."""
    out = {}
    def hook(pairs):
        d = {}
        for k, v in pairs:
            d[k] = v
            if isinstance(v, str) and v.startswith("\x00tok:"):
                out[k] = v[5:]
            elif not isinstance(v, (dict, list)):
                out[k] = v
        return d
    json.loads(js_text,
               parse_int=lambda s: "\x00tok:" + s,
               parse_float=lambda s: "\x00tok:" + s,
               object_pairs_hook=hook)
    return out

results = collections.Counter()
detail = []
schemas = sorted(glob.glob("schemas/devices/*/*.yaml"))
for path in schemas:
    if "-codec.js" in path or "_library-composed" in path:
        continue
    try:
        doc = yaml.safe_load(open(path))
    except Exception:
        continue
    if not isinstance(doc, dict) or not doc.get("test_vectors"):
        continue
    codec = tempfile.NamedTemporaryFile(suffix=".js", delete=False).name
    gen = subprocess.run([".venv/bin/python", "tools/generate_ts013_codec.py", path, "-o", codec],
                         capture_output=True, text=True)
    if gen.returncode != 0:
        results["codec-gen-failed"] += 1; os.unlink(codec); continue
    for tv in doc["test_vectors"]:
        payload = (tv.get("payload") or "").replace(" ", "")
        if not payload:
            continue
        port = tv.get("fPort", tv.get("fport")) or 1
        runner = f"""
const fs=require('fs'); eval(fs.readFileSync({json.dumps(codec)},'utf8'));
const hex={json.dumps(payload)}; const b=[];
for(let i=0;i<hex.length;i+=2) b.push(parseInt(hex.substr(i,2),16));
try {{ const r=decodeUplink({{bytes:b,fPort:{int(port)}}}); console.log(JSON.stringify(r.data)); }}
catch(e) {{ console.log('__ERR__'+e.message); }}
"""
        pr = subprocess.run(["node", "-e", runner], capture_output=True, text=True, timeout=30)
        js_out = pr.stdout.strip()
        if not js_out or js_out.startswith("__ERR__"):
            results["js-error"] += 1; continue
        try:
            si = SchemaInterpreter(doc)
            r = si.decode(bytes.fromhex(payload), fPort=int(port))
        except Exception:
            results["py-error"] += 1; continue
        py_norm = normalize(r.data)
        py_tok = tokens(json.dumps(py_norm))
        js_tok = tokens(js_out)
        common = set(py_tok) & set(js_tok)
        mism = [k for k in common if str(py_tok[k]) != str(js_tok[k])]
        only_py = set(py_tok) - set(js_tok)
        only_js = set(js_tok) - set(py_tok)
        if mism:
            results["value-mismatch"] += 1
            for k in sorted(mism)[:2]:
                detail.append(("VALUE", os.path.basename(path), k, js_tok[k], py_tok[k]))
        elif only_py or only_js:
            results["keys-differ-only"] += 1
            for k in sorted(only_py)[:1]:
                detail.append(("EXTRA-PY", os.path.basename(path), k, "-", py_tok[k]))
            for k in sorted(only_js)[:1]:
                detail.append(("EXTRA-JS", os.path.basename(path), k, js_tok[k], "-"))
        else:
            results["identical"] += 1
    os.unlink(codec)

print("=== Python interpreter (raw output) vs generated TS013 codec ===")
for k, v in results.most_common():
    print(f"  {k:22s} {v}")
print()
print("first divergences:")
for kind, sch, key, js, py in [d for d in detail if d[0]=="VALUE"][:14]:
    print(f"  {kind:9s} {sch:28s} {key:24s} js={js!s:22s} py={py!s}")
