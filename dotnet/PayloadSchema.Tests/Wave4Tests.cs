// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>0.5.2 wave 4: the rejections; the fixtures hold the decodes of every new type.</summary>
public class Wave4Tests
{
    [Theory]
    [InlineData("{name: v, type: s16, encoding: bcd}")]
    [InlineData("{name: v, type: u16, encoding: zigzag}")]
    [InlineData("{name: v, type: u8, match_value: {a: 1}}")]
    [InlineData("{name: v, type: bitfield_string, length: 1, parts: [[0, 8, octal]]}")]
    [InlineData("byte_group: {size: 1, fields: [{name: a, type: 'u8[0:3]', endian: little}]}")]
    public void Rejected(string field)
    {
        Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse("name: p\nfields:\n  - " + field + "\n"));
    }

    [Theory]
    [InlineData("uflt16", 0.5, 0xF800UL)]
    [InlineData("uflt16", 0.001, 0x6831UL)]
    [InlineData("sflt16", -0.25, 0xF400UL)]
    [InlineData("sflt24", 1.0, 0x3F0000UL)]
    public void MinifloatEncoder(string type, double value, ulong word)
    {
        var schema = SchemaParser.Parse($"name: p\nfields:\n  - {{name: v, type: {type}}}\n");
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["v"] = value });
        Assert.Empty(result.Errors);
        Assert.Equal(Helpers.EncodeUint(word, type == "sflt24" ? 3 : 2, "big"), result.Payload);
    }
}
