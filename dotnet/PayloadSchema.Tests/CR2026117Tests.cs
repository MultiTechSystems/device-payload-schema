using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-117: a reference naming nothing declared is refused at load (PS-539), and a
/// guard condition on an absent value fails, so its <c>else</c> applies (PS-540). Mirrors
/// tests/test_cr_2026_117_references_and_guards.py; the message is the same in all four.
/// </summary>
public class CR2026117Tests
{
    const string Message = "`$batery` (ref) names no field, var, tag component, index or count_as "
                           + "the schema declares (PS-539)";

    [Fact]
    public void AnUndeclaredReferenceIsRefusedAtLoad()
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(
            "name: t\nfields:\n  - {name: battery, type: u8}\n  - {name: x, type: number, ref: $batery}\n"));
        Assert.Equal(Message, e.Message);
        foreach (var field in new[]
        {
            "  - {name: x, type: u8, name_from: \"k_${batery}\"}\n",
            "  - match: {field: $batery, cases: {1: [{name: y, type: u8}]}}\n",
            "  - {name: x, type: number, ref: $battery, guard: {when: [{field: $batery, gt: 0}], else: 0}}\n",
            "  - {name: x, type: repeat, count: $batery, fields: [{name: y, type: u8}]}\n",
        })
        {
            var f = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(
                "name: t\nfields:\n  - {name: battery, type: u8}\n" + field));
            Assert.Contains("PS-539", f.Message);
        }
    }

    [Theory]
    [InlineData("fields:\n  - {name: raw, type: u8, var: kind}\n  - {name: n, type: number, ref: $kind}\n"
                + "  - tlv: {tag_fields: [{name: rsn, type: u8}, {name: ch, type: u8}], tag_key: ch, "
                + "cases: {1: [{name: r, type: number, ref: $rsn}]}}\n")]
    [InlineData("fields:\n  - {name: e, type: repeat, count: 1, index: i, count_as: k, fields: [{name: v, type: number, ref: $i}]}\n"
                + "  - {name: after, type: number, ref: $k}\n")]
    [InlineData("fields:\n  - {name: v, type: repeat, count: 2, present_if: {field: $element, ne: 0}, element: {type: u8}}\n")]
    [InlineData("fields:\n  - {name: a, type: u8}\n  - {name: b, type: number, ref: $eui}\n"
                + "metadata: {include: [{name: eui, source: $device_eui}]}\n")]
    [InlineData("fields:\n  - {name: a, type: u8, lookup: {1: $nothing}}\n")]
    public void WhatCountsAsDeclared(string doc)
    {
        SchemaParser.Parse("name: t\n" + doc);
    }

    [Theory]
    [InlineData("{when: [{field: $r, gte: 0}], else: -1}", -1.0)]
    [InlineData("{when: [{field: $r, ne: 3}], else: -1}", -1.0)]
    [InlineData("{when: [{field: $r, gte: 0}]}", null)]
    public void AGuardOnAnAbsentValueFails(string guard, double? expected)
    {
        var schema = SchemaParser.Parse("name: t\nfields:\n  - {name: a, type: u8}\n  - {name: d, type: u8}\n"
            + "  - {name: r, type: number, compute: {op: div, a: $a, b: $d}}\n"
            + "  - {name: c, type: number, ref: $a, guard: " + guard + "}\n");
        var output = SchemaDecoder.Decode(schema, new byte[] { 0x08, 0x00 });
        Assert.False(output.ContainsKey("r"));
        if (expected == null)
            Assert.False(output.ContainsKey("c"));
        else
            Assert.Equal(expected.Value, Convert.ToDouble(output["c"]));
    }
}
