// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-111: a repeat of single values (PS-528, PS-529, PS-531), an index counted from
/// `index_base` (PS-530, PS-375), and null for an omitted element. The cases and messages
/// are the spec prototype's TestCR2026111ElementRepeat.
/// </summary>
public class CR2026_111Tests
{
    static PayloadSchemaDefinition Parse(string fields) =>
        SchemaParser.Parse("name: t\nendian: big\nfields:\n" + fields);

    const string Increments =
        "  - name: increments\n    type: repeat\n    until: end\n    element: {type: u16, div: 10}\n";

    static List<object?> Values(Dictionary<string, object?> output, string key) =>
        Assert.IsType<List<object?>>(output[key]);

    [Fact]
    public void ItReportsAnArrayOfValues()
    {
        var output = SchemaDecoder.Decode(Parse(Increments), Convert.FromHexString("00780096005A"));
        Assert.Equal(new double[] { 12, 15, 9 }, Values(output, "increments").Select(Convert.ToDouble));
    }

    [Fact]
    public void AnOmittedValueIsNullInItsPosition()
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - name: v\n    type: repeat\n    count: 3\n    element: {type: u8, sentinel: [255]}\n"),
            Convert.FromHexString("01FF03"));
        var v = Values(output, "v");
        Assert.Equal(3, v.Count);
        Assert.Equal(1.0, Convert.ToDouble(v[0]));
        Assert.Null(v[1]);
        Assert.Equal(3.0, Convert.ToDouble(v[2]));
    }

    [Fact]
    public void AMappingMissIsNullToo()
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - name: v\n    type: repeat\n    count: 2\n    element: {type: u8, lookup: {1: on}}\n"),
            Convert.FromHexString("0102"));
        Assert.Equal(new object?[] { "on", null }, Values(output, "v"));
    }

    [Fact]
    public void PresentIfRefersToTheValueByItsName()
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - name: v\n    type: repeat\n    count: 3\n    present_if: {field: $x, ne: 0}\n"
            + "    element: {name: x, type: u8}\n"), Convert.FromHexString("010002"));
        Assert.Equal(new double[] { 1, 2 }, Values(output, "v").Select(Convert.ToDouble));
    }

    [Fact]
    public void AnObjectElementReportsTheObject()
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - name: v\n    type: repeat\n    until: end\n    element:\n      type: object\n"
            + "      fields:\n        - {name: a, type: u8}\n"), Convert.FromHexString("0102"));
        var v = Values(output, "v");
        Assert.Equal(1.0, Convert.ToDouble(Assert.IsType<Dictionary<string, object?>>(v[0])["a"]));
    }

    [Theory]
    [InlineData("    fields: [{name: a, type: u8}]\n    element: {type: u8}\n",
        "Repeat 'v' declares both of `fields` and `element`; it declares exactly one (PS-083, PS-528)")]
    [InlineData("",
        "Repeat 'v' declares neither of `fields` and `element`; it declares exactly one (PS-083, PS-528)")]
    [InlineData("    element: 3\n", "Repeat 'v': `element` is a single field definition (PS-528)")]
    [InlineData("    index: i\n    index_base: \"1\"\n    element: {type: u8}\n",
        "Repeat 'v': `index_base` is an integer (PS-530)")]
    [InlineData("    index: i\n    index_base: 1.5\n    element: {type: u8}\n",
        "Repeat 'v': `index_base` is an integer (PS-530)")]
    [InlineData("    index: i\n    index_base: true\n    element: {type: u8}\n",
        "Repeat 'v': `index_base` is an integer (PS-530)")]
    public void TheSchemaRulesAreRejectedAtLoad(string body, string message)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() =>
            Parse("  - name: v\n    type: repeat\n    count: 1\n" + body));
        Assert.Equal(message, thrown.Message);
    }

    [Fact]
    public void AnUnnamedRepeatIsLabelledUnnamed()
    {
        var thrown = Assert.Throws<InvalidOperationException>(() =>
            Parse("  - type: repeat\n    count: 1\n"));
        Assert.Equal("Repeat '<unnamed>' declares neither of `fields` and `element`; it declares "
            + "exactly one (PS-083, PS-528)", thrown.Message);
    }

    [Fact]
    public void TheEncoderTakesAnArrayOfValues()
    {
        var result = SchemaEncoder.Encode(Parse(Increments),
            new Dictionary<string, object?> { ["increments"] = new List<object?> { 12, 15, 9 } });
        Assert.True(result.Success, string.Join("; ", result.Errors));
        Assert.Equal(Convert.FromHexString("00780096005A"), result.Payload);
    }

    [Fact]
    public void ANullPositionIsRefusedOnEncode()
    {
        var result = SchemaEncoder.Encode(Parse(Increments),
            new Dictionary<string, object?> { ["increments"] = new List<object?> { 12, null } });
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e =>
            e.Contains("Repeat 'increments' has null at position 1, and its element declares no "
                + "`sentinel` to write for it (PS-544)"));
    }

    [Fact]
    public void IndexBaseNumbersFrom1()
    {
        var output = SchemaDecoder.Decode(Parse(
            "  - name: devices\n    type: repeat\n    count: 2\n    index: i\n    index_base: 1\n"
            + "    fields:\n      - {name: id, type: u16, name_from: \"Device${i}ID\"}\n"),
            Convert.FromHexString("000A000B"));
        var devices = Values(output, "devices");
        Assert.True(((Dictionary<string, object?>)devices[0]!).ContainsKey("Device1ID"));
        Assert.True(((Dictionary<string, object?>)devices[1]!).ContainsKey("Device2ID"));
    }

    [Fact]
    public void IndexBaseEncodesAsItDecodes()
    {
        var schema = Parse("  - name: v\n    type: repeat\n    count: 2\n    index: i\n    index_base: 1\n"
            + "    fields:\n      - {name: n, type: u8}\n      - {name: pos, type: number, ref: $i}\n");
        var payload = Convert.FromHexString("0507");
        var output = SchemaDecoder.Decode(schema, payload);
        var v = Values(output, "v");
        Assert.Equal(1.0, Convert.ToDouble(((Dictionary<string, object?>)v[0]!)["pos"]));
        Assert.Equal(2.0, Convert.ToDouble(((Dictionary<string, object?>)v[1]!)["pos"]));
        Assert.Equal(payload, SchemaEncoder.Encode(schema, output).Payload);
    }

    static Dictionary<string, object?> FieldsMeta(string fields, byte[] payload)
    {
        var output = SchemaDecoder.Interpret(SchemaParser.Parse("name: t\nfields:\n" + fields),
            payload, new InputContext());
        var meta = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        return Assert.IsType<Dictionary<string, object?>>(meta["fields"]);
    }

    [Fact]
    public void TheMetaEntryNestsTheElement()
    {
        var fields = FieldsMeta("  - name: temperatures\n    type: repeat\n    count: 8\n    index: i\n"
            + "    index_base: 1\n    identity: i\n    element: {name: t, type: u16, unit: Cel}\n", new byte[16]);
        var entry = Assert.IsType<Dictionary<string, object?>>(fields["temperatures"]);
        Assert.Equal(new[] { "type", "element", "identity" }, entry.Keys);
        Assert.Equal("repeat", entry["type"]);
        var element = Assert.IsType<Dictionary<string, object?>>(entry["element"]);
        Assert.Equal(new[] { "type", "unit" }, element.Keys);
        Assert.Equal("u16", element["type"]);
        Assert.Equal("Cel", element["unit"]);
        var identity = Assert.IsType<Dictionary<string, object?>>(entry["identity"]);
        Assert.Equal(new[] { "index", "count", "index_base" }, identity.Keys);
        Assert.Equal(1L, Convert.ToInt64(identity["index_base"]));
        Assert.Equal(8L, Convert.ToInt64(identity["count"]));
    }

    [Fact]
    public void ABaseOf0AddsNothingToTheListing()
    {
        var fields = FieldsMeta("  - name: v\n    type: repeat\n    count: 2\n    index: i\n    identity: i\n"
            + "    fields:\n      - {name: a, type: u8}\n", new byte[] { 1, 2 });
        var identity = Assert.IsType<Dictionary<string, object?>>(
            Assert.IsType<Dictionary<string, object?>>(fields["v"])["identity"]);
        Assert.Equal(new[] { "index", "count" }, identity.Keys);
    }
}
