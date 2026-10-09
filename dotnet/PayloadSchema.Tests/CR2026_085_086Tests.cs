// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-085 (bytes after the last field, a cut-off tlv tag) and CR-2026-086 (the
/// order of a field's arithmetic, amended: valid_range before the lookup).
///
/// The fixtures _language-conformance/leftover-bytes.yaml, range-before-lookup.yaml and
/// match-default-skip.yaml hold every implementation to the decodes. What a fixture
/// cannot state - an error, a warning not reported twice, an encoder's output - is here,
/// mirroring tests/test_cr_2026_085_086.py.
/// </summary>
public class CR2026_085_086Tests
{
    const string One = "- {name: a, type: u8}\n";

    static PayloadSchemaDefinition Load(string fields)
        => SchemaParser.Parse("name: probe\nfields:\n" + fields);

    static Dictionary<string, object?> Decode(string fields, string hex)
        => SchemaDecoder.Decode(Load(fields), Convert.FromHexString(hex));

    static List<string> Warnings(Dictionary<string, object?> result)
        => result.TryGetValue("_warnings", out var w) && w is List<string> list ? list : new();

    static List<string> Leftover(Dictionary<string, object?> result)
        => Warnings(result).Where(w => w.Contains("PS-472")).ToList();

    // PS-472: the two bytes after `a` were dropped with no word.
    [Fact]
    public void BytesAfterTheLastFieldAreReported()
    {
        var result = Decode(One, "2A0102");
        Assert.Equal(42L, Convert.ToInt64(result["a"]));
        var found = Assert.Single(Leftover(result));
        Assert.Equal("2 byte(s) after the last field left undecoded, from offset 1 (PS-472)", found);
    }

    [Fact]
    public void AnExactFrameReportsNothing()
        => Assert.Empty(Warnings(Decode(One, "2A")));

    // Only the selected list is held to it: a port schema reports from its port's fields.
    [Fact]
    public void APortSchemaReportsItsSelectedList()
    {
        var schema = SchemaParser.Parse(
            "name: probe\nports:\n  1:\n    fields:\n      - {name: a, type: u8}\n");
        var result = SchemaDecoder.DecodeWithPort(schema, new byte[] { 0x2A, 0x00 }, 1);
        Assert.Contains("from offset 1", Assert.Single(Leftover(result)));
    }

    // The tag is consumed, but it is not decoded, so PS-302's warning already says what
    // was left: no second warning.
    [Fact]
    public void APs302WarningIsTheLeftoverWarning()
    {
        var result = Decode("- tlv: {tag_size: 1, cases: {1: [{name: a, type: u8}]}}\n", "012A09FF");
        var warning = Assert.Single(Warnings(result));
        Assert.Contains("left undecoded", warning);
        Assert.Empty(Leftover(result));
    }

    // PS-477: the plain form failed with a bare buffer underflow naming no tlv.
    [Theory]
    [InlineData("{tag_size: 2, cases: {1: [{name: a, type: u8}]}}", "00012A00",
        "tlv entry at offset 3: 1 byte(s) remain, fewer than its 2-byte tag (PS-477)")]
    [InlineData("{tag_fields: [{name: ch, type: u8}, {name: ty, type: u8}], tag_key: [ch, ty], "
        + "cases: {'[1, 2]': [{name: a, type: u8}]}}", "01022A01",
        "tlv entry at offset 3: 1 byte(s) remain, fewer than its 2-byte tag (PS-477)")]
    public void ATagCutOffAtTheEndIsAnError(string tlv, string hex, string message)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => Decode($"- tlv: {tlv}\n", hex));
        Assert.Equal(message, thrown.Message);
    }

    // The device case: ws50x's port-85 tlv has a two-byte tag, and a payload ending one
    // byte into an entry failed here with a buffer underflow. The reference's message.
    [Fact]
    public void Ws50xCutOffTagNamesTheTlv()
    {
        var dir = AppContext.BaseDirectory;
        string? path = null;
        for (int i = 0; i < 10 && dir != null; i++, dir = Path.GetDirectoryName(dir))
        {
            var candidate = Path.Combine(dir, "schemas", "devices", "milesight", "ws50x.yaml");
            if (File.Exists(candidate)) { path = candidate; break; }
        }
        Assert.NotNull(path);
        var schema = SchemaParser.Parse(File.ReadAllText(path!));
        var thrown = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.DecodeWithPort(schema, Convert.FromHexString("FF291111"), 85));
        Assert.Equal("tlv entry at offset 3: 1 byte(s) remain, fewer than its 2-byte tag (PS-477)",
            thrown.Message);
    }

    // The tag fits and the length does not: no entry is decoded, and the bytes are left
    // over from the tag's offset. This was a buffer underflow failing the decode.
    [Fact]
    public void ALengthCutOffLeavesTheEntryOverFromItsTag()
    {
        var result = Decode("- tlv: {tag_size: 1, length_size: 2, cases: {1: [{name: a, type: u8}]}}\n",
            "0100012A0100");
        Assert.Equal(42L, Convert.ToInt64(result["a"]));
        Assert.Equal("2 byte(s) after the last field left undecoded, from offset 4 (PS-472)",
            Assert.Single(Leftover(result)));
    }

    // PS-474: the leftover bytes were never data, so they are not re-encoded.
    [Fact]
    public void AnEncoderWritesNothingAfterTheLastField()
    {
        var schema = Load(One);
        var decoded = SchemaDecoder.Decode(schema, new byte[] { 0x2A, 0x01, 0x02 });
        decoded.Remove("_warnings");
        Assert.Equal(new byte[] { 0x2A }, SchemaEncoder.Encode(schema, decoded).Payload);
    }

    const string Guarded =
        "- {name: x, type: u8}\n"
        + "- {name: v, type: number, ref: $x, guard: {when: [{field: $x, gt: 0}], else: 99}, "
        + "valid_range: [0, 10], out_of_range: omit}\n";

    static Dictionary<string, string> Quality(Dictionary<string, object?> result)
        => (Dictionary<string, string>)result["_quality"]!;

    // PS-443 as amended: a guard reporting its `else` ends the sequence, so 99 is reported
    // though the range stops at 10. It was omitted as out of range.
    [Fact]
    public void AGuardElseIsNotComparedWithTheRange()
    {
        var result = Decode(Guarded, "00");
        Assert.Equal(99.0, Convert.ToDouble(result["v"]));
        Assert.Equal("good", Quality(result)["v"]);
    }

    [Fact]
    public void AValueTheGuardLetsThroughIsCompared()
    {
        var result = Decode(Guarded, "20");
        Assert.False(result.ContainsKey("v"));
        Assert.Equal("out_of_range", Quality(result)["v"]);
    }

    // PS-475: compared after the lookup, a label was no number - every value read "good"
    // and `_quality` was never produced.
    [Fact]
    public void ALookedUpFieldIsComparedOnItsNumber()
    {
        var result = Decode("- {name: f, type: u8, valid_range: [0, 1], lookup: [zero, one, two]}\n", "02");
        Assert.Equal("two", result["f"]);
        Assert.Equal("out_of_range", Quality(result)["f"]);
    }

    // An omitted value is not looked up, so an index its sequence lacks is no error
    // (PS-105 would otherwise fail the decode).
    [Fact]
    public void AnOmittedValueIsNotLookedUp()
    {
        var result = Decode(
            "- {name: f, type: u8, valid_range: [0, 1], out_of_range: omit, lookup: [zero, one]}\n", "05");
        Assert.False(result.ContainsKey("f"));
        Assert.Equal("out_of_range", Quality(result)["f"]);
    }
}
