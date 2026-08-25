# Session Notes

## RESUME HERE - state at the end of 2026-08-25

**Everything is merged to `master` and pushed.** Nineteen CRs landed as PRs #12-#29;
`master` is the only remote branch and the working tree is clean. `master` is
branch-protected, so every change goes through `gh pr create --base master` - a direct
push is refused with `GH013`.

Green as of the last run:

| | |
|---|---|
| Python | **2639** passed / 4 skipped |
| Go | `go vet` + `go test -count=1` clean; decode 1239/1239, encode 1173 ordered / 1164 plain |
| Java | BUILD SUCCESS, 46 tests; decode 1239 of 1250 |
| C# | 92/92; decode 1239 of 1250, encode 1164 |
| `vector-verdicts.py` | **1250 vectors, interpreted 100%, generated 100%, 0 disagreements** |
| `encode-round-trip.py` | 1163 of 1239, **0 unexplained** |
| validate-devices / validate-examples / selftest / score-check / docs-index-check | pass |

Corpus 1229 -> 1250. Decode floors 1193 -> 1239. Encode round-trip: reference 1131 ->
1163, Go 1144 -> 1173, Java 1143 -> 1163, C# 1144 -> 1164.

**There is no obvious next CR, and that is the honest state rather than a stopping point.**
Every construct is described in the meta-schema and validated; all five implementations
agree on all 1250 vectors; the encode residue is 76 vectors, every one classified as
information the decode does not carry. What remains in AGENTS.md is scoped decisions, not
defect hunts:

- `definitions.field` stays permissive - accepts `s17`, a nameless field, `mult: "0.1"`.
  Closing it is real rejection risk across 189 schemas for a payoff `validate_schema.py`
  already delivers.
- The C interpreter has no TLV and no `name_from`, and `bindings/c/schema_ffi.c`'s
  `schema_create_yaml()` is still a stub whose `result_to_json()` destroys precision with
  `%g`. Unchanged today.
- 11 corpus vectors are genuinely unreversible encodes (enum `default`, lookup `default`,
  `sqrt`), tolerated by the Java and C# decode floors.
- Go's plain `Encode` is 2 behind the reference on `ws515`/`wt101`, whose devices lay
  channels out non-ascending. That is the documented limitation of that API;
  `EncodeOrdered` handles them.

**Read the "How the measurements went wrong" section below before trusting any figure in
this file.** Nine of the day's mistakes were in measurement rather than in fixes, and one
produced a plausible *success* story that was repeated across four PRs before anyone
checked it.

## Session: Aug 25, 2026

Nineteen CRs, PRs #12-#29. Every one merged to `master` individually.

### What was fixed

