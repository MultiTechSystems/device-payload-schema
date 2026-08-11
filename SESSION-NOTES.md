# Session Notes

## RESUME HERE - state at the end of 2026-08-11

**Everything is committed**, on a topic branch in each repo rather than the default
branch:

| Repo | Branch | Commits ahead of default |
|---|---|---|
| `payload-codec-proto` | `session/2026-08-10-language-alignment` | 8 (plus the 16 pre-existing on `master`) |
| `la-payload-schema` | `session/2026-08-10-cr-006-007-008` | 4 (plus 1 pre-existing on `main`) |

Neither branch is pushed. Merge or rebase onto `master`/`main` as you prefer - the repo
convention has been to keep everything on `master`, so a fast-forward is fine; the branch
exists so nothing landed on the default branch unreviewed.

Green as of the last run: Python 1789 passed / 4 skipped, Go, Java and C# corpus runners
at the full **1189**, C selftests pass, `compose_library_vectors.py --check` clean, and
zero regressions against a freshly regenerated `score-baseline.json`.

Corpus 1166 -> 1189 over the two days. Tiers: BRONZE 3, SILVER 76, GOLD 40, PLATINUM 30,
REJECTED 69 of 218.

**Done on 2026-08-11**, all five items from yesterday's list:

1. Committed - in four groups per repo rather than the nine I had sketched. The nine were
   not achievable: each hub file (`schema_interpreter.py`, `validate_schema.py`,
   `generate_ts013_codec.py`, `go/schema/schema.go`) carries two to four of those topics,
   so they cannot be split without hunk-level surgery, and the intermediate states would
   not have built. The specification repo *did* split cleanly, one commit per CR.
2. **CR-2026-008 implemented and moved to `implemented/`.** Normalization runs once
   where `decode()` returns, so no decode path can bypass it. Java narrowed too, since a
   binding returning native values reaches the rendering rule through its reported type.
3. **`score-baseline.json` regenerated** - zero regressions against it now.
4. **`round` made decimal-correct in Go and C#.**
5. **Nested `object` support in the TS013 generator**, which restored ct303/ct305/ct310
   to SILVER.

**Four bugs found by doing that work, none of them anticipated:**

- **The encoder silently zeroed a `bytes` field.** Once the decoder reported hex,
  `encode(decode(payload))` turned `deadbeef0064` into `000000000064` with no error,
  because `_encode_field` fell through to `bytes(length)` for anything that was not
  already a bytes object. PS-281 needed the symmetric change; the encoder now takes a
  bytes object, a hex string or an octet list and reports anything else.
- **The output-schema generator was wrong for four constructs** - `bitfield_string` and
  `version_string` declared "number" while reporting "v1.2.52" (280 of the mismatches),
  `repeat` and `object` also fell through to "number", and a key produced by more than
  one branch took whichever branch the walk saw last. Found by checking every decoded
  corpus value against its declared type: 2718 values, now zero mismatches.
- **The TS013 generator advanced past every bitfield.** `consume` defaulted to 1 against
  PS-060, so several bitfields sharing a byte each moved on and read different bytes.
- **A plain typed field in Go dropped its bare modifier when it also had a transform** -
  `div: 1000` silently ignored, reporting 2355 instead of 2.35. The same either/or was
  fixed on the ref and compute paths earlier and this branch was missed.

Also corrected a sentence in CR-2026-008 that contradicted its own normative table: it
claimed the output-schema generator should stop demoting a modified field to "number",
where the table says a modified field *is* a number. The generator was already right.

**Where to pick up, highest value first:**

1. **Push the two branches** and decide whether they merge to `master`/`main`. Also the
   three `td-tools` branches still have no PRs, and the catalog fix should go first.
2. **The remaining TS013 generator gaps.** `lookup: default`, `$ref` splicing and
   `$field` resolution are done. The cross-check stands at **1134 identical, 1 value
   difference, 5 key-set differences** (`tools/crossvalidate_js_json.py`).
   `name_from` and `repeat` are done too. The cross-check is at **1137 identical, zero
   value differences, 3 key-set differences**. Still open: some `ers` TLV channels and
   rbs30x's empty `stored_downlink` - both key-set only.
   The `repeat` fixtures now pin the record shape in their `expected` blocks. They did
   not before, which is exactly why the generator could omit the construct entirely
   while all four runners passed.
   **`vars` is now the variable table and `d` is only the reported output.** Six
   emitters record into `vars`, all of them post-modifier, matching the interpreters -
   the two plain-type paths, the bit-range path, the byte_group member path, the float
   path (post-modifier), the enum path (the mapped label, not the raw integer) and the
   computed-field path (which previously wrote `d` alone). If you add an emitter, write
   `vars` too or every `$ref` to that field silently resolves to undefined.
   Two lessons from getting this wrong the first time: switching the references without
   fixing what `vars` holds loses 15 comparisons, and the computed-field path is the easy
   one to miss because it has six separate `d.{name} =` sites.

3. **A round-trip/encode corpus.** Still the least-tested part of the project and the MCU
   tier's whole job: every vector is decode-only, `src/test_encoder.c` is in no build
   target, and Java and C# have no encoder at all.
4. **The C gateway front-end** - YAML reader and JSON writer. `result_to_json()`'s `%g`
   destroys precision and `schema_create_yaml()` is a stub.
