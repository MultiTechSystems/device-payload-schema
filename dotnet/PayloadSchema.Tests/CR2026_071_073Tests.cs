// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-071 (a field's arithmetic pipeline) and CR-2026-073 (one operation per stage).
///
/// The decode order - source, modifiers, transform, lookup, with a failed guard's `else`
/// reported as declared - is held to every implementation by three corpus fixtures:
/// _language-conformance/arithmetic-order-computed.yaml, arithmetic-order-read.yaml and
/// guard-else-as-declared.yaml. What a fixture cannot state is a schema that must be
/// refused, so the rejections are here, mirroring tests/test_cr_2026_071_073.py.
/// </summary>
public class CR2026_071_073Tests
{
    const string Guard = "guard: {when: [{field: $x, gt: 0}], else: 0}";

    static PayloadSchemaDefinition Load(string fields)
        => SchemaParser.Parse("name: probe\nfields:\n" + fields);

    static Dictionary<string, object?> Decode(string fields, string hex)
        => SchemaDecoder.Decode(Load(fields), Convert.FromHexString(hex));

    static void AssertRejected(string fields, string tag)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => Load(fields));
        Assert.Contains(tag, thrown.Message);
    }

    // PS-445: ignored with success before (a u8 decoded its raw value; a literal took the else).
    [Theory]
    [InlineData("- {name: v, type: u8, " + Guard + "}\n")]
    [InlineData("- {name: v, type: s16, " + Guard + "}\n")]
    [InlineData("- {name: v, type: number, value: 3, " + Guard + "}\n")]
    public void AGuardOffAComputedFieldIsRejected(string field)
        => AssertRejected("- {name: x, type: u8}\n" + field, "PS-445");

    // The rule reaches every construct body, since each is a field list of its own.
    [Theory]
    [InlineData("- byte_group: {size: 1, fields: [{name: v, type: 'u8[0:7]', consume: 1, " + Guard + "}]}\n")]
    [InlineData("- match: {field: $x, cases: {3: [{name: v, type: u8, " + Guard + "}]}}\n")]
    [InlineData("- tlv: {tag_size: 1, length_size: 0, cases: {1: [{name: v, type: u8, " + Guard + "}]}}\n")]
    [InlineData("- flagged: {field: x, groups: [{bit: 0, fields: [{name: v, type: u8, " + Guard + "}]}]}\n")]
    [InlineData("- {name: r, type: repeat, count: 1, fields: [{name: v, type: u8, " + Guard + "}]}\n")]
    [InlineData("- {name: o, type: object, fields: [{name: v, type: u8, " + Guard + "}]}\n")]
    public void AGuardIsCheckedWhereverTheFieldSits(string construct)
        => AssertRejected("- {name: x, type: u8, var: x}\n" + construct, "PS-445");

    [Theory]
    [InlineData("ref: $x")]
    [InlineData("compute: {op: add, a: $x, b: 1}")]
    [InlineData("type: integer, compute: {op: add, a: $x, b: 1}")]
    public void AGuardOnAComputedFieldIsAccepted(string source)
    {
        var type = source.Contains("type:") ? "" : "type: number, ";
        var output = Decode($"- {{name: x, type: u8}}\n- {{name: v, {type}{source}, {Guard}}}\n", "03");
        Assert.True(output.ContainsKey("v"));
    }

    // PS-452: this implementation decoded {add: 1, mult: 2} as 3 (mult, then add) and
    // {op: round, add: 1} as the rounded value with the add dropped.
    [Theory]
    [InlineData("{add: 1, mult: 2}", "holds add, mult")]
    [InlineData("{div: 2, sqrt: true}", "holds div, sqrt")]
    [InlineData("{op: round, add: 1}", "holds op, add")]
    [InlineData("{}", "holds none")]
    [InlineData("{decimals: 2}", "holds none")]
    [InlineData("{ties: away}", "holds none")]
    public void AStageWithoutExactlyOneOperationIsRejected(string stage, string held)
    {
        var thrown = Assert.Throws<InvalidOperationException>(
            () => Load($"- {{name: v, type: u8, transform: [{stage}]}}\n"));
        Assert.Contains("PS-452", thrown.Message);
        Assert.Contains(held, thrown.Message);
    }

    [Theory]
    [InlineData("- {name: v, type: u8, transform: [{add: 1, decimals: 2}]}\n", "PS-452")]
    [InlineData("- {name: v, type: u8, transform: [3]}\n", "PS-452")]
    [InlineData("- {name: v, type: u8, transform: {add: 1}}\n", "PS-102")]
    [InlineData("- {name: v, type: u8, transform: [{sub: 3, add: 1}]}\n", "PS-390")]
    public void AMalformedTransformIsRejected(string fields, string tag) => AssertRejected(fields, tag);

    [Fact]
    public void TheParametersOfAnOpStageArePartOfItsOneOperation()
    {
        var output = Decode("- {name: v, type: u8, transform: [{op: round, decimals: 1, ties: away}, {add: 1}]}\n", "03");
        Assert.Equal(4.0, Decoded.Num(output["v"]));
    }

    [Fact]
    public void AStageIsCheckedWhereverTheFieldSits()
        => AssertRejected("- byte_group: {size: 1, fields: [{name: hi, type: 'u8[4:7]', transform: [{add: 1, div: 2}]}, "
            + "{name: lo, type: 'u8[0:3]', consume: 1}]}\n", "PS-452");

    // PS-443: the bare modifiers on a compute were dropped, so this reported 2.
    [Fact]
    public void AComputeTakesItsModifiersBeforeItsStages()
    {
        var output = Decode("- {name: g, type: u8}\n"
            + "- {name: m, type: number, compute: {op: add, a: $g, b: 0}, mult: 10}\n"
            + "- {name: t, type: number, compute: {op: add, a: $g, b: 0}, mult: 10, transform: [{add: 1}]}\n", "02");
        Assert.Equal(20.0, Decoded.Num(output["m"]));
        Assert.Equal(21.0, Decoded.Num(output["t"]));
    }

    // PS-444: the lookup indexed the else, reporting the eighth label for `else: 7`.
    [Fact]
    public void AFailedGuardsElseIsReportedAsDeclared()
    {
        var output = Decode("- {name: g, type: u8}\n"
            + "- {name: v, type: number, ref: $g, guard: {when: [{field: $g, gt: 0}], else: 7}, "
            + "mult: 10, lookup: [a, b, c, d, e, f, g, h]}\n", "00");
        Assert.Equal(7.0, Decoded.Num(output["v"]));
    }

    // PS-444: the guard precedes the source, so a failed one never resolves its reference.
    [Fact]
    public void AGuardIsEvaluatedBeforeTheSource()
    {
        var output = Decode("- {name: g, type: u8}\n"
            + "- {name: v, type: number, ref: $absent, guard: {when: [{field: $g, gt: 0}], else: -1}}\n", "00");
        Assert.Equal(-1.0, Decoded.Num(output["v"]));
    }

    // PS-105: a computed value may carry a fraction. It indexes no entry of a sequence,
    // and was truncated before - 1.5 reported as an out-of-bounds index 1.
    [Fact]
    public void AFractionalValueIndexesNoEntryOfASequence()
    {
        const string fields = "- {name: x, type: u8}\n- {name: v, type: number, ref: $x, div: 2, lookup: [a, b, c, d]}\n";
        Assert.Equal("c", Decode(fields, "04")["v"]);
        var thrown = Assert.Throws<InvalidOperationException>(() => Decode(fields, "03"));
        Assert.Contains("PS-105", thrown.Message);
        Assert.Contains("1.5", thrown.Message);
    }

    // PS-269: a fraction matches no key of a mapping, which ToInt once truncated to key 2.
    [Fact]
    public void AFractionalValueMatchesNoKeyOfAMapping()
    {
        const string fields = "- {name: x, type: u8}\n- {name: v, type: number, ref: $x, div: 2, lookup: {2: two}}\n";
        Assert.Equal("two", Decode(fields, "04")["v"]);
        Assert.False(Decode(fields, "05").ContainsKey("v"));
    }
}
