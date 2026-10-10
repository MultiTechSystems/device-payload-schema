// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-104: a lookup label may be a boolean (PS-106), reported as a JSON boolean, and
/// a boolean label matches only a boolean input (PS-513). Every label was read as text,
/// so this reported the strings "true" and "false", and the encoder wrote `false` through
/// `[true, false]` as index 0. lookup-boolean-labels.yaml holds the decode and round trip;
/// these hold what a fixture cannot state.
/// </summary>
public class CR2026_104Tests
{
    const string Probe = "name: p\nfields:\n"
        + "  - {name: m, type: \"u8[4:7]\", lookup: {0: false, default: true}}\n"
        + "  - {name: q, type: \"u8[0:0]\", consume: 1, lookup: [true, false]}\n";

    [Theory]
    [InlineData("31", true, false)]
    [InlineData("00", false, true)]
    public void DecodesBooleans(string hex, bool m, bool q)
    {
        var output = SchemaDecoder.Decode(SchemaParser.Parse(Probe), Convert.FromHexString(hex));
        Assert.Equal(m, Assert.IsType<bool>(output["m"]));
        Assert.Equal(q, Assert.IsType<bool>(output["q"]));
    }

    [Theory]
    [InlineData(false, "01")]
    [InlineData(true, "00")]
    public void SequenceEncodesByIndex(bool label, string want)
    {
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: g, type: u8, lookup: [true, false]}\n");
        var back = SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["g"] = label });
        Assert.Empty(back.Errors);
        Assert.Equal(want, Convert.ToHexString(back.Payload));
    }

    [Fact]
    public void BitRangesEncode()
    {
        var back = SchemaEncoder.Encode(SchemaParser.Parse(Probe),
            new Dictionary<string, object?> { ["m"] = false, ["q"] = false });
        Assert.Empty(back.Errors);
        Assert.Equal("01", Convert.ToHexString(back.Payload));
    }

    [Fact]
    public void DefaultOnlyBooleanIsRefused()
    {
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: f, type: u8, lookup: {0: false, default: true}}\n");
        Assert.Equal("00", Convert.ToHexString(SchemaEncoder.Encode(schema,
            new Dictionary<string, object?> { ["f"] = false }).Payload));
        var refused = SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["f"] = true });
        Assert.Contains(refused.Errors, e => e.Contains("PS-513"));
    }

    [Fact]
    public void TrueIsNotOne()
    {
        var numbers = SchemaParser.Parse("name: p\nfields:\n  - {name: n, type: u8, lookup: [0, 1]}\n");
        Assert.NotEmpty(SchemaEncoder.Encode(numbers, new Dictionary<string, object?> { ["n"] = true }).Errors);
        var booleans = SchemaParser.Parse("name: p\nfields:\n  - {name: n, type: u8, lookup: {0: false, 1: true}}\n");
        Assert.NotEmpty(SchemaEncoder.Encode(booleans, new Dictionary<string, object?> { ["n"] = "true" }).Errors);
        Assert.False(Helpers.SameLabel(true, 1));
        Assert.False(Helpers.SameLabel(1.0, true));
        Assert.False(Helpers.SameLabel("true", true));
        Assert.True(Helpers.SameLabel(1, 1.0));
    }

    [Fact]
    public void NumberLabelsAreNumbers()
    {
        // Netvox r718n3's multiplier: every label was read as text, so 5 decoded as "5".
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: n, type: u8, lookup: {1: 1, 2: 5, 3: 10, 4: 100}}\n");
        var (ok, value) = Helpers.ToFloat64(SchemaDecoder.Decode(schema, new byte[] { 2 })["n"]);
        Assert.True(ok);
        Assert.Equal(5.0, value);
        Assert.Equal("02", Convert.ToHexString(SchemaEncoder.Encode(schema,
            new Dictionary<string, object?> { ["n"] = 5 }).Payload));
        Assert.NotEmpty(SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["n"] = "5" }).Errors);
    }

    [Fact]
    public void QuotedLabelStaysText()
    {
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: n, type: u8, lookup: [\"true\", \"1\"]}\n");
        Assert.Equal("true", SchemaDecoder.Decode(schema, new byte[] { 0 })["n"]);
        Assert.Equal("1", SchemaDecoder.Decode(schema, new byte[] { 1 })["n"]);
    }

    [Fact]
    public void TemplateRejectsBooleanLabels()
    {
        // PS-407: a ${value} default reports a string, so a boolean label is a schema error.
        var error = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(
            "name: p\nfields:\n  - {name: f, type: u8, lookup: {0: false, default: \"v${value}\"}}\n"));
        Assert.Contains("PS-407", error.Message);
    }
}
