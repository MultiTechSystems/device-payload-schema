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

Not covered here, deliberately: the `header:` block. Java and C# honour it while
Python and Go ignore it entirely, and the specification does not define it, so
there is no authority to pin either behaviour. See AGENTS.md.