**CR-2026-013/014 - unknown TLV tags (#12).** An unknown tag was handled in silence: with
a length field the entry was skipped and nothing recorded, without one the decoder `break`'d
and abandoned the rest of the payload. The caller got a result indistinguishable from a
device that sent fewer fields. All five implementations now report it; Go, Java and C#
gained a `_warnings` key alongside `_quality`, and `unknown: raw` was implemented in three
of them for the first time. `expected_warnings` on a test vector makes it assertable -
absent asserts nothing, `[]` asserts none were reported, and each entry is matched as
substrings because the specification fixes what a warning must contain and not its wording.

**That found six vendor vectors losing bytes in silence.** AM307/AM307L/AM308/AM308L drop 4
of 14 bytes on tag `0x05, 0x6A`; WS50x drops 5 of 8, twice; EM310-tilt 2 of 11. Payloads and
expected fields unchanged - what they lost is now written down.

**CR-2026-015 to -019 - the meta-schema (#13-#17).** `definitions.field` took
`additionalProperties` and described no construct. `tlv`, `flagged`, `match`, `byte_group`
and `repeat` are all described now and closed with `additionalProperties: false`, and
`_warnings`/`unknown_tags` are declared rather than merely tolerated. Reading the
implementations to write those descriptions is what produced everything below - the
descriptions themselves were the least valuable part.

Three things the reading turned up:

- **`tools/generate_jsonschema.py` had not produced `schemas/payload-schema.json` for a
  long time.** Nothing in the Makefile or CI ran it, so nobody noticed. One `--output` run
  would have deleted eleven keys, `expected_warnings` among them. It refuses now and names
  what would be lost; `--force` still overwrites. **Edit that file by hand.**
- **`match` had no validation at all** - a block with no discriminator was reported valid
  and decoded as nothing.
- **`repeat` and `byte_group` had none either**, including PS-017 (`consume` forbidden on a
  `byte_group` member), which had been prose-only and never enforced.

**CR-2026-020 - `match` parity (#18).** Go dropped `length`, `name`, `var` and `default` in
its parser while `decodeMatch` already honoured `length` - and since it defaults to 1, a
two-byte discriminator was read as one byte and every field after the construct came from
the wrong offset. It also built its case list by ranging over a Go map, so where two keys
could match the same value **the winner varied between runs of the same binary**. The TS013
generator emitted `vars.kind === 2..5` for a range key, which is not valid JavaScript - the
whole codec failed to parse, so such a schema had *no* generated path rather than a wrong
one.

**CR-2026-021 - `repeat` limits, and the tooling defect (#19).** `max` was no ceiling in the
generated codec and `min` was unchecked. But the reason that was invisible is the bigger
half: `tools/vector-verdicts.py` compared values with `if not values_match(...)`, and
`values_match` returns `(ok, detail)` - a two-element tuple is always truthy, so **the
condition could never hold**. From the day the second return value was added the tool
compared *key presence only*. Its `0 vectors where the two paths disagree` line meant only
that both paths produced the same keys.

**CR-2026-022 - `byte_length` spans (#20).** PS-088 requires the members to divide the span.
Python, Go and Java enforced it; **C# and the TS013 generator had no post-loop check at
all**, so both ways of failing were accepted and the read position ended somewhere other
than the span's end. A 2-byte member over a 5-byte span produced a third record holding the
*following field's* byte, and the following field read past the payload as zero.

**CR-2026-023 to -031 - the encode side (#21-#29).** Bare runs of bit ranges were written a
byte apiece with unshifted values (`40` encoding as `020000`); Go had no `u32le16` encode
case and truncated its integer conversions; three encoders never read a match's `default:`
key; `name_from` values were looked up under the declared name rather than the templated
key. All fixed in all four encoders, each with a fixture.

`tools/encode-round-trip.py` is new and is the durable artefact: it classifies every vector
that does not re-encode, and `test_cr_2026_023_encode_round_trip.py` asserts none is
`unexplained`. The count alone was 77 and read as an encoder full of holes; all but one
entry was information the decode does not carry. **Do not re-add a hand-maintained residue
table** - the one in `test_encode_round_trip.py`'s docstring said "1129 of 1191" and every
row was wrong by the time the corpus reached 1237.

### How the measurements went wrong

Five kinds, eleven instances, all in test scaffolding rather than in a fix. Worth reading
because the failure mode is consistent and the notes above are only as good as the figures
behind them.

1. **A tuple read as a boolean** (#19). The conformance cross-check had never compared a
   value. Announced itself only because a vector I had just predicted would fail, passed.
2. **Loose source anchors**, four times - #22 (a method name matching its call site), #24
   (`func encodeField` matching `encodeFields`), and #26 twice (`index("ncode")` matching an
   unrelated word, then a `case` line spelled identically in decode and encode). Every one failed
   on correct code, and one was **hiding a real omission behind the false failure** - Go's
   flagged path genuinely was not wired. Anchor on something that occurs once, and say where
   you searched from.
3. **Two bucketings subtracted as comparable** (#23). Per-shape round-trip counts are
   ratchets for one harness over time; each harness buckets schemas its own way and their
   denominators differ. Compare vector *sets* - `encode-round-trip.py --list` exists for it.
4. **CR tests pinning running floors**, four times (#22, #24, #25 pinned the total; #26
   bounded the total and pinned the per-shape bucket instead, which #28 then broke). Both
   are running values. I documented the rule after the first occurrence and reproduced it
   twice more, once in the very commit that was fixing the other two. Bound both, never pin
   either.
5. **Two different APIs compared as one** (#27), and this is the one that misled. Go has
   `Encode` and `EncodeOrdered` with different contracts; the corpus test uses the ordered
   pair and my probe used the plain one. So "Go is 28 behind the reference", then 14, then
   13, across four PRs - all measured on a path those PRs were not about. **On the API its
   own test measures, Go fails a strict subset of what the reference fails.** The fixes in
   those CRs were real and their floors moved; the comparison figures were not.

The pattern: a wrong measurement that *fails* is self-correcting, and a wrong measurement
that agrees with what you expected is not. Writing rules into AGENTS.md demonstrably did not
stop me repeating them inside the same session - re-measuring differently did. The
structural defences are what hold: separate ratchets per contract
(`TestCorpusEncodePlainRoundTrip`), bounds instead of equalities, and `--list` so a
cross-implementation comparison cannot be made loosely.

### Conventions established

- **`master` is branch-protected.** `gh pr create --base master`; a direct push is refused.
- **`schemas/payload-schema.json` is hand-maintained.** Its generator refuses to clobber it.
- **A CR-specific test bounds every floor it names.** Never an equality.
- **Two `name_from` resolvers per implementation, deliberately** - decode against variables,
  encode against the data it was handed.
- **An unlisted type in Go's encode switch is a reported failure**, not a silent zero-byte
  write. That is what made the `u32le16` gap findable. Keep it.


## Session: Aug 11, 2026 (later)

### `length: remaining` - specified everywhere, implemented nowhere

Chasing the last cross-check difference (rbs30x's `stored_downlink`) turned up a keyword
that the specification defines and no implementation had:

- **PS-013/PS-014/PS-015** define `length: remaining` - consume to the end of the
  payload, empty rather than an error when already at the end, at most one per nesting
  level. Every match for "remaining" in all five implementations, the generator and the
  validator was incidental. **No schema used it**, because it did not work.
- The one schema that needed it wrote **`length: -1`**. In Python that sliced
  `buf[pos:pos + -1]` - an empty value *and* a read cursor rewound by one byte. Go's
  `Read` already resolved a negative count to the remainder (added when the corpus
  runners went in), so **Go and Python already disagreed on that field**, silently: no
  vector asserted `stored_downlink`.
- The generator's loop condition was `_si < -1`, so it never ran either.

Fixed, with `remaining` as the only spelling and a negative integer as the internal
sentinel the parsers map it to - so no field struct needs a string:

- **Python** `resolve_length()`, used at all six variable-length decode sites. On encode
  `remaining` has no count to pad, so a `skip` emits nothing.
- **Go** parser maps the keyword to `-1`; `Read` already honoured it.
- **Java** parser maps it (`toInt` would have silently returned its `0` default);
  `getEffectiveLength` passes the sentinel through instead of falling back to the type
  default; `DecodeContext.read` resolves it, guarded because `new byte[-1]` throws
  `NegativeArraySizeException`.
- **C#** the same three, guarded because `AsSpan` throws on a negative count.
- **Validator** PS-014/PS-015 in the *structural* pass. It first went into
  `check_best_practices`, which was wrong: errors added there are recorded in
  `schema_errors` but **do not flip `schema_valid`**, so a MUST cannot be enforced from
  it. The check walks every nesting level via a new `iter_field_lists()`, because
  `check_fields` descends into `fields`, `byte_group` and `flagged.groups` but *not* TLV
  `cases` - exactly where the offending field lived.

### The `bytes` type was reporting an array

Found while fixing the above, and worse than the original bug: the generator emitted
`bytes` as a **JavaScript array of octet values** where PS-281 and all five interpreters
report a **lowercase hex string**. It never showed up because
`tools/crossvalidate_js_json.py` skipped dicts and lists entirely, so every `bytes` field
in the corpus went uncompared. The harness now compares nested values, which is what
turned up the `_quality` gap too.

**Lesson for any cross-check harness: what it declines to compare, it silently blesses.**

### Also

- Corpus floors 1189 -> **1190** in Go, Java and C#. The floor is the ratchet: at 1189
  Java or C# could have failed the new vector and still passed. Tightening it first is
  what proved their `remaining` support actually works. Their stale comments still
  described the negative-operand `idiv`/`mod` gap that CR-2026-007 closed; removed.
- `rbs30x.yaml` asserts `stored_downlink` in both directions - `"010009"` and, at the end
  of the payload, `""` (PS-014). It asserted neither before, which is why two
  implementations could disagree about it indefinitely.
- New `tests/test_remaining_length.py`, 14 tests: evaluation, the empty case per type,
  the no-rewind property, the three validator rules, and the schema that needed it.

### `valid_range` -> `_quality` in the generated codec

The generator implemented no range checking, so every schema with `valid_range` produced
a codec that dropped the `_quality` object the interpreters report - 12 vectors, in
`vicki.yaml` and `qingping.yaml`.

Implemented as a single pass at the end of the decode function rather than a check inside
each of the six emitters. Two reasons: the emitters already carry a "remember to also
write `vars`" trap and this would have added a second one, and a post-pass keyed on the
reported name is guarded by `hasOwnProperty`, so a field in an untaken TLV or conditional
branch does not acquire a flag.

What it deliberately does not do: `_valid_range_fields()` mirrors the interpreters' three
call sites - the main field loop (plain and computed fields) and `flagged` group members -
and does **not** descend into `repeat`, `object`, TLV `cases` or `match` cases, because
the interpreters do not check there either. A field inside a TLV case gets no flag even
when it declares a range. Emitting one would have invented a divergence in the opposite
direction. All 196 `valid_range` declarations in the corpus sit in top-level `fields` or
in `definitions` (spliced by `$ref`), so nothing is missed today; the point is which
behaviour is being copied.

The decode functions now carry a warnings array and return it, and the decode entry
points surface it instead of a hardcoded `[]`. An out-of-range value produces the same
warning text as the interpreter.

Two of the tests I wrote for this were wrong first time and passed for the wrong reason:
`compute` takes `a:`/`b:`, not `operands:`, so the "computed field" case computed 0 and
compared 0 against its range; and a range of `[0, 10]` on 200/10 is out of range, not in
it. Both now assert the direction they claim. `tests/test_valid_range_quality.py`, 15
tests, each comparing the codec against the interpreter rather than against my
expectation of it.

`generate_output_schema.py` now declares `_quality` too, so the output schema describes
the field rather than merely tolerating it under `additionalProperties: true`. PS-182
closes the key set - only fields with `valid_range` appear - so the declaration lists
them and sets `additionalProperties: false`, except when a `valid_range` field also has
`name_from`, where the output key is decided at run time and the values are constrained
instead.

The collector walks the **whole schema**, not the field lists the properties come from,
and deliberately over-collects. A declared-but-absent property costs nothing in JSON
Schema; a *missing* one combined with `additionalProperties: false` would reject valid
decoder output. That matters concretely: 159 of the corpus's 196 `valid_range`
declarations live in `definitions`, and `process_fields` does not resolve `$ref` at all,
so a walk of the field lists alone would have under-declared most of them.

40 schemas now declare `_quality`; the only two files containing `valid_range` without it
are `SEMANTIC-MAPPING.md` and the meta-schema `payload-schema.json`, neither a device
schema. `tests/test_output_schema_quality.py` replays every corpus vector that emits
`_quality` against its own declaration, so the closed key set cannot silently start
rejecting real output.

### `$ref` in the output schema

`process_fields` never resolved `$ref`, so every field behind a reference was absent from
the generated output schema. Now spliced the same way the interpreters and the TS013
generator do it: local `#/definitions/...` only, the target's `fields:` spliced into the
list rather than nested, recursive with a depth cap for a definition that refers to
itself. The `definitions` had to be threaded through every recursive `process_fields`
call, not just the top-level one, or a reference inside a flagged group or a TLV case
would still have been missed.

Measured effect: exactly one schema changes, `ref-header.yaml`, from 1 declared property
to 3 - and it now declares exactly what its decoder reports. I had claimed
`basic_station.yaml` and `udp_packet_forwarder.yaml` produced empty output schemas
because of this; **that was wrong**. They have no `fields` at all - they are protocol
descriptions built from `message_types`/`packet_types` - so zero properties is correct
for them and unrelated to `$ref`.

A corpus check now asserts every key a decoder reports is a declared property: 173
schemas pass, and the three that do not are named in the test with their reasons.

### `match` in the output schema

`process_fields` handled `switch` and `tlv` but not `match`, so every field inside a match
case was undeclared. Both syntaxes are covered now - the nested `match:` form with `cases`
keyed by value, and the legacy `type: match`/`on:` form with cases as a list of
`{case, fields}` - along with `default` when it carries fields, a literal `default` key
inside `cases`, and the `name:` key, which reports the discriminator itself (`var:` only
stores it, so it declares nothing). `default: error` and `default: skip` report nothing.

Effect: rbs30x 3 -> 50 declared properties, rn320bth 2 -> 12, laq4 3 -> 13, and no other
schema in the corpus changes. 16 of rbs30x's properties come out with union types like
`["string", "integer"]`, which is `merge_property`'s widening doing its job - the same
name is reported as a `lookup` label in one case and a raw integer in another, and a flat
output has one property for it.

The corpus guarantees now live in `tests/test_output_schema_corpus.py`: every reported key
is a declared property, and every reported value satisfies its declared type (2832
value/type pairs, zero mismatches).

One correction to my own measurement: I reported "175 fully declared" earlier. The honest
figure is **173 of 174**, because two schemas have no vector that decodes at all and my
first count credited them as complete. The test now excludes them - a schema that decodes
nothing proves nothing about whether its properties are declared.

### `name_from` in the output schema

The last construct whose output the schema could not account for. `name_from` builds the
reported key from a template filled in from values decoded earlier (PS-265/PS-266), so
the field's own name is never a key - and the old output schema declared exactly that:
`name-from.yaml` described `reading`, a key the decoder never emits, and did not describe
`channel_3_reading`, the key it does.

Two forms now, chosen by what the template references:

- **Every reference closed -> exact properties.** A `lookup` counts as closed, which I
  checked rather than assumed: PS-269 *drops* a field whose value is not in the mapping
  rather than reporting the raw number, so an unmapped value never reaches a key - it
  makes the `name_from` field fail to decode instead. `lookup: default:` just adds one
  more member. The cross-product is capped at 64; past that the pattern form is clearer
  than hundreds of properties for one field.
- **Otherwise -> an anchored `patternProperties` entry** carrying the field's value
  schema, so a consumer validates the value even where the key is dynamic.

Every reference pattern comes from measured substitution behaviour: a lookup reference
substitutes the **label** (`beta_reading`), a signed field can contribute a minus sign
(`at_-2_reading`), a scaled field renders as a decimal while an integral one loses its
trailing zero (`v_2.5_reading`, `v_2_reading`), and an `ascii` field contributes arbitrary
text. Where the type is unclear the fragment is deliberately loose - too narrow a pattern
would reject a key the decoder really produces, while too wide only describes the shape
loosely.

Patterns are scoped: one inside a nested `object` attaches to that object, not the root.

**The corpus is now fully described: all 174 schemas that decode a vector report zero
undeclared keys**, so `KNOWN_UNDECLARED` in the corpus test is empty.

One thing left deliberately imprecise, and correct as it stands: a `valid_range` field
with `name_from` still gets the permissive `_quality` form (values constrained, keys open)
rather than the enumerated keys.

### Typing `lookup` and `enum` in the output schema

**PS-106: "Lookup values MAY be numbers or strings."** The generator assumed otherwise. A
mapping was declared `["string", "integer"]` whatever it mapped to - too loose for the 23
string-valued mappings in the corpus, and outright wrong for a mapping to floats, which it
typed as an integer. A sequence was declared `{"type": "string", "enum": [...]}` even when
its entries were numbers.

The type now comes from the values, and both `lookup` forms are closed sets, so the values
are declared as an `enum`:

- A **mapping** omits the field where the value is not a key and no `default` is declared
  (PS-269), so a raw number is never reported. A `default:` label joins the set.
- A **sequence** is indexed from zero (PS-104) and an out-of-bounds index MUST be an error
  (PS-105).

`type: enum` splits on its `default`: with one, an unmapped value is reported as that
default (PS-068) and the set is closed; without one it is reported as the **string**
`"unknown(9)"`, so the set is open, no enum is declared, and `string` belongs in the type
even where every declared value is a number. I loosened all three corpus enums before
noticing they declare defaults, and put the enum back.

Effect: 7 schemas change, all in the intended direction - `["string", "integer"]` becomes
`"string"` plus the real label set. The corpus test grew a fourth guard that every reported
value is in its declared `enum`, which is what actually tests the closed-set claim against
real data rather than against my reading of the code.

### PS-105 implemented in all five

Every implementation now treats an out-of-bounds sequence index as a decode error rather
than silently reporting the raw index. The message is identical everywhere -
`lookup index 7 out of bounds for 2 entries` - and each language asserts it in its own
suite, which is what makes the parity real: `tests/test_ps105_sequence_lookup.py`,
`cr004_test.go`, `CR2026004Test.java`, `CR2026_004Tests.cs`, `src/selftest_schema.c`.

**The ambiguity I had to resolve.** PS-105 says only "MUST emit an error" - it does not say
whether the payload is abandoned. Read as a decode error, because PS-278 shows the
specification saying "report the field as absent and MUST NOT abort" where that is what it
means, and PS-269 already provides the omit-quietly behaviour for the failure that deserves
it. The two lookup failures are therefore deliberately different: a mapping gap is a known
unknown and omits; a sequence index out of bounds is a shape mismatch and errors.

**CR-2026-009**, now in `la-payload-schema/change-requests/implemented/` with its text
applied to clause 3, adds PS-285 (the field is not reported) and PS-286 (surfaced as a
decode error, explicitly not PS-278's non-aborting case). Category D: the reading is fixed,
not changed. So the implementation here and the specification now agree, in that order -
the code was written against the reading, then the reading was written down. Both
alternatives are recorded with reasons - aligning with PS-269's quiet omission discards the
distinction the two lookup forms exist to express, and blessing the raw index makes a field
declared as a label report an integer, which no generated JSON Schema can describe without
admitting values the field should never hold.

Three existing tests asserted the old behaviour and were rewritten, which is worth noting
because of *how* they were written: one accepted either outcome ("out of range should
either return raw value or 'unknown'"), and two asserted only that the key was present,
which the raw index satisfied. A test written to pass whatever the code does is how a
requirement stays unimplemented. CR-2026-004 is unaffected - it narrowed PS-104 and says in
as many words that no existing requirement changes behaviour.

**C needed more than the others, and turned up two further defects.**

- Its lookups are stored keyed whichever form they came from, so PS-105 could not be told
  from PS-269. `field_def_t` gained `lookup_is_sequence`, and the v1 binary format carries
  it in the high bit of the lookup count byte (the count is bounded by SCHEMA_MAX_LOOKUP,
  far under 0x7F). A format change, so an old reader would misread a new binary that sets
  the flag; a new reader reading an old binary sees no flag and keeps mapping semantics.
- **C had two lookup sites that disagreed with each other.** The enum path omitted on a
  miss (PS-269, correct); the ordinary-field path stored the raw integer, so C violated
  PS-269 as well for every plain field with a lookup. Both now agree.
- `binary_schema_v2.py` still cannot express a sequence lookup at all -
  `LookupTable.add()` calls `.items()` on it and raises. Untouched: v2 is not what C reads.

### Two build defects found while testing the C change

Both were found by mutation-testing the new selftest - deliberately reverting the C
behaviour to check the test caught it. It did not, twice, for different reasons.

- **`make` tracked no header dependencies.** A header-only change rebuilt nothing, and the
  entire schema interpreter is a header, so `make selftest` was passing against stale
  objects after exactly the changes most worth testing. Fixed with `-MMD -MP` and
  `-include $(SELFTEST_OBJS:.o=.d)`. Verified by mutating the header without `make clean`
  and watching the selftest fail, then restoring and watching it pass.
- **`make clean` ran `rm -rf build-*`, which deleted the tracked `build-system/`
  directory.** It really did delete `build-system/syntax/protobuf.xml` on my run; I
  restored it from git. Now matched on the variant suffix
  (`build-*-debug build-*-release build-*-coverage`), which cannot match `build-system`.

Also: `include/schema_interpreter.h` called `snprintf` without including `<stdio.h>`, so it
only compiled where the including file happened to have pulled it in already. Every
existing caller did, which is why nothing noticed until a new file included the header
first.

### The output schema's closed enum is now exactly right

It declared a sequence lookup's entries as a closed `enum` while every implementation could
still report a raw index, which was arguably too strict. With PS-105 implemented, no
conformant decode can report a value outside the sequence, so the declaration and the
behaviour agree.

### How the PS-105 gap was found

Found while typing sequence lookups for the output schema, not by any test: no corpus
vector indexes a sequence out of bounds, and the tests that touched the case were written
to accept whatever the code did. The choice at the time was between implementing PS-105 and
withdrawing it in favour of the raw fallback; implementing it kept the output schema's
closed `enum` and the label set for 260 corpus fields. See the section above for what that
took.

### The spec's conformance tables are generated now

Reported as a known gap when CR-2026-009 went in, and now closed. The M/R/O tables in
clause 10 of `la-payload-schema` were extracted once and hand-maintained, so they had
drifted: rows quoting wording CRs had replaced, source line references about twenty lines
out, and **no rows at all for roughly fifty requirements** added from CR-2026-002 onwards
(213/36/27 became 238/52/29 when regenerated).

`tools/generate-conformance-tables.py` builds them from the sections, reusing
`extract-requirements.py` as a library so there is one definition of "a normative
statement" for both artifacts. `--check` is wired into `make quality-all`.

Two things worth carrying forward:

- **My earlier caution was wrong.** I said the M-numbers were referenced by certification
  tests, so renumbering would be risky. Nothing references them - not a test, not a tool,
  only a historical notes file in this repo citing a different range (M001-M234). Checking
  took one grep and would have saved the hedge.
- **The extractor deleted HTML comments before counting lines**, so every requirement below
  a multi-line comment was reported early - four lines, in `07-output-formats.md`. Found
  because those six were the only requirements my by-line verification could not match to a
  row; the fix was to blank comments in place. The verification was worth more than the
  generator: it is what turned "the table looks right" into 274 of 274 bullets matched to a
  row at its exact line.

### The spec repo's test suite corrupted a tracked file

Found by running it. `python3 -m pytest tools/tests` in `la-payload-schema` wrote two
invented release sections into the tracked `CHANGELOG.md` - `[2.5.0]` and `[1.0.1]`, dated
today, with empty Added/Changed/Fixed headings. Anyone running the suite and committing
without reading `git status` would have published phantom releases.

The cause was a relative default: `bump-version.py`'s `--changelog` defaults to
`CHANGELOG.md`, resolved against the working directory, and two tests of `main()`
redirected `--spec-info` into a temp directory but not `--changelog`. The two version
numbers in the pollution are exactly the ones those tests bump to. Both now point at a
temp file and assert the version reaches it. Verified by md5sum across a full run.

**Both suites are green now.** Every failure was pre-existing, confirmed against a worktree
at the pre-session commit, and each turned out to be a stale test rather than a real fault:

| Suite | Was | Now |
|---|---|---|
| `pytest tools/tests` | 433 passed, 2 failed | **436 passed, 0 failed** |
| `make test` (`self-test.sh`) | 68 passed, 3 failed | **70 passed, 0 failed**, 13 skipped |

- The two pytest failures were `test_assemble_slides.py`, now fixed, so **the pytest suite
  is 436 passed with no failures**. `ac2a3fc` deliberately removed
  `parts.append('\n# Slides\n')` - its message says so - because the heading rendered as an
  extra section-title slide, which was the duplicate-slide bug it set out to fix. The tests
  had asserted that heading. `# Slides` is only an *input* marker in `main.md`, one of four
  splice points, and neither fixture contained one, so the heading could never have appeared
  in their output. They now assert the include block, the slide filenames, and
  header-slides-footer ordering, plus that `# Slides` is absent - which pins the fix instead
  of leaving it to be undone silently. The marker path had no assembly-level coverage at
  all, so removing the heading could not be told from breaking the splice; it has a test
  now.
- The three self-test failures were `self-test.sh` requiring template section names this
  specification has never used - `01-introduction`, `98-glossary`, `99-bibliography`. It
  reported three missing sections on every run while nothing was missing, and **being
  permanently red it could not report anything real either**, which is the worse half of
  the defect. Replaced with a check derived from the repository, in both directions: every
  section `main.md` includes must exist, and every section file must be included by
  `main.md` or it is silently absent from the spec while looking present in the tree.
  Neither direction can be expressed as a fixed list, and neither needs touching when a
  section is added or renamed.
- Baseline was 67 passed there; it is 68 now because `generate-conformance-tables.py`
  clears the tool-syntax check.

### Known gap: `length: $variable`

The specification allows `length` to be an integer, a `$variable` reference, or
`remaining`. The reference form is implemented nowhere - only `repeat`'s separate
`byte_length` key resolves `$len`. `resolve_length()` now raises a message that says so,
rather than `int()`'s "invalid literal for int() with base 10: '$len'". No schema uses it.

### Known gap: `remaining` is not representable in the binary schema

`tools/binary_schema_v2.py` encodes `length` as an unsigned varint, and the C interpreter
has no notion of a remainder. So the MCU tier cannot express `remaining` at all. It fails
loudly (`encode_varint` raises on the string) rather than writing something wrong, and
rbs30x cannot be compiled to a binary schema today for unrelated pre-existing reasons -
`binary_schema_v2.py` and `generate-c.py` both fail on it at HEAD as well. Worth a
sentinel value in the format when the gateway front-end goes in.

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
