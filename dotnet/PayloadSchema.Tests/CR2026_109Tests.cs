// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-109: a `$` reference or a `var` to a looked-up field binds the number under a
/// text or boolean label, a numeric label as itself, and the number where the mapping has
/// no entry (PS-523); a match selects on that binding (PS-524). A `name_from` template still
/// writes the label (PS-532). This implementation bound the label everywhere, so a compute
/// over a text label found no operand and a case keyed 129 never matched "report_rsp".
/// lookup-reference-binding.yaml holds the four sites per lookup kind on all five paths;
/// these hold what a fixture cannot state.
/// </summary>
public class CR2026_109Tests
{
    static Dictionary<string, object?> Decode(string fields, string hex) =>
        SchemaDecoder.Decode(SchemaParser.Parse("name: p\nfields:\n" + fields),
            Convert.FromHexString(hex));

    static string Sites(string lookup) =>
        $"  - {{name: code, type: u8, var: c, lookup: {lookup}}}\n"
        + "  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}\n"
        + "  - {name: held, type: number, ref: $c}\n";

    [Theory]
    [InlineData("{1: report, 129: report_rsp}", "81", 258.0, 129.0)]
    [InlineData("{0: false, 1: true}", "01", 2.0, 1.0)]
    [InlineData("[0, 125, 250, 500]", "01", 250.0, 125.0)]
    [InlineData("{1: 10, 2: 20}", "02", 40.0, 20.0)]
    [InlineData("{1: report}", "05", 10.0, 5.0)]
    [InlineData("{1: report, default: spare}", "05", 10.0, 5.0)]
    [InlineData("{1: report, default: \"code_${value}\"}", "05", 10.0, 5.0)]
    public void ComputeAndVarReadTheBinding(string lookup, string hex, double doubled, double held)
    {
        var output = Decode(Sites(lookup), hex);
        Assert.Equal(doubled, Convert.ToDouble(output["doubled"]));
        Assert.Equal(held, Convert.ToDouble(output["held"]));
    }

    [Theory]
    [InlineData("{1: report, 129: report_rsp}", "8107", "129")]
    [InlineData("{0: false, 1: true}", "0107", "1")]
    [InlineData("[0, 125, 250, 500]", "0107", "125")]
    [InlineData("{1: 10, 2: 20}", "0207", "20")]
    [InlineData("{1: report}", "0507", "5")]
    [InlineData("{1: report, default: spare}", "0507", "5")]
    public void AMatchSelectsOnTheBinding(string lookup, string hex, string key)
    {
        var output = Decode($"  - {{name: code, type: u8, lookup: {lookup}}}\n"
            + $"  - match: {{field: $code, cases: {{{key}: [{{name: hit, type: u8}}]}}, "
            + "default: [{name: other, type: u8}]}\n", hex);
        Assert.Equal(7L, Convert.ToInt64(output["hit"]));
        Assert.False(output.ContainsKey("other"));
    }

    [Fact]
    public void AStringCaseKeyMatchesNoNumber()
    {
        // PS-524: the label as a case key no longer selects.
        var output = Decode("  - {name: code, type: u8, lookup: {129: report_rsp}}\n"
            + "  - match: {field: $code, cases: {report_rsp: [{name: hit, type: u8}]}, "
            + "default: [{name: other, type: u8}]}\n", "8107");
        Assert.False(output.ContainsKey("hit"));
        Assert.Equal(7L, Convert.ToInt64(output["other"]));
    }

    [Theory]
    [InlineData("{1: report, 129: report_rsp}", "81", "reading_report_rsp")]
    [InlineData("{0: false, 1: true}", "01", "reading_true")]
    [InlineData("[0, 125, 250, 500]", "01", "reading_125")]
    [InlineData("{1: 10, 2: 20}", "02", "reading_20")]
    [InlineData("{1: report, default: spare}", "05", "reading_spare")]
    [InlineData("{1: report, default: \"code_${value}\"}", "05", "reading_code_5")]
    public void ATemplateWritesTheLabel(string lookup, string hex, string key)
    {
        var output = Decode($"  - {{name: code, type: u8, var: c, lookup: {lookup}}}\n"
            + "  - {name: reading, type: u8, name_from: \"reading_${code}\"}\n"
            + "  - {name: again, type: u8, name_from: \"again_${c}\"}\n", hex + "0708");
        Assert.Equal(7L, Convert.ToInt64(output[key]));
        Assert.Equal(8L, Convert.ToInt64(output["again" + key["reading".Length..]]));
    }