5. Then items 3 onward in the Next list below: the two Decentlab hints, the preprocessor
   in validate/score, provenance for the three Silver-capped schemas, `name_from` for
   milesight, and the milesight vector counts.

## Session: Aug 10, 2026

Four items from the previous "Next" list: the bitfield form, `header:`,
`KNOWN-ISSUES.md` and the maths transforms. All four done. Corpus 1166 -> 1177, and
every implementation is at the full count.

### Completed

**CR-2026-006: the bracket range is the only bitfield spelling**
- Withdrew `u8[3+:2]`, `bits<3,2>`, `bits:2@3` and the sequential `u8:2` from the
  spec, the Python interpreter, both binary encoders, the JS and TS013 generators and
  the C header. Go, Java and C# went from 1163 to the full corpus without gaining a
  line of bitfield code, which was the point.
- The rewrite recipe in AGENTS.md was incomplete: translating `u8:3`+`bit_offset: 5`
  to `u8[5:7]` keeps every value and silently drops `bytes_consumed` from 1 to 0,
  because the sequential form auto-advanced at bit 0. Each byte's last field needs
  `consume: 1`. Verified by decoding all 256 one-byte payloads through each of the
  four affected definitions before and after - 1024 decodes, identical including read
  position.
- The form was worse than "Python-only": `binary_schema_v2.py` and
  `generate_js_decoder.py` resolved it to start bit 0 (bits 0-2 where the interpreter
  reads 5-7), and the C header set a sentinel of 255 that no decode path read. Three
  implementations accepted it and produced the wrong answer.
- `lorawan_frames.yaml` had **four** affected definitions, not three as noted.
- Corrected two stale claims: `SPEC-IMPLEMENTATION-STATUS.md` said `u8:4` worked in
  all five implementations, and `BITFIELD-SHORTHAND.md` documented *seven* spellings
  where the spec listed five - the doc and the spec had disagreed for months.

**`header:` removed, and `$ref` implemented in Java**
- Measured the divergence rather than assuming it: a two-byte probe showed Java
  reporting both fields while Python and Go reported no header field *and* read the
  header's byte as the first field. Nothing errored. Used by 0 of 241 schemas.
- Removed from Java and C#. `validate_schema.py` now reports a top-level `header:`,
  because deleting it silently would turn a two-language divergence into four
  languages quietly losing a schema's first fields.
- The conformance fixture for the replacement immediately found **`$ref`
  unimplemented in Java** - no `definitions` parsing, no resolution - in the binding
  whose users were being pointed at it. Implemented by splicing at parse time, for
  schema-level and per-port field lists.

**KNOWN-ISSUES.md: seven quarantined vectors -> zero, file gone**
- Root cause of several: `lorawan_frames.yaml` and `lorawan_mac_commands.yaml` both
  say "little-endian" in their header comments and TS001 requires it, but neither set
  `endian: little`, so both composed as big-endian.
- `type: array` never existed; `repeat` + `until: end` expresses exactly the same
  thing and already worked everywhere. Another "missing feature" that was not.
- Three payloads had never been executed and matched their own expected values under
  no scaling at all. Payloads recomputed from the expected values, which carry the
  intent, and marked `source: generated`.
- A `bytes` field has no agreed output representation - Python returns bytes, Go a
  hex string. Vectors now use hex. **The disagreement itself is unresolved.**
- `compose_library_vectors.py` now force-quotes strings: PyYAML left a hex EUI
  unquoted and Go's parser read it as 1.02030405060708e+14, so one vector passed and
  its identically-shaped neighbour failed.
- The Java and C# runners did not descend into lists or maps; C# could not express a
  nested expectation at all. Both now recurse per PS-044/PS-045.

**Unary maths transforms in Go, Java and C#; dl-blg converted**
- `sqrt`, `abs`, `pow`, `log10`, `log` added to all three, with Python's domain
  clamps. New fixture `_language-conformance/transform-maths.yaml`.
- Go's plain-field transform branch was a drifted copy of the shared loop: `add`
  before `mult` against PS-101, and no `{op: round}`. Routed through
  `applyTransformStages`.
- **A compute field's transform ran twice in Go and C#** - both skipped
  re-application for `ref` and forgot `compute`. Found only because dl-blg gained
  vectors.
- `dl-blg` converted using `log` plus a `polynomial` over ln(R) for the
  Steinhart-Hart cubic, and cross-validated against the vendor decoder: all four
  fields match, temperature to the 14th significant figure. Two vectors added from
  the vendor's own examples, `source: vendor-codec`.

### Next

1. ~~The four corpus runners are more permissive than the specification.~~ **Done.**
   PS-039 requires integers to match *exactly* and PS-040's 0.001 is an absolute
   float tolerance; Go, Java and C# applied a relative
   `max(0.001, |want|*0.001)` to everything, about 20 days of slack on a GPS
   timestamp. All three now compare an integer expectation exactly and a float
   within an absolute 0.001, and `values_match` was made explicit about the integer
   case too. Zero regressions - the corpus stayed at 1177 in all four, as the
   earlier measurement predicted. Python's runner was already close, since it used
   `values_match`'s absolute tolerance rather than a relative one.
   `TestConformanceComparisonIsExactForIntegers` guards it with the real ts003
   numbers, asserting first that the old relative rule *did* accept a 2048-second
   error.
