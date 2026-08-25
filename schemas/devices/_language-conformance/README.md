# Language conformance fixtures

These are not devices. Each schema exercises one construct that no device schema
in the corpus uses, so that the four interpreters are held to the same behaviour
for it. They live under `schemas/devices/` because that is the only tree the
corpus runners walk — Python, Go, Java and C# all read it, so a fixture placed
here is checked in every language, which a fixture in `examples/` is not.

Every construct here was previously covered by no corpus vector. Adding them
found two defects that four implementations disagreed about:

- `enum` with `base:`/`values:` was unimplemented in Java, which reported the raw
  integer instead of the mapped name (PS-067).
- `default:` on an enum was ignored by Python, Go and C#, all of which reported
  the raw value where the schema declares a fallback name (PS-068).

`source: generated` on a vector here means the expected value came from the
Python interpreter and pins cross-language agreement, not correctness.
`source: spec-example` means the expected value was read off the specification,
and the implementations were changed to match it.

`encode-padding.yaml` is the one fixture here that exists for the *encode*
direction. A field carrying no value for the caller to supply — an unnamed padding
entry, or a `_`-prefixed internal — still occupies its bytes, and Go's encoder
returned early on both and wrote none of them, so the payload came out three bytes
short and every field after the gap shifted down. `skip-type.yaml` did not catch
it because a *named* skip takes a different branch.

Its padding bytes are zero, unlike `skip-type.yaml`'s `FF FF`, and that is the
point: zero padding makes the vector round-trip, so `encode(decode(payload)) ==
payload` fails the moment such a field stops contributing its bytes. A fixture
whose padding cannot be reconstructed can only ever test decoding. All four
implementations round-trip it, which is why every `plain fixed` floor moved 54 → 55
when it was added.

`tlv-nameless-case.yaml` pins a `byte_group` inside a `tlv` case — a construct
that was broken in both directions, independently, and that `hbi/mla20`'s case
`0x20` uses while shipping no vectors. Adding it found the third and fourth
defects this directory has caught:

- **Python decoded `20 01 51` to `{unknown: 81}`.** A byte_group carries no name
  and no type at the case's top level, so the generic path read its shared byte as
  a `u8` called `unknown` and never descended into the bit ranges. Go descended,
  so the two disagreed completely on the same bytes. Python was fixed to match Go;
  `_decode_tlv` now dispatches the construct.
- **Encoding never selected such a case at all.** A case's claimable names were
  collected from the top level of the case only, so a nameless construct claimed
  nothing, the case was never a candidate, and the channel emitted no bytes *and
  no error*. All four implementations had it, in both the claiming loop and the
  case-fidelity ranking beside it; all four are fixed, and every encode floor moved
  with this fixture. See AGENTS.md.

The three `unknown-tlv-tag-*.yaml` fixtures pin the `unknown` parameter of a `tlv`, which
**no device schema in the corpus sets** - so every mode of it went unexercised while 85 of
the corpus's 87 tlv constructs sat in the shape where the default abandons the rest of the
payload. They are the first vectors to use `expected_warnings` (CR-2026-014) for what it
was added for: asserting what a decode *said*, not only what it read.

- `unknown-tlv-tag-skip.yaml` - the tag-only default. One vector asserts the warning and
  the count of bytes lost; the other asserts `expected_warnings: []`, which is the form
  that catches an edit beginning to discard data.
- `unknown-tlv-tag-skip-delimited.yaml` - the same mode with `length_size`, where the
  entry is stepped over and the fields after it are still trustworthy. Two of the 87
  constructs are in this shape and neither had a vector with an undescribed tag.
- `unknown-tlv-tag-raw.yaml` - the captured entry under `unknown_tags` (PS-303), which
  every implementation built and then dropped under the default `merge: true`. It is the
  only vector in the corpus that reads the key, and adding it found the fifth defect this
  directory has caught: the generated output JSON Schema did not describe `unknown_tags`
  at all, so a consumer reading it could not learn the key exists.

Not covered here, deliberately:

- **The `header:` block.** Java and C# honour it while Python and Go ignore it
  entirely, and the specification does not define it, so there is no authority to
  pin either behaviour. See AGENTS.md.
- **`unknown: error`.** It fails the decode by design, and a vector cannot assert a
  failed decode - there is no vector kind whose expectation is a failure. The unit tests
  in `tests/test_cr_2026_013_unknown_tlv.py` and each language's `CR2026013` file cover
  it; the corpus cannot.
- **A `flagged` inside a `tlv` case.** The same nameless-construct shape as
  `tlv-nameless-case.yaml`, and the claiming fix covers it in Go and Python, but no
  device schema uses it and a fixture would only re-pin what the byte_group one
  already pins. `go/schema/encode_parity_test.go` covers it at the unit level.
