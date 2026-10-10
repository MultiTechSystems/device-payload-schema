// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>CR-2026-112 (PS-532): how a name_from template writes the value it substitutes.</summary>
public class CR2026_112Tests
{
    [Theory]
    [InlineData("{name: x, type: bool, bit: 0, consume: 1}", "01", "k_true")]
    [InlineData("{name: x, type: boolean, value: false}", "", "k_false")]
    [InlineData("{name: x, type: u8, div: 10}", "14", "k_2")]
    [InlineData("{name: x, type: u8, div: 4}", "0A", "k_2.5")]
    [InlineData("{name: x, type: u8, div: 10000000}", "01", "k_1e-7")]
    [InlineData("{name: x, type: u8, lookup: {1: eco, 2: boost}}", "02", "k_boost")]
    public void ValuesAreWrittenAsTheGeneratedCodecWritesThem(string source, string hex, string key)
    {
        var schema = SchemaParser.Parse("name: p\nendian: big\nfields:\n  - " + source
            + "\n  - {name: v, type: u8, name_from: \"k_${x}\"}\n");
        var payload = Convert.FromHexString(hex + "09");
        var output = SchemaDecoder.Decode(schema, payload);
        Assert.True(output.ContainsKey(key), string.Join(",", output.Keys));
        Assert.Equal(payload, SchemaEncoder.Encode(schema, output).Payload);
    }

    [Theory]
    [InlineData("object")]
    [InlineData("repeat\n    count: 1")]
    public void AnObjectOrRepeatReferenceIsRejectedAtLoad(string kind)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(
            "name: bad\nfields:\n  - name: grp\n    type: " + kind
            + "\n    fields:\n      - {name: a, type: u8}\n"
            + "  - {name: v, type: u8, name_from: \"v_${grp}\"}\n"));
        Assert.Contains("PS-532", thrown.Message);
    }

    [Fact]
    public void AVariableAndABooleanRoundTripThroughTheirKey()
    {
        var schema = SchemaParser.Parse("name: enc\nendian: big\nfields:\n"
            + "  - name: side\n    type: u8\n    var: at\n    lookup: {1: left, 2: right}\n"
            + "  - {name: wet, type: bool, bit: 0, consume: 1}\n"
            + "  - {name: reading, type: u8, name_from: \"r_${at}_${wet}\"}\n");
        var payload = new byte[] { 0x02, 0x01, 0x05 };
        var output = SchemaDecoder.Decode(schema, payload);
        Assert.True(output.ContainsKey("r_right_true"), string.Join(",", output.Keys));
        Assert.Equal(payload, SchemaEncoder.Encode(schema, output).Payload);
    }
}
