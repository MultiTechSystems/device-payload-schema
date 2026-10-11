// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-118: several records from one repeat element (PS-541), refused on encode
/// (PS-542), and a condition testing a set (PS-543). The cases and messages are the spec
/// prototype's TestCR2026118RecordsAndSets and tools/schema_interpreter.py's.
/// </summary>
public class CR2026_118Tests
{
    static PayloadSchemaDefinition Parse(string fields) =>
        SchemaParser.Parse("name: t\nendian: big\nfields:\n" + fields);

    const string Messages =
        "  - name: messages\n    type: repeat\n    until: end\n    count_as: n\n    index: i\n"
        + "    present_if: {field: $_dataId, not_in: [1, 2, 3, 4, 5, 6]}\n"
        + "    fields:\n"
        + "      - {name: _dataId, type: u8}\n"
        + "      - {name: pos, type: number, ref: $i}\n"
        + "      - match:\n"
        + "          field: $_dataId\n"
        + "          cases:\n"
        + "            7:\n"
        + "              - records:\n"
        + "                  - [{name: type, type: string, value: upload_battery}, {name: battery, type: u16}]\n"
        + "                  - [{name: type, type: string, value: upload_interval}, {name: interval, type: u16, mult: 60}]\n"
        + "            3: [{name: x, type: u8}]\n"
        + "            0: [{name: hw, type: u8}]\n"
        + "  - {name: count, type: number, ref: $n}\n";

    static List<object?> Values(Dictionary<string, object?> output, string key) =>
        Assert.IsType<List<object?>>(output[key]);

    static Dictionary<string, object?> At(List<object?> list, int i) =>
        Assert.IsType<Dictionary<string, object?>>(list[i]);

    [Fact]
    public void RecordsBecomeConsecutiveElementsAndNotInDrops()
    {
        var output = SchemaDecoder.Decode(Parse(Messages), Convert.FromHexString("070064003C0305000A"));
        var m = Values(output, "messages");
        Assert.Equal(3, m.Count);
        Assert.Equal("upload_battery", At(m, 0)["type"]);
        Assert.Equal(100.0, Convert.ToDouble(At(m, 0)["battery"]));
        Assert.Equal("upload_interval", At(m, 1)["type"]);
        Assert.Equal(3600.0, Convert.ToDouble(At(m, 1)["interval"]));
        Assert.Equal(10.0, Convert.ToDouble(At(m, 2)["hw"]));
        // count_as counts the records reported; index counts source elements.
        Assert.Equal(3.0, Convert.ToDouble(output["count"]));
        Assert.Equal(new double[] { 0, 0, 2 }, m.Select(e => Convert.ToDouble(((Dictionary<string, object?>)e!)["pos"])));
    }