    [Fact]
    public void AMissIsOmittedButBound()
    {
        var output = Decode(Sites("{1: report}"), "05");
        Assert.False(output.ContainsKey("code"));
        Assert.Equal(5.0, Convert.ToDouble(output["held"]));
    }

    [Fact]
    public void ATemplateOverAMissIsAnError()
    {
        var error = Assert.ThrowsAny<Exception>(() => Decode(
            "  - {name: code, type: u8, lookup: {1: report}}\n"
            + "  - {name: reading, type: u8, name_from: \"reading_${code}\"}\n", "0507"));
        Assert.Contains("name_from for 'reading' references code, which reported no value "
            + "(PS-266, PS-532)", error.Message);
    }

    [Fact]
    public void ASentinelLeavesTheReferenceUnbound()
    {
        // Unbound, so the computed field reading it is absent (PS-314, PS-429), rather
        // than 510 from a number the sentinel withheld.
        var data = Decode(
            "  - {name: code, type: u8, sentinel: [255], lookup: {1: report}}\n"
            + "  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}\n", "FF");
        Assert.False(data.ContainsKey("code"));
        Assert.False(data.ContainsKey("doubled"));
    }

    [Fact]
    public void ANumericDefaultLabelBindsItself()
    {
        var output = Decode(Sites("{1: 10, default: 0}"), "05");
        Assert.Equal(0.0, Convert.ToDouble(output["code"]));
        Assert.Equal(0.0, Convert.ToDouble(output["held"]));
    }

    [Fact]
    public void AByteGroupMemberBindsItsNumber()
    {
        var output = Decode("  - byte_group:\n      size: 1\n      fields:\n"
            + "        - {name: mode, type: \"u8[4:7]\", lookup: {8: boost}}\n"
            + "        - {name: level, type: \"u8[0:3]\"}\n"
            + "  - {name: doubled, type: number, compute: {op: mul, a: $mode, b: 2}}\n", "83");
        Assert.Equal("boost", output["mode"]);
        Assert.Equal(16.0, Convert.ToDouble(output["doubled"]));
    }

    [Fact]
    public void ATlvCaseFieldBindsItsNumber()
    {
        var output = Decode("  - tlv:\n      tag_size: 1\n      cases:\n        1:\n"
            + "          - {name: code, type: u8, lookup: {129: report_rsp}}\n"
            + "          - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}\n"
            + "          - {name: reading, type: u8, name_from: \"r_${code}\"}\n", "018107");
        Assert.Equal("report_rsp", output["code"]);
        Assert.Equal(258.0, Convert.ToDouble(output["doubled"]));
        Assert.Equal(7L, Convert.ToInt64(output["r_report_rsp"]));
    }

    [Theory]
    [InlineData("8107")]
    [InlineData("0107")]
    public void AMatchOnALabelRoundTrips(string hex)
    {
        // The encoder recovers the number under the label to choose the case (PS-524).
        var schema = SchemaParser.Parse("name: p\nfields:\n"
            + "  - {name: code, type: u8, var: c, lookup: {1: report, 129: report_rsp}}\n"
            + "  - match: {field: $code, cases: {129: [{name: hit, type: u8}], "
            + "1: [{name: low, type: u8}]}}\n");
        var data = SchemaDecoder.Decode(schema, Convert.FromHexString(hex));
        var back = SchemaEncoder.Encode(schema, data);
        Assert.Empty(back.Errors);
        Assert.Equal(hex, Convert.ToHexString(back.Payload));
    }
}