2. ~~Decide what type a field reports.~~ **CR-2026-008 submitted.** One question with
   three measured faces: `bytes` is a Python bytes object and a Go hex string; Java
   widens a plain `u8` to `Double` where Python keeps `int`; and an integral float
   renders `15.0` from Python but `15` from JavaScript, because `JSON.stringify` drops
   a zero fraction - **304 of 2850 corpus fields (10.7%) across 60 schemas**, and our
   own generated TS013 codec disagrees with our own interpreter on the same schema and
   bytes.
   Tested in Docker against real consumers: **rapidjson asserts** on `GetUint()` of
   `5.0` (it will not narrow a double), **jsoncpp converts but silently truncates**
   `22.5` to 22, and draft-07 `"type": "integer"` *matches* `5.0`, so schema validation
   catches neither.
   **The C gateway interpreter settles the direction.** It exists to replace deployed
   JS codecs, and a gateway that swaps codecs must not change its JSON - so the rule
   follows JavaScript (`15`, not `15.0`) and **Python is the implementation that
   changes**. An earlier draft of these notes had this the other way round.
   The CR also adds `type: integer` for computed fields, performing no rounding of its
   own: `idiv` truncates and `{op: round, decimals: 0}` rounds, both already working,
   so folding rounding into a type would give two spellings for one operation - the
   defect CR-2026-006 just removed. Note `type: u32` is *not* an output declaration
   today; it is read as a four-byte read.
   Out of scope and worth its own look: an unmatched `enum` still reports
   `unknown(2)` where PS-269 has `lookup` omit the field, so the two constructs
   disagree.
3. **The two remaining Decentlab hints**: `dl-zn2` is mechanical; `dl-iam` needs a
   `max` of two computed fields, which does not exist. Beware the naming: `floor:`
   and `ceiling:` in a transform are *clamps*, not rounding - `floor: 0` means
   `max(value, 0)`.
3a. ~~CR-2026-007 submitted.~~ **Implemented and accepted: `idiv`/`mod` are floored.**
   Applied to Python, Go, Java, C# and the **TS013 JS generator**; a zero divisor now
   omits the field in all five (`div` too) instead of emitting `NaN`, erroring or
   throwing. Spec text applied to `03-modifiers.md`; CR moved to `implemented/`.
   All four corpus runners are back at the full **1188**, and the JSON cross-check went
   from 14 numeric divergences against the generated codec to **0** for these
   operators.
   Implementation notes worth keeping:
   - **Do not** write floored division as `(int64)floor((double)a/b)` - inexact above
     2^53 (verified: 2^53+1 gives ...92 not ...93). Correct on the integers:
     `q = a/b; if (a%b != 0 && ((a<0) != (b<0))) q--;`. The remainder is
     `((a%b)+b)%b`, exact and divisor-signed. Go and C# carry both helpers now.
   - Java needed only `Math.floorMod` beside its existing `Math.floorDiv` - a one-word
     fix for an inconsistency that had made `a == idiv*b + mod` fail in Java alone.
   - Omission reuses each implementation's existing absent-field path: Python's
     `OMITTED`, Go's `omitted` sentinel, `null` in Java, `null` in C#, and `undefined`
     in generated JS (not `NaN`, because `JSON.stringify` writes NaN as `null`, and
     null is not what absent means).
   - Four existing tests asserted the old behaviour (two Python, two Go) and were
     inverted to assert omission plus *decoding continues past it*, which is the part
     that actually matters.
4. **`src/test_comprehensive.c` is in no build target** - 160 checks run nowhere, 2
   of them failing on stale `unknown(N)` expectations PS-269 replaced. Wire it in or
   fix the assertions.
4a. **The C implementation has two targets that differ in I/O, not semantics** - so
   one core with two front-ends, not two implementations. `schema_interpreter.hpp` is
   already this pattern.
   - **MCU (Zephyr)**: binary schema in, native C values out, uplink packing and
     downlink command reading. Never sees YAML or JSON - the header has zero
     occurrences of `json`. Its reduced feature set is therefore correct, not a gap,
     and every output-representation question is irrelevant to it. Missing: v2
     structural opcodes in `schema_load_binary`, which today reads v1 flat fields only
     while the Python side already emits v2 with MATCH/VAR.
   - **Gateway (embedded Linux)**: **YAML in, JSON out**, TS013 `{data, warnings,
     errors}`, to replace deployed JS codecs. Missing all three of those. libyaml is
     fine here - an earlier draft of these notes argued against it to protect the
     18.6 KB figure, which is an MCU concern misapplied to a box with megabytes.
   The JSON writer is the piece that decides success, and CR-2026-008 is its contract.
   With YAML in C the corpus runner walks `schemas/devices/` like the other four, no
   host-side fixture generation. **Build the runner before features** - C is invisible
   to the corpus, which is why its dead bitfield sentinel and unbuilt test files
   survived. For this target the acceptance test is a **JSON diff against the generated
   TS013 codec**, not just the expected-value blocks; that catches `15` vs `15.0` on
   the first run.
   Feature order by corpus usage: `tlv`/`flagged`, then `transform`, then
   `compute`/`ref`/`polynomial`, then `repeat`/`until`.
