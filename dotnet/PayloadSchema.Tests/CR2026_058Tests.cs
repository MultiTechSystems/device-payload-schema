// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-058: what the specification described and never required. The fixtures in
/// _language-conformance cover what a vector can express; these cover the rejections,
/// errors and absent keys a vector cannot.
/// </summary>
public class CR2026_058Tests
{
    static Dictionary<string, object?> Decode(string fields, string hex)
        => SchemaDecoder.Decode(SchemaParser.Parse("name: p\nfields:\n" + fields), Convert.FromHexString(hex));

    [Theory]
    [InlineData("- {name: v, type: u8, transform: [{round: 1}]}\n")]
    [InlineData("- {name: v, type: u8, transform: [{op: floor}]}\n")]
    [InlineData("- {name: v, type: u8, transform: [{op: round, ties: up}]}\n")]
    [InlineData("- {name: v, type: u8, transform: [{sub: 3}]}\n")]
    [InlineData("- {name: v, type: bytes, length: 2, format: base64, separator: ':'}\n")]
    [InlineData("- {name: v, type: bytes, length: 2, format: 'hex:lower'}\n")]
    [InlineData("- byte_group: {size: 1, fields: [{name: a, type: 'u8[0:3]'}, {name: b, type: 'u8[2:5]'}]}\n")]
    [InlineData("- {name: k, type: u8, var: k}\n- match: {field: $k, length: 1, cases: {1: [{name: a, type: u8}]}}\n")]
    [InlineData("- match: {cases: {1: [{name: a, type: u8}]}}\n")]
    public void InvalidConstructsAreRejectedAtLoad(string fields)
    {
        Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse("name: p\nfields:\n" + fields));
    }

    [Theory]
    [InlineData("even", 78.12, -2.0)]
    [InlineData("away", 78.13, -3.0)]
    public void RoundTies(string ties, double h, double n)
    {
        var output = Decode(
            $"- {{name: h, type: u8, transform: [{{mult: 100}}, {{div: 256}}, {{op: round, decimals: 2, ties: {ties}}}]}}\n"
            + $"- {{name: n, type: s8, transform: [{{div: 2}}, {{op: round, ties: {ties}}}]}}\n", "C8FB");
        Assert.Equal(h, Convert.ToDouble(output["h"]), 12);
        Assert.Equal(n, Convert.ToDouble(output["n"]), 12);
    }

    [Theory]
    [InlineData("count: $n", "4 byte(s) at offset 1")]
    [InlineData("until: end", "2 byte(s) at offset 3")]
    [InlineData("byte_length: 4", "2 byte(s) at offset 3")]
    public void ExceedingMaxIsAnError(string bound, string unparsed)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => Decode(
            "- {name: n, type: u8, var: n}\n- {name: r, type: repeat, max: 2, " + bound
            + ", fields: [{name: v, type: u8}]}\n", "040A141E28"));
        Assert.Contains("max of 2", thrown.Message);
        Assert.Contains(unparsed, thrown.Message);
    }

    [Fact]
    public void AFailedGuardWithoutElseOmitsTheField()
    {
        var output = Decode("- {name: raw, type: u8}\n"
            + "- {name: scaled, type: number, ref: $raw, mult: 2, guard: {when: [{field: $raw, lt: 100}]}}\n"
            + "- {name: after, type: u8}\n", "C805");
        Assert.False(output.ContainsKey("scaled"));
        Assert.Equal(5.0, Convert.ToDouble(output["after"]));
    }

    [Fact]
    public void TheEnumDescriptionFormReportsItsName()
    {
        var output = Decode("- name: m\n  type: enum\n  values:\n    1: {name: standby, description: radio off}\n", "01");
        Assert.Equal("standby", output["m"]);
    }
}
