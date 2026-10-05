// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>CR-2026-045 (references, PS-345 to PS-349, PS-461, PS-462) and CR-2026-051 (literals, PS-357 to PS-361).</summary>
public class CR2026_045Tests
{
    [Theory]
    [InlineData("PS-345", "definitions:\n  d: [{name: v, type: u8}]\nfields:\n  - $ref: '#/definitions/d'\n")]
    [InlineData("PS-348", "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: '#/definitions/missing'\n")]
    [InlineData("PS-462", "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: 'lib.yaml#/definitions/d'\n")]
    [InlineData("PS-461", "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: '#/d'\n")]
    [InlineData("PS-349", "definitions:\n  a: {fields: [{$ref: '#/definitions/b'}]}\n  b: {fields: [{$ref: '#/definitions/a'}]}\nfields:\n  - {name: x, type: u8}\n")]
    public void InvalidReferencesAreRejected(string tag, string body)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse("name: p\n" + body));
        Assert.Contains(tag, thrown.Message);
    }

    [Fact]
    public void AReferenceResolvesInANestedList()
    {
        var schema = SchemaParser.Parse("name: p\ndefinitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n"
            + "  - {name: o, type: object, fields: [{$ref: '#/definitions/d'}]}\n"
            + "  - {name: r, type: repeat, count: 2, fields: [{$ref: '#/definitions/d'}]}\n");
        var output = SchemaDecoder.Decode(schema, new byte[] { 1, 2, 3 });
        Assert.Equal(1.0, Convert.ToDouble(((Dictionary<string, object?>)output["o"]!)["v"]));
        Assert.Equal(2, ((System.Collections.IList)output["r"]!).Count);
    }

    [Theory]
    [InlineData("{name: k, type: string, value: 3}")]
    [InlineData("{name: k, type: number, value: '3'}")]
    [InlineData("{name: k, type: number, value: 3, mult: 2}")]
    public void AMalformedLiteralIsRejected(string field)
    {
        Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse("name: p\nfields:\n  - " + field + "\n"));
    }

    [Fact]
    public void LiteralsAndConstantsEncode()
    {
        var bare = SchemaParser.Parse("name: p\nfields:\n  - {name: s, type: string, length: 2}\n");
        Assert.Contains("PS-361", Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.Decode(bare, new byte[] { 0x61, 0x62 })).Message);
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: cid, type: u8, value: 16}\n"
            + "  - {name: unit, type: string, value: ppm}\n  - {name: x, type: u8}\n");
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["cid"] = 153.0, ["x"] = 5.0 });
        Assert.Equal(new byte[] { 0x10, 0x05 }, result.Payload);
        Assert.Empty(result.Warnings);
    }
}