4b. **Encoding is the least-tested part of the project, and it is the MCU tier's whole
   job.** All 1188 vectors are decode-only; none asserts an encoded payload. C has a
   full encoder and `src/test_encoder.c` is in no build target. Python and Go have
   encoders; **Java and C# have none** - Java's only `encode` hit is
   `Base64.getEncoder()`. PS-061..PS-064 and the reverse of canonical modifier order
   are normative and untested cross-language. Cheap fix: round-trip the existing
   vectors (values back through `encode`, assert the original bytes), with a per-vector
   opt-out for the lossy constructs - `lookup`, `hex` case, guard fallbacks, unmatched
   enums.
4c. **Four of eight C test files are in no build target** (`test_comprehensive.c`,
   `test_interpreter.c`, `test_binary_schema.c`, `test_encoder.c`), and six compiled
   executables are tracked in git under `src/`.
4d. **The TS013 generator's rounding is fixed; its feature gaps remain.**
   `tools/crossvalidate_js_json.py` reports **1126 identical**, 8 key-set differences
   and 5 value differences, none of them rounding or idiv/mod any more.
   The generator now emits a `roundHalfEven` helper instead of `Math.round`, which was
   half-up and asymmetric for negatives. Two traps went into that helper, both worth
   keeping in mind for any other language:
   - Rounding `v * 10^d` is **not** the same as rounding `v` at `d` decimals. 2.355 is
     stored as 2.35499999999999998, but `2.355 * 100` lands on exactly 235.5, so the
     multiply manufactures a tie and rounds up. `toFixed` is correctly rounded on the
     stored value, so it handles the ordinary case.
   - Detecting a genuine tie needs the long expansion, not a round-trip test:
     `Number("2.345") === 2.345` is true because that is the shortest representation,
     while the stored value is above the half. The helper reads `toFixed(d+19)` and
     requires a 5 at the cut with only zeros beyond.
   Verified 24/24 against Python's `round()`, including negative halves and the
   0.5/1.5/2.5/3.5 sequence.
   **New pre-existing bug found doing this: Go's `round` transform diverges from
   Python, Java and C#.** Go uses `math.RoundToEven(v*scale)/scale`, so it inherits
   the multiply error: 2.355 -> 2.36 and 2.675 -> 2.68, where the other three give
   2.35 and 2.67. Three of four are decimal-correct and Go is the outlier. No corpus
   vector catches it yet.
   Remaining generator feature gaps: no `lookup: default`, no `$ref` splicing (so
   `ref-header.yaml` reads from the wrong offsets), no `repeat: byte_length`, no
   nested `object` members, and it drops the `log` transform chain that `dl-blg` and
   `qingping` now use.
   The 8 key-set differences are `name_from`, `repeat` items, some TLV channels, and
   nested `object` members.
4e. **`score-baseline.json` is stale, and one real regression hides behind that.**
   Scoring the tree against it reports 26 regressions. 23 are decentlab schemas at
   REJECTED 16.5% -> 14.1%, and they are **not** from this session: scoring the same
   schema in a clean worktree at HEAD gives 14.1% too, so the baseline (2026-08-07
   10:37) predates an edge-case scoring fix in the unpushed commits. Regenerate the
   baseline before trusting it as a regression gate.
   The 3 real ones are mine: `ct303`/`ct305`/`ct310` went SILVER 98% -> BRONZE 66%
   when they gained their current channels. Diagnosed, not guessed:
   - **JS gate, 15 points**: the generated codec reports `missing current_chn1_alarm`,
     because the generator does not emit nested `object` members. Fixing the generator
     is the real repair.
   - **Semantic dilution**: the addition brought 27 new fields the scorer counts as
     sensors. I annotated the six genuine current readings (IPSO 3317, which is
     Current) and **deliberately left `_max`, `_min`, `_total` and the alarm bits
     unannotated** - they are not sensor readings, and inventing mappings to lift a
     score is what PS-264 exists to prevent. That recovered 3 points.
   Python passes 16/16, branch coverage is 100%, and crossvalidate still reports
   `agrees` against the vendor decoder. So the schemas are more correct than before
   and score lower, which is worth remembering: the score measures tooling agreement
   as much as schema quality.
5. **Push.** Nothing in this session is committed.
6. `la-payload-schema`'s `python3 -m pytest tools/tests` writes to the real
   `CHANGELOG.md` (empty sections plus a bogus `[2.5.0]`). Two slide-assembly tests
   also fail there, both pre-existing.

Carried forward from earlier sessions, all four verified still open on 2026-08-10:

7. **Wire the preprocessor into validate and score.** A library-composed schema
   cannot be scored; `validate_schema` rejects an unflattened `$ref` schema
   (correctly, and loudly). `schema_preprocessor.py` is referenced by neither
   `validate_schema.py` nor `score_schema.py`, and is wired into `fuzz.yml` alone.
8. **Provenance for the three schemas capped at Silver** — `digital-matter/oyster`,
   `dragino/laq4`, `mclimate/vicki` still declare no `source:` on any vector, which
   is what caps them under PS-264. Cross-check against vendor docs or decoders and
   declare the real one. Do not guess: that is the point of the requirement.