    [Fact]
    public void TheElementsOtherFieldsGoIntoEachRecordFirst()
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - name: m\n    type: repeat\n    count: 1\n    fields:\n"
            + "      - {name: channel, type: u8}\n      - {name: _spare, type: u8}\n"
            + "      - records: [[{name: a, type: u8}], [{name: b, type: u8}]]\n"),
            Convert.FromHexString("01FF0203"));
        var m = Values(output, "m");
        Assert.Equal(2, m.Count);
        Assert.Equal(new[] { "channel", "a" }, At(m, 0).Keys);
        Assert.Equal(new[] { "channel", "b" }, At(m, 1).Keys);
        Assert.Equal(1.0, Convert.ToDouble(At(m, 1)["channel"]));
        Assert.Equal(3.0, Convert.ToDouble(At(m, 1)["b"]));
    }

    [Fact]
    public void MetaElementsCoverEveryRecord()
    {
        var output = SchemaDecoder.Interpret(SchemaParser.Parse("name: t\nfields:\n" + Messages),
            Convert.FromHexString("070064003C"), new InputContext());
        var meta = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        var fields = Assert.IsType<Dictionary<string, object?>>(meta["fields"]);
        var elements = Assert.IsType<Dictionary<string, object?>>(
            Assert.IsType<Dictionary<string, object?>>(fields["messages"])["elements"]);
        Assert.Equal(new[] { "pos", "type", "battery", "interval", "x", "hw" }, elements.Keys);
    }

    [Theory]
    [InlineData("  - records: [[{name: a, type: u8}]]\n",
        "`records` belongs in a repeat's element, or a match case within it (PS-541)")]
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    fields: [{records: []}]\n",
        "`records` is a non-empty list of field lists (PS-541)")]
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    fields: [{records: [{name: a, type: u8}]}]\n",
        "`records` is a non-empty list of field lists (PS-541)")]
    [InlineData("  - name: m\n    type: object\n    fields:\n      - records: [[{name: a, type: u8}]]\n",
        "`records` belongs in a repeat's element, or a match case within it (PS-541)")]
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    element:\n      match:\n        length: 1\n"
        + "        cases:\n          1: [{records: [[{name: a, type: u8}]]}]\n",
        "Repeat 'm': an `element` cannot reach a `records` entry (PS-541)")]
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    present_if: {field: $a, in: [1, x]}\n"
        + "    fields: [{name: a, type: u8}]\n",
        "`in` in the present_if of 'm' is a list of numbers (PS-543)")]
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    present_if: {field: $a, not_in: 3}\n"
        + "    fields: [{name: a, type: u8}]\n",
        "`not_in` in the present_if of 'm' is a list of numbers (PS-543)")]
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    present_if: {field: $a, in: [1], ne: 0}\n"
        + "    fields: [{name: a, type: u8}]\n",
        "the present_if of 'm' has 2 comparison operators; a condition has exactly one (PS-126)")]
    // CR-2026-124: PS-126 holds for every condition, not only a set one.
    [InlineData("  - {name: a, type: u8}\n  - name: b\n    type: number\n    ref: $a\n"
        + "    guard: {when: [{field: $a, gt: 0, lt: 3}], else: 0}\n",
        "a guard condition of 'b' has 2 comparison operators; a condition has exactly one (PS-126)")]
    [InlineData("  - {name: a, type: u8}\n  - name: b\n    type: number\n    ref: $a\n"
        + "    guard: {when: [{field: $a}], else: 0}\n",
        "a guard condition of 'b' has 0 comparison operators; a condition has exactly one (PS-126)")]
    [InlineData("  - {name: a, type: u8}\n  - name: b\n    type: number\n    ref: $a\n"
        + "    guard: {when: [{field: $a, in: [true]}], else: 0}\n",
        "`in` in a guard condition of 'b' is a list of numbers (PS-543)")]
    // CR-2026-124: a record field may not share a name the element reports outside.
    [InlineData("  - name: m\n    type: repeat\n    count: 1\n    fields:\n"
        + "      - {name: channel, type: u8}\n"
        + "      - match: {length: 1, cases: {1: [{name: kind, type: u8}]}}\n"
        + "      - records: [[{name: kind, type: u8}], [{name: channel, type: u8}]]\n",
        "Repeat 'm': ['channel', 'kind'] is reported both by a record and by the element outside `records` (PS-541)")]
    public void TheSchemaRulesAreRejectedAtLoad(string fields, string message)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => Parse(fields));
        Assert.Contains(message, thrown.Message);
    }

    [Fact]
    public void AnInternalOrObjectMemberNameIsNoClash()
    {
        var output = SchemaDecoder.Decode(Parse("  - name: m\n    type: repeat\n    count: 1\n    fields:\n"
            + "      - {name: _x, type: u8}\n"
            + "      - {name: o, type: object, fields: [{name: b, type: u8}]}\n"
            + "      - records: [[{name: _x, type: u8}, {name: b, type: u8}]]\n"),
            Convert.FromHexString("01020304"));
        Assert.Single(Values(output, "m"));
    }

    [Fact]
    public void EncodingRecordsIsRefused()
    {
        var schema = Parse("  - name: m\n    type: repeat\n    count: 1\n    fields:\n"
            + "      - records: [[{name: a, type: u8}]]\n");
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?>
        {
            ["m"] = new List<object?> { new Dictionary<string, object?> { ["a"] = 1 } },
        });
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e =>
            e.Contains("Repeat 'm' has elements that yield several records, which cannot be encoded (PS-542)"));
    }

    [Theory]
    [InlineData("in: [1, 3]", new double[] { 1, 3 })]
    [InlineData("not_in: [1, 3]", new double[] { 2 })]
    // CR-2026-124 (PS-543): an empty `in` never holds, an empty `not_in` always does.
    [InlineData("in: []", new double[] { })]
    [InlineData("not_in: []", new double[] { 1, 2, 3 })]
    public void InAndNotIn(string cond, double[] kept)
    {
        var output = SchemaDecoder.Decode(Parse(
            $"  - name: v\n    type: repeat\n    count: 3\n    present_if: {{field: $x, {cond}}}\n"
            + "    element: {name: x, type: u8}\n"), Convert.FromHexString("010203"));
        Assert.Equal(kept, Values(output, "v").Select(Convert.ToDouble));
    }

    [Theory]
    [InlineData("04", 40.0)]
    [InlineData("05", -1.0)]
    public void AGuardTakesIn(string payload, double expected)
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - {name: mode, type: u8}\n"
            + "  - name: setting\n    type: number\n    ref: $mode\n    mult: 10\n"
            + "    guard: {when: [{field: $mode, in: [2, 4]}], else: -1}\n"),
            Convert.FromHexString(payload));
        Assert.Equal(expected, Convert.ToDouble(output["setting"]));
    }
}
