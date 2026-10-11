// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-121 (PS-544, PS-545), CR-2026-123 (PS-546, PS-547) and CR-2026-126 (PS-548):
/// a null element written as its first sentinel, what an element may be, a repeat's
/// identity from a sibling repeat, and an IPSO instance that is an integer. The cases
/// mirror tests/test_cr_2026_121_123_126.py and the spec prototype's tests.
/// </summary>
public class CR2026_121_123_126Tests
{
    static PayloadSchemaDefinition Parse(string fields) =>
        SchemaParser.Parse("name: t\nendian: big\nfields:\n" + fields);

    static string Repeat(string element, int count = 3, string extra = "") =>
        $"  - name: v\n    type: repeat\n    count: {count}\n{extra}    element: {element}\n";

    static List<object?> Values(Dictionary<string, object?> output, string key) =>
        Assert.IsType<List<object?>>(output[key]);

    static string LoadError(string fields) =>
        Assert.Throws<InvalidOperationException>(() => Parse(fields)).Message;

    const string Sentinel = "{type: u16, sentinel: [0xFFFF, 0xFFFE]}";

    // ------------------------------------------------------------- PS-544

    [Fact]
    public void ANullRoundTripsThroughTheFirstSentinel()
    {
        var schema = Parse(Repeat(Sentinel));
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString("0001FFFF0003"));
        Assert.Null(Values(output, "v")[1]);
        Assert.Equal(Convert.FromHexString("0001FFFF0003"), SchemaEncoder.Encode(schema, output).Payload);
    }

    [Fact]
    public void TheSecondSentinelReEncodesAsTheFirst()
    {
        var schema = Parse(Repeat(Sentinel));
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString("0001FFFE0003"));
        Assert.Equal(Convert.FromHexString("0001FFFF0003"), SchemaEncoder.Encode(schema, output).Payload);
    }

    [Fact]
    public void TheSentinelIsWrittenAsRawBits()
    {
        var result = SchemaEncoder.Encode(Parse(Repeat("{type: u16, div: 10, sentinel: [0xFFFF]}", 2)),
            new Dictionary<string, object?> { ["v"] = new List<object?> { 0.5, null } });
        Assert.Equal(Convert.FromHexString("0005FFFF"), result.Payload);
    }

    [Fact]
    public void ANullWithoutASentinelIsRefusedNamingThePosition()
    {
        var result = SchemaEncoder.Encode(Parse(Repeat("{type: u8}", 2)),
            new Dictionary<string, object?> { ["v"] = new List<object?> { 1, null } });
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e => e.Contains(
            "Repeat 'v' has null at position 1, and its element declares no `sentinel` to write for it (PS-544)"));
    }

    // ------------------------------------------------------------- PS-545

    [Fact]
    public void AnInternalNameIsReportedAndEncodes()
    {
        var schema = Parse(Repeat("{name: _x, type: u8}", 2));
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString("0102"));
        Assert.Equal(new double[] { 1, 2 }, Values(output, "v").Select(Convert.ToDouble));
        Assert.Equal(Convert.FromHexString("0102"), SchemaEncoder.Encode(schema, output).Payload);
    }

    [Fact]
    public void AnInternalNameStillBindsForReferences()
    {
        var output = SchemaDecoder.Decode(Parse(Repeat("{name: _x, type: u8}", 3,
            "    present_if: {field: $_x, ne: 0}\n")), Convert.FromHexString("010002"));
        Assert.Equal(new double[] { 1, 2 }, Values(output, "v").Select(Convert.ToDouble));
    }

    [Theory]
    [InlineData("{byte_group: [{name: a, type: \"u8[0:3]\"}]}")]
    [InlineData("{name: m, match: {length: 1, cases: {}}}")]
    [InlineData("{name: f, type: u8, flagged: {field: $x, groups: []}}")]
    [InlineData("{name: n}")]
    public void AConstructOrTypelessElementIsRejected(string element)
    {
        Assert.Contains("Repeat 'v': `element` is a data field that declares `type`, not a construct (PS-545)",
            LoadError(Repeat(element)));
    }

    [Fact]
    public void AnObjectElementReportsOneObject()
    {
        var output = SchemaDecoder.Decode(Parse(Repeat(
            "{name: o, type: object, fields: [{name: a, type: u8}, {name: b, type: u8}]}", 2)),
            Convert.FromHexString("01020304"));
        var v = Values(output, "v");
        Assert.Equal(4.0, Convert.ToDouble(Assert.IsType<Dictionary<string, object?>>(v[1])["b"]));
    }

    // ------------------------------------------------------------- PS-546, PS-547

    const string Codes = "  - name: codes\n    type: repeat\n    count: 2\n"
        + "    element: {type: u8, lookup: {1: \"1.8.1\", 2: \"2.8.1\", default: \"${value}\"}}\n";

    static string ValuesField(int count = 2, string index = "i", string identity = "$codes[i]",
        string element = "{type: u32}") =>
        $"  - name: values\n    type: repeat\n    count: {count}\n    index: {index}\n"
        + (identity != "" ? $"    identity: {identity}\n" : "") + $"    element: {element}\n";

    static Dictionary<string, object?> FieldsMeta(string fields, byte[] payload)
    {
        var output = SchemaDecoder.Interpret(SchemaParser.Parse("name: t\nendian: big\nfields:\n" + fields),
            payload, new InputContext());
        var meta = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        return Assert.IsType<Dictionary<string, object?>>(meta["fields"]);
    }

    [Fact]
    public void TwoArraysAndTheMetaIdentity()
    {
        var payload = Convert.FromHexString("01020000007B000001C8");
        var output = SchemaDecoder.Decode(Parse(Codes + ValuesField()), payload);
        Assert.Equal(new object?[] { "1.8.1", "2.8.1" }, Values(output, "codes"));
        Assert.Equal(new double[] { 123, 456 }, Values(output, "values").Select(Convert.ToDouble));
        var entry = Assert.IsType<Dictionary<string, object?>>(FieldsMeta(Codes + ValuesField(), payload)["values"]);
        Assert.Equal(new[] { "type", "element", "identity" }, entry.Keys);
        var identity = Assert.IsType<Dictionary<string, object?>>(entry["identity"]);
        Assert.Equal(new[] { "repeat", "values", "open" }, identity.Keys);
        Assert.Equal("codes", identity["repeat"]);
        Assert.Equal(new object?[] { "1.8.1", "2.8.1", "${value}" }, Assert.IsType<List<object?>>(identity["values"]));
        Assert.Equal(true, identity["open"]);
    }

    [Fact]
    public void ASiblingUnitIsListedByTheRepeat()
    {
        var units = "  - name: units\n    type: repeat\n    count: 2\n    element: {type: u8, lookup: {0: Wh, 1: W}}\n";
        var fields = FieldsMeta(units + ValuesField(identity: "", element: "{type: u32, unit: \"$units[i]\"}"),
            Convert.FromHexString("00010000007B000001C8"));
        var element = Assert.IsType<Dictionary<string, object?>>(
            Assert.IsType<Dictionary<string, object?>>(fields["values"])["element"]);
        var unit = Assert.IsType<Dictionary<string, object?>>(element["unit"]);
        Assert.Equal(new[] { "repeat", "values" }, unit.Keys);
        Assert.Equal(new object?[] { "Wh", "W" }, Assert.IsType<List<object?>>(unit["values"]));
    }

    [Fact]
    public void DifferentLengthsAreAnErrorNamingBoth()
    {
        var ex = Assert.ThrowsAny<Exception>(() =>
            SchemaDecoder.Decode(Parse(Codes + ValuesField(count: 1)), Convert.FromHexString("01020000007B")));
        Assert.Contains("Repeat 'values' has 1 values and 'codes', which identifies them, has 2 (PS-546)",
            ex.ToString());
    }

    [Theory]
    [InlineData(false, "i", true)]   // not earlier
    [InlineData(true, "j", true)]    // wrong index
    [InlineData(true, "i", false)]   // no lookup
    public void AReferenceThatIsNotAnEarlierLabelledSiblingIsRejected(bool codesFirst, string index, bool lookup)
    {
        var codes = lookup ? Codes : "  - name: codes\n    type: repeat\n    count: 2\n    element: {type: u8}\n";
        var values = ValuesField(index: index);
        Assert.Contains("PS-546)", LoadError(codesFirst ? codes + values : values + codes));
    }

    // ------------------------------------------------------------- computed count

    [Theory]
    [InlineData("integer")]
    [InlineData("number")]
    public void ACountMayNameAComputedField(string kind)
    {
        var schema = Parse("  - {name: total, type: u8}\n"
            + $"  - {{name: _n, type: {kind}, compute: {{op: sub, a: $total, b: 2}}}}\n"
            + "  - name: v\n    type: repeat\n    count: $_n\n    element: {type: u8}\n");
        var payload = Convert.FromHexString("040A0B");
        var output = SchemaDecoder.Decode(schema, payload);
        Assert.Equal(new double[] { 10, 11 }, Values(output, "v").Select(Convert.ToDouble));
        Assert.Equal(payload, SchemaEncoder.Encode(schema, output).Payload);
    }

    // ------------------------------------------------------------- PS-548

    const string Ps548 = "its ipso.instance can be other than an integer from 0 to 65534 (PS-548)";

    static string Soil(string lookup) =>
        "  - name: s\n    type: repeat\n    count: 2\n    index: i\n    fields:\n"
        + $"      - {{name: k, type: u8, lookup: {lookup}}}\n"
        + "      - {name: t, type: s16, ipso: {object: 3303, instance: $k}}\n";

    [Fact]
    public void IntegerLabelsAreAccepted() => Parse(Soil("{0: 1, 1: 2}"));

    [Theory]
    [InlineData("{0: soil, 1: air}")]
    [InlineData("{0: 70000}")]
    [InlineData("{0: \"1\", default: \"${value}\"}")]
    public void LabelsThatAreNotInstanceIdsAreRejected(string lookup) =>
        Assert.Contains("Field 't': " + Ps548, LoadError(Soil(lookup)));

    [Theory]
    [InlineData("two")]
    [InlineData("-1")]
    [InlineData("65535")]
    [InlineData("true")]
    [InlineData("1.5")]
    public void ALiteralMustBeAnInstanceId(string instance) =>
        Assert.Contains("Field 't': " + Ps548,
            LoadError($"  - {{name: t, type: u8, ipso: {{object: 3303, instance: {instance}}}}}\n"));

    [Theory]
    [InlineData("0")]
    [InlineData("65534")]
    public void ALiteralAtEitherEndIsAccepted(string instance) =>
        Parse($"  - {{name: t, type: u8, ipso: {{object: 3303, instance: {instance}}}}}\n");

    [Theory]
    [InlineData(1, true)]
    [InlineData(65527, true)]
    [InlineData(65528, false)]
    public void AnIndexMustFitFromItsBase(int indexBase, bool ok)
    {
        var fields = $"  - name: s\n    type: repeat\n    count: 8\n    index: i\n    index_base: {indexBase}\n"
            + "    element: {type: u8, ipso: {object: 3303, instance: $i}}\n";
        if (ok) Parse(fields);
        else Assert.Contains("Field '<unnamed>': " + Ps548, LoadError(fields));
    }

    [Theory]
    [InlineData("{0: 3, 1: 4}", true)]
    [InlineData("{0: Wh, 1: W}", false)]
    public void ASiblingReferenceIsHeldToIt(string labels, bool ok)
    {
        var fields = $"  - name: units\n    type: repeat\n    count: 2\n    element: {{type: u8, lookup: {labels}}}\n"
            + ValuesField(identity: "", element: "{type: u32, ipso: {object: 3305, instance: \"$units[i]\"}}");
        if (ok) Parse(fields);
        else Assert.Contains(Ps548, LoadError(fields));
    }
}