9. ~~`name_from` unlocks 27 milesight channels.~~ **Premise was wrong; partly done.**
   `name_from` is used by 0 of 84 milesight schemas, and it is not what these channels
   need. Checked against the vendor decoders rather than the note: the missing keys
   are `current_chn1..3` in ct303/ct305/ct310 and `region_1..10` in vs321/vs373, and
   in both the key is **fixed per channel id**, not derived from a decoded value -
   ct303 builds it from `current_chns.indexOf(channel_id)`, a constant per channel,
   and vs321's regions are fixed bit positions in a mask. Because our schemas use
   per-(channel, type) TLV cases, each case names its fields outright. `name_from` is
   for a key that genuinely varies with a *value*, which none of these is.
   - Done: ct303, ct305 and ct310 gained their three current channels each (total,
     value, and the max/min/value/alarm-bits channel), cross-validated against the
     vendor decoder - all three now report `agrees`. Milesight 47 -> 50 of 84.
   - **Go declared `TypeObjectLower = "object"` and never used it in either type
     switch**, so a schema writing the documented lowercase `object` was rejected
     with "unknown field type: object". Same shape as the old `hex` alias bug. Fixed
     at both switch sites.
   - Still open: vs321 and vs373. Their region channel is a loop over a 16-bit mask
     emitting 20 fixed keys, which needs no new feature either - but note the
     bitfield range reads its base value big-endian regardless of the schema's
     `endian`, so a little-endian mask has to be taken as two u8s rather than one
     u16. Those two schemas also decode nothing for their declared example today,
     so more than the regions is missing.
10. **Milesight leftovers**: 11 of 84 schemas still have fewer than 5 test vectors,
    so they stay Rejected on vector count alone. Roughly 26 channels also need
    per-device modelling (conditional members, loops over bit maps).

## Session: Aug 7, 2026 (later)

### Completed

**Decentlab: vendor agreement 21 -> 55 of 58**
- `convert_decentlab.py` translated a vendor `convert` with a chain of regexes and
  parked anything unmatched in a `# formula:` comment, emitting a bare u16. That is
  a deliberate handoff, not a bug - the script gets close and the remaining syntax
  is ours to write - but 80 fields across 36 schemas were still sitting on it.
- Added a general affine fitter: evaluate the vendor expression at two points, fit
  `a*x + b`, confirm at a third so a polynomial or logarithm is rejected rather than
  silently linearised. Exact to 8.3e-16 against a 1e-3 tolerance. 52 fields.
- Wrote the real syntax by hand for the rest: `compute` for two-word values,
  `polynomial` for cubics, chained `ref` for derived values. 13 + 12 more fields.
- Two shapes were *wrongly* matched rather than unmatched, so no hint warned anyone:
  52 offset-binary fields declared `s16` (for word 0x8a77 that is -300.89 where the
  device means 26.79), and `x[i] + x[j]*65536` emitted as a big-endian `u32`.
- Three schemas remain on hints: `dl-blg` (needs a natural log), `dl-iam` (`max()`
  of two linear combinations), `dl-zn2` (difference of two two-word values).

**All four YAML implementations reached the full device corpus, then it grew**
- Java 1052 -> full, C# 1082 -> full, Go 1101 -> full, via computed fields, bit
  ranges, `byte_group`, `s24`, inline `match`, hex case/lookup keys, `ne` guards,
  the `round` transform and simple-tag TLV.
- Two runner defects mimicked interpreter gaps: `fPort` vs `fport` (oyster decoded
  with no port at all), and `byte_group` assembling bytes little-endian in Go and C#.

**Bitfields crossing a byte boundary were silently wrong in three languages**
- `u24[0:11]` returned 11 where the device means 1320. Python, Java and C# read
  `buf[pos]` alone and discarded the declared base width; only Go used it.
- `rakwireless/qingping` depended on it, *and* declared its temperature as
  `u24[4:23]` where the vendor reads 12 bits at `[12:23]`, *and* had no consumer for
  either raw field - so it reported no temperature or humidity at all. Fixed, with a
  vector from the vendor decoder. Note that decoder is broken as published
  (`Decoder(bytes, port)` then reads `data[...]`); TTN's own live page returns
  `{"error": "data is not defined"}`, so nobody has ever run it.

**Corpus coverage sweep: 1116 -> 1166 vectors**
- Asked which constructs no vector exercises. Answer: repeat, name_from,
  bitfield_string, formula, until, byte_length, merge, unknown, default, skip,
  header, enum. Fixtures for each now live in
  `schemas/devices/_language-conformance/`.
