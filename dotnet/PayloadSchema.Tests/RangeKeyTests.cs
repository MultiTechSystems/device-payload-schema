// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// PS-398, PS-151: a range case key has numeric bounds, not necessarily integers, and a
/// malformed one is rejected at load. Mirrors tests/test_range_case_keys.py.
/// </summary>
public class RangeKeyTests
{
    static string Schema(string key) =>
        "name: p\nfields:\n- name: v\n  type: u8\n- match:\n    field: $v\n    default: []\n    cases:\n"
        + $"      '{key}':\n        - {{name: hit, type: u8}}\n";

    [Theory]
    [InlineData("a..b")]
    [InlineData("1..")]
    [InlineData("..2")]
    [InlineData("1...5")]
    [InlineData("1.5.5..9")]
    [InlineData("5..2")]
    [InlineData("9..1.5")]
    public void AMalformedRangeKeyIsRejectedAtLoad(string key)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(Schema(key)));
        Assert.Contains($"match.cases[{key}]: ", thrown.Message);
        Assert.Contains("PS-398", thrown.Message);
    }

    [Theory]
    [InlineData(2, false)]
    [InlineData(3, true)]
    [InlineData(7, true)]
    [InlineData(8, false)]
    public void AnIntegerDiscriminatorMeetsFractionalBounds(int value, bool hit)
    {
        var schema = SchemaParser.Parse(Schema("2.5..7.5"));
        var output = SchemaDecoder.Decode(schema, new byte[] { (byte)value, 9 });
        Assert.Equal(hit, output.ContainsKey("hit"));
    }

    [Fact]
    public void AHexadecimalBoundMatches()
    {
        var schema = SchemaParser.Parse(Schema("0x10..0x1F"));
        Assert.True(SchemaDecoder.Decode(schema, new byte[] { 0x10, 5 }).ContainsKey("hit"));
        Assert.False(SchemaDecoder.Decode(schema, new byte[] { 0x20, 5 }).ContainsKey("hit"));
    }
}
