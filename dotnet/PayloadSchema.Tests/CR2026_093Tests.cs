// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-093 (PS-486): a tlv entry is never partly read. A length cut short, or a length
/// declaring more bytes than remain, is an error naming the tlv and the entry's offset,
/// for a known tag and for an unknown one that would be skipped. Mirrors
/// tests/test_cr_2026_093.py, whose cases are the CR's own.
/// </summary>
public class CR2026_093Tests
{
    static PayloadSchemaDefinition Load(int lengthSize, int reserve)
        => SchemaParser.Parse("name: probe\nfields:\n- tlv:\n    tag_size: 1\n"
            + $"    length_size: {lengthSize}\n"
            + (reserve > 0 ? $"    reserve: {reserve}\n" : "")
            + "    cases:\n      1: [{name: a, type: u8}]\n"
            + "      2: [{name: b, type: bytes, length: remaining}]\n");

    [Theory]
    [InlineData(1, 0, "01010702",
        "tlv entry at offset 3: 0 byte(s) remain after its tag, fewer than its 1-byte length (PS-486)")]
    [InlineData(1, 0, "0101070205AABB",
        "tlv entry at offset 3: its length declares 5 byte(s), 2 remain (PS-486)")]
    [InlineData(1, 0, "0101070905AABB",
        "tlv entry at offset 3: its length declares 5 byte(s), 2 remain (PS-486)")]
    [InlineData(2, 0, "010001070200",
        "tlv entry at offset 4: 1 byte(s) remain after its tag, fewer than its 2-byte length (PS-486)")]
    [InlineData(1, 1, "0101070205AABB",
        "tlv entry at offset 3: its length declares 5 byte(s), 1 remain (PS-486)")]
    public void ACutShortEntryIsAnError(int lengthSize, int reserve, string hex, string message)
    {
        var schema = Load(lengthSize, reserve);
        var thrown = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.Decode(schema, Convert.FromHexString(hex)));
        Assert.Equal(message, thrown.Message);
    }

    [Theory]
    [InlineData("010107", null)]
    [InlineData("0101070202AABB", "aabb")]
    public void AnEntryThatFitsDecodes(string hex, string? b)
    {
        var result = SchemaDecoder.Decode(Load(1, 0), Convert.FromHexString(hex));
        Assert.Equal(7L, Convert.ToInt64(result["a"]));
        Assert.Equal(b, result.TryGetValue("b", out var v) ? v as string : null);
        Assert.False(result.ContainsKey("_warnings"));
    }
}