- That found two spec violations: PS-067 (Java had no `enum` type at all, reporting
  the raw integer) and PS-068 (`default:` on an enum ignored by Python, Go *and* C#).
- `tools/compose_library_vectors.py` makes `schemas/library/` runnable. Those files
  are definition catalogues, not schemas, so nothing could decode them and their
  vectors were verified by nothing. 42 composed in; 7 quarantined into
  KNOWN-ISSUES.md because they do not decode and had never been executed.

**CR-2026-005 submitted** (in la-payload-schema): `u32le16`/`s32le16` for a 32-bit
value carried as two 16-bit units, low unit first. Not an endianness variant - the
payload is a word stream with no 32-bit field on the wire - but the bytes do land
in a recognisable CDAB order, and 30 occurrences across 16 vendor decoders make it
worth a type. 17 values currently spend 14 lines of `compute` each.

### Next

1. **Settle the bitfield form.** The specification lists five spellings and the
   direction under discussion is to keep only the bracket form `u8[3:4]`. If that is
   settled, the work is to drop the other four from the spec and the Python
   interpreter, and rewrite `lorawan/lorawan_frames.yaml`, the only file using the
   sequential form. That rewrite is mechanical and verified equivalent, and it
   returns Go, Java and C# to the full 1166 without touching an interpreter. **Do
   not close that gap by implementing `u8:N` three more times.**
2. **Work through `_library-composed/KNOWN-ISSUES.md`** - 7 vectors. Two are
   representation mismatches (a vector says `[1,2,...]` where the field decodes to
   bytes, so probably a vector-format question rather than a decode bug), one wants
   a `type: array` that does not exist, and four disagree numerically.
   `lorawan_mac_commands__device_time_ans` matches neither endianness, so its
   expected value is simply wrong.
3. **Decide `header:`.** Undocumented, unused by any schema, honoured by Java and C#
   and ignored by Python and Go. The library's own `common/headers.yaml` implements
   headers as `definitions:`, and `definitions` + `$ref` covers the use case more
   generally, so the likely answer is to delete it - or define it as sugar.
4. **The three remaining Decentlab hints.** Only `dl-blg` needs a language feature:
   `log` exists as a transform stage in Python but not in Go, Java or C#. Adding
   `pow`/`log`/`sqrt` to those three would also let squares stop going through
   `compute`.
5. **Wire the preprocessor into validate and score.** A library-composed schema
   cannot be scored today; `validate_schema` rejects an unflattened `$ref` schema
   (correctly, and loudly - the one tool that behaved well all session).
6. **Push.** 16 commits in payload-codec-proto, 1 in la-payload-schema, and the
   three td-tools branches are on the fork with no PRs opened.

### Notes for whoever picks this up

- **The requirements extractor misses 21 of the 270 PS ids** in the spec - a second
  id on a line, a line ending in a colon, or no modal verb - so the traceability
  appendix under-reports by 7.8% and any audit built on it inherits the blind spot.
- **Corpus runners walk `schemas/devices/` only.** A fixture anywhere else is read
  by nothing and looks like coverage while proving nothing. That is why both
  `_language-conformance/` and `_library-composed/` sit inside the device tree.
- **Cross-file `$ref` is a pre-step, by design.** The interpreters resolve only
  local `#/definitions/...`; `schema_preprocessor.py` inlines the rest so they stay
  free of a loader. It is wired into `fuzz.yml` and nothing else.
- When inlining a definition, **splice its `fields:` rather than nesting them** - a
  nested container without `type: object` is never descended into and every field
  reports as missing.
- Vendor data is not automatically the better authority: the qingping datasheet
  contradicts itself on every sensor range, and its published decoder cannot run.

## Session: Aug 7, 2026

### Completed

**All four YAML implementations now decode the whole corpus**
- Python 1116, Go 1101 -> 1116, C# 1082 -> 1116, Java 1052 -> 1116. Every floor is
  now the full 1116, so any failure anywhere is a regression, not a known gap.
- Java gained the most: computed fields (`ref`, `polynomial`, `compute`, `guard`) were
  not parsed at all, `u8[lo:hi]` fell through to U8 and read the whole byte, `consume`
  was unmodelled, `byte_group` was absent, `s24` resolved to U8 and misaligned
  everything after it, and there was no inline `- match:` block or map-shaped cases.
- Go: TLV cases were parsed only when the block had `tag_fields`/`tag_key`, so a plain
  `tag_size` block (elsys/ers) had no cases and decoded nothing. `ne` in a guard was
  neither parsed nor evaluated. `{op: round}` was dropped.
- C#: match case keys and lookup keys went through `int.TryParse`, which does not read
  `0x01`, so hex-written cases and lookups never matched - rbs30x reported nothing
  past its header. Same `ne` and `round` defects as Go.

**Two bugs worth remembering because they mimic interpreter gaps**
- The Java and Go *runners* read a vector's port as `fPort` only, while oyster writes
  `fport`. Its seven vectors decoded with no port at all, so every field reported
  missing and it read as an interpreter gap. Python and C# already read both.
- A guard's fallback must be reported exactly as declared. Java briefly applied the
  field's modifiers to it, which turned vicki's `else: 0` into `(0 - 28.33333) /
  5.66666` and put sensorTemperature 5 degrees low.

**Modifier/transform ordering unified**
- All four now apply bare modifiers *then* transform stages, both when both are
  present. Go, C# and Java each had an either/or chain that silently dropped the
  modifier on a field that scales and then rounds. Rounding is half-to-even
  everywhere, matching the interpreter; half-up disagrees on exact halves the vectors
  contain.

**CR-2026-002 and CR-2026-003 accepted**
- Both moved to `change-requests/implemented/` in la-payload-schema; their spec text
  was already applied in 24c819e. `CR-tracking.md` rewritten - its summary counts,
  tables and statistics had all drifted apart.
- PS-264 is now enforced by default in `score_schema.py`: the gate existed behind
  `--require-provenance`, which was right while the CR was a proposal and wrong once
  it was normative. Flag inverted to `--no-require-provenance`.
- Three schemas capped at Silver as a result - `digital-matter/oyster`,
  `dragino/laq4`, `mclimate/vicki` - all because no vector declares a `source`. No
  provenance was invented for them; that is the point of the requirement.
- `payload-schema.json` gained `source` with the five PS-263 values.

**Everything pushed**
- `payload-codec-proto` master and `la-payload-schema` main are pushed; the three
  td-tools branches are on the MultiTech fork with conventional-commit messages and
  ECA sign-off, no PRs opened yet.

### Next

1. **Decentlab is the largest correctness debt**: 37 of 58 still disagree with the
   vendor decoder, untouched since the offset-binary fix. Re-clone the oracle first.
2. **Provenance for the three capped schemas** - cross-check oyster, laq4 and vicki
   against vendor docs or decoders and declare the real `source`. Do not guess one.
3. **`name_from` unlocks 27 milesight channels** blocked on computed keys.
4. **Milesight leftovers**: 26 channels need per-device modelling; 11 schemas are
   still Rejected for want of 5 verified vectors.
5. **Open the three td-tools PRs**, catalog fix first - without it the conformance
   suite runs on 6 conversions instead of 163 and still passes.

### Notes for whoever picks this up

- The repo `.venv` was dead (built June 3 for Python 3.12 at an old path, no pip), so
  `make test-languages` failed at the pytest step. Rebuilt from requirements.txt.
  `hypothesis` was missing from the user environment too; test_hypothesis.py could not
  even be collected.
- The C# test `YamlKeyOrderMatters` was renamed to `ModifiersApplyInCanonicalOrder`.
  Its assertion held either way, but the name asserted the contract CR-2026-002
  removed.
- Everything in the Aug 6 "Next" list is now done except the schema-coverage items,
  which are items 1-4 above.

## Session: Aug 6, 2026

### Completed

**Cross-language conformance suite (the big one)**
- The corpus is now the shared test set: 1,116 vectors across 98 schemas, run by a
  runner in every implementation reading the same YAML.
  - `tests/test_corpus_conformance.py` 1116/1116
  - `go/schema/corpus_conformance_test.go` 1101
  - `dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs` 1082
  - `bindings/java/.../CorpusConformanceTest.java` 1052
  - C has no runner: it consumes a binary schema, not YAML.
- Non-Python runners assert against a committed floor, so known gaps stay visible and
  a regression fails. **Raise the floor whenever a gap closes.**
- Before this existed, Go and C# each read two hardcoded schemas and Java none, which
  is how Go and Java came to return an empty result for *every* TLV schema in the
  repository without a test failing.

**Interpreter defects found by running the corpus**
- Go and Java: inline `- tlv:` blocks never got their cases (type assigned after
  parse), so no channel/type schema decoded. Fixed in both.
- Go and C#: composite case keys compared as strings against a tag rendered without a
  space, `[1,117]` vs the `"[1, 117]"` schemas are written with. Fixed in both.
- Go and Java: the sequence form `lookup: ["off", "on"]` was unparsed - hundreds of
  corpus fields reported raw integers. Fixed in both.
- Go: `DecodeContext.Read` bounds-checked only the upper end, so `length: -1` panicked
  and took the caller down. Now treats negative as the remainder.
- Go: guards were evaluated *after* compute, so a guarded division by zero aborted the
  whole payload (dl-alb, vicki). Conditions are checked first now.
- Go: no `hex` type (constant is `"Hex"`, no lowercase alias) and no `u8[lo:hi]` bit
  ranges - both added.
- C#: `ParseFieldType` strips the range so `u8[0:0]` read the whole byte. Routed to
  the Bits path, which now honours `consume`.
- Python: `hex` emitted uppercase, violating PS-074; `hex:upper` was unreachable
  because any type containing a colon parsed as a bit range.
- All four disagreed four ways on an unmatched `lookup`: raw value (Python, Go, Java)
  or the string `"unknown(42)"` (C). All now omit the field per PS-269.

**CR-2026-004 - implemented and applied**
- `name_from` computed keys, sparse mapping `lookup` with `default`, negated/wildcard
  TLV case keys (`"[1, !0]"`, `"[2, *]"`). PS-265..PS-270.
- Spec text applied to Clauses 2, 3 and 4; CR moved to `change-requests/implemented/`.
- Implemented in Python, Go, Java, C#. **Not C** - no TLV, fixed-size names, binary
  schema has no slot for a template or a default.

**Milesight family**
- 84 Rejected -> 18 Platinum, 33 Gold, 22 Silver, 11 Rejected (mean 14% -> 82.3%).
- Vendor agreement 0 -> 47 of 84, measured against the vendor's own JS decoder.
- Fixed: 13 conversions recorded in comments but never applied (humidity /2 etc.),
  version and serial channels (were `u8`, vendor reads 1-8 bytes), packed flag bytes,
  enum labels from vendor ternaries and status maps, units from TTN's payload schema.

### Next

1. **Java is the biggest conformance gap** (64 failures): computed `ref` fields are not
   evaluated and `u8[lo:hi]` bit ranges are unsupported. The same two fixes just made
   in Go and C# should close most of it. Then raise its floor.
2. **C#**: `rbs30x`'s `match` cases decode nothing; `vicki.sensorTemperature` differs
   by more than rounding (46.95 vs 14.76) - a real computed-field bug, not a tolerance
   issue. Both need the `round` transform op too (Go also lacks it).
3. **Go**: `byte_group` (oyster, ers) and `round`.
4. **`name_from` unlocks 27 milesight channels** that were blocked on computed keys -
   they can be added now that the construct exists in four implementations.
5. **Milesight leftovers**: 26 channels need per-device modelling (conditional members,
   loops over bit maps); 11 schemas are still Rejected for want of 5 verified vectors.
6. **Decentlab**: 37 of 58 still disagree with the vendor decoder - untouched since the
   offset-binary fix.
7. **CR-2026-002 and CR-2026-003** are in `change-requests/submitted/` awaiting WG
   review. 004 is implemented.
   *(Aug 7: both accepted and implemented - see the session above.)*

### Notes for whoever picks this up

- Re-clone the Decentlab oracle: `git clone --depth 1
  https://github.com/decentlab/decentlab-decoders` - the copy used today was in a
  session scratchpad and is not persistent. The TTN checkout at
  `~/Workspace/lora/tools/lorawan-devices` is permanent.
- Three TTN declared examples are stale and disagree with their own vendor decoder:
  `ws50x`, `uc1114`, `uc1152` all declare `true` where the decoder yields `"on"`.
  Prefer the decoder; `crossvalidate_ttn.py` reports the two oracles separately.
- `uc1114`'s vendor decoder reads past the end of the payload (its loop has no
  `else break`), so it reports a field for a channel type its own condition excludes.
  We deliberately do not reproduce that, so cross-validation will keep flagging it.
