// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-133 (PS-550): an encoder given a repeat whose identity, unit or ipso.instance
/// names a sibling repeat rejects arrays of different lengths, naming both, as a decoder
/// does (PS-546). Mirrors tests/test_cr_2026_133_sibling_length_encode.py.
/// </summary>
public class CR2026_133Tests
{
    const string Codes = "  - name: codes\n    type: repeat\n    count: 2\n"
        + "    element: {type: u8, lookup: {1: '1.8.1', 2: '2.8.1'}}\n";

    const string ValuesField = "  - name: values\n    type: repeat\n    count: 2\n    index: i\n"
        + "    identity: $codes[i]\n    element: {type: u32}\n";

    static PayloadSchemaDefinition Parse(string fields) =>
        SchemaParser.Parse("name: t\nendian: big\nfields:\n" + fields);

    static EncodeResult Encode(PayloadSchemaDefinition schema, string sibling, List<object?> values) =>
        SchemaEncoder.Encode(schema, new Dictionary<string, object?>
        {
            [sibling] = new List<object?> { "1.8.1", "2.8.1" },
            ["values"] = values,
        });

    [Fact]
    public void EqualLengthsEncode()
    {
        var result = Encode(Parse(Codes + ValuesField), "codes", new List<object?> { 123, 456 });
        Assert.True(result.Success, string.Join("; ", result.Errors));
        Assert.Equal(Convert.FromHexString("01020000007B000001C8"), result.Payload);
    }

    [Theory]
    [InlineData(3, "Repeat 'values' is given 3 values and 'codes', which identifies them, 2 (PS-550)")]
    [InlineData(1, "Repeat 'values' is given 1 values and 'codes', which identifies them, 2 (PS-550)")]
    public void DifferentLengthsAreRejectedNamingBoth(int count, string message)
    {
        var values = Enumerable.Range(1, count).Select(v => (object?)v).ToList();
        var result = Encode(Parse(Codes + ValuesField), "codes", values);
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e => e.Contains(message));
    }

    [Fact]
    public void ASiblingUnitIsHeldToIt()
    {
        var schema = Parse("  - name: units\n    type: repeat\n    count: 2\n"
            + "    element: {type: u8, lookup: {1: '1.8.1', 2: '2.8.1'}}\n"
            + "  - name: values\n    type: repeat\n    count: 2\n    index: i\n"
            + "    element: {type: u32, unit: '$units[i]'}\n");
        var result = Encode(schema, "units", new List<object?> { 1 });
        Assert.Contains(result.Errors, e => e.Contains(
            "Repeat 'values' is given 1 values and 'units', which identifies them, 2 (PS-550)"));
    }
}