- A field-block scan that jumps to the end of each block never sees fields nested
  inside an `object`. This bit twice today - when annotating history records and when
  marking label fields. Advance by one line instead.
- `schemas/library/gateway/telemetry-v1.yaml` is untracked and was left alone all
  session: it predates this work and is not mine to commit.

## Session: Feb 24, 2026

### Completed

**Output JSON Schema Generation**
- Added `tools/generate_output_schema.py` for generating JSON Schema describing decoder output
- Generates draft-07 compliant schemas with type constraints, ranges, descriptions
- Enables standard JSON Schema validation of codec output
- Updated README, SPEC-IMPLEMENTATION-STATUS.md, OUTPUT-FORMATS.md documentation

**MClimate Vicki Schema**
- Created `schemas/devices/manual/mclimate-vicki.yaml` for Vicki Smart Radiator Thermostat
- Added `mod` and `idiv` compute operators across Python, Go, and JS interpreters
- Fixed `byte_group` field referencing in interpreter and validator
- Fixed `ref` field modifier application (`mult`, `div`, `add`)
- Generated JS codec and output schema

**Schema Language Extensions**
- `compute` now supports: `add`, `sub`, `mul`, `div`, `mod`, `idiv`
- `transform` now supports: `{op: round, decimals: N}` syntax
- Updated JSON meta-schema to include new operators
- Updated documentation (SCHEMA-LANGUAGE-REFERENCE.md, .cursorrules)

**Schema Development Process**
- Created `tools/analyze_codec.js` for extracting test vectors from existing JS codecs
- Created `docs/SCHEMA-DEVELOPMENT-GUIDE.md` with best practices for codec conversion
- Added schema development section to `.cursorrules`
- Process: Analyze original → Generate test vectors → Write schema → Validate → Document deviations

**Interpreter/Generator Fixes**
- Added `round` transform to Python interpreter (`{op: round, decimals: N}` syntax)
- Added `round` transform to JS generator (generates `Math.round()` calls)
- Vicki schema now produces identical output to original TTN codec for keepalive messages

---

## Session: Feb 19, 2026

### Completed

**Testing & Coverage**
- Python: 448 tests, 81.0% coverage
- Go: 331 tests, 80.1% coverage
- Added edge case tests for formula injection, recursion limits, buffer boundaries
- Added compact format edge cases (Go-specific)
- Added binary schema and base64 edge cases

**Fuzzing Infrastructure**
- Verified Python random fuzzing (`fuzz_decoder.py`)
- Verified Python Hypothesis property-based tests
- Verified Go native fuzzing (`fuzz/go/decoder_test.go`)
- Coverage analysis at intervals (core paths saturate quickly)

**Benchmarks**
- Ran on Ryzen 9 7950X3D (local) and Intel i5-2400 (skidoosh)
- Documented in `SPEC-IMPLEMENTATION-STATUS.md`
- Go Binary Schema: ~600K ops/sec (Ryzen), ~280K ops/sec (i5)
- Python Interpreter: ~21K ops/sec (Ryzen), ~8K ops/sec (i5)
- Cloud estimates added (t3.micro to c7g.large)

**Documentation**
- Updated `AUDIT-REPORT.md` with current test counts
- Updated `SPEC-IMPLEMENTATION-STATUS.md` with benchmarks
- Created `FUTURE-FEATURES.md` roadmap

**Semantic Fields (Implemented)**
- `valid_range: [min, max]` - bounds checking with `_quality` flags
- `resolution` - minimum detectable change metadata
- `unece` - standard unit codes (UNECE Recommendation 20)
- Schema validation in `validate_schema.py`
- Python interpreter support with quality output
- Go interpreter support with quality output
- Test coverage in both Python and Go

---

## Future TODO

See [FUTURE-FEATURES.md](docs/FUTURE-FEATURES.md) for detailed roadmap.

**Planned:**
- Embedded codegen: bounds constants from `valid_range`, scale constants from `resolution`
- Output format extensions: SenML vmin/vmax, IPSO 5603/5604
- Batch generation of output schemas for all device schemas

**Per-Device Deliverables (Required):**
1. YAML Schema (`device.yaml`) - Source payload definition
2. JS Codec (`device-codec.js`) - TS013-compliant decoder
3. Output Schema (`device-output.schema.json`) - JSON Schema for decoded payload

**Out of Scope (device profile, not schema):**
- `accuracy`, `instrument_range` - static sensor characteristics belong in device registries

### References

- [FUTURE-FEATURES.md](docs/FUTURE-FEATURES.md)
- UNECE Recommendation 20
