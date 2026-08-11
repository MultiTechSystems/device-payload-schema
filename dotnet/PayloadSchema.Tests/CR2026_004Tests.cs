// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-004 (PS-265 .. PS-270): computed field names, sparse lookup mappings,
/// and negated or wildcard TLV case keys.
///
/// Also pins the composite case key fix. Case keys were compared as strings against
/// a tag rendered without a space ("[1,117]" versus the "[1, 117]" schemas are
/// written with), so every composite key missed and channel/type schemas decoded to
/// nothing.
/// </summary>
public class CR2026_004Tests
{
    static Dictionary<string, object?> Decode(string yaml, byte[] payload)
    {
        var schema = SchemaParser.Parse(yaml);
        return SchemaDecoder.Decode(schema, payload);
    }

    const string Sparse = @"
name: sparse
fields:
  - name: button
    type: u8
    lookup: {1: short, 2: long, 3: double}
";

    [Theory]
    [InlineData(1, "short")]
    [InlineData(2, "long")]
    [InlineData(3, "double")]
    public void SparseMappingMatchesEveryKey(byte raw, string expected)
    {
        Assert.Equal(expected, Decode(Sparse, new[] { raw })["button"]);
    }

    [Fact]
    public void UnmappedValueOmitsTheField()
    {
        Assert.False(Decode(Sparse, new byte[] { 0x09 }).ContainsKey("button"));
    }

    [Fact]
    public void DefaultIsUsedWhenDeclared()
    {
        const string yaml = @"
name: with_default
fields:
  - name: state
    type: u8
    lookup: {1: on, default: unknown}
";
        Assert.Equal("unknown", Decode(yaml, new byte[] { 0x09 })["state"]);
    }

    [Fact]
    public void SequenceFormOutOfRangeIsAnError()
    {
        const string yaml = @"
name: seq
fields:
  - name: relay
    type: u8
    lookup: [""off"", ""on""]
";
        Assert.Equal("on", Decode(yaml, new byte[] { 0x01 })["relay"]);
        // PS-105: an out-of-bounds index is an error, not the raw value. This asserted
        // only that the key was present, which the raw index satisfied.
        var thrown = Assert.Throws<InvalidOperationException>(
            () => Decode(yaml, new byte[] { 0x07 }));
        Assert.Equal("lookup index 7 out of bounds for 2 entries", thrown.Message);
    }

    [Fact]
    public void NameFromBuildsTheKeyFromThePayload()
    {
        const string yaml = @"
name: computed
endian: little
fields:
  - name: region_id
    type: u8
  - name: avg_dwell
    name_from: ""region_${region_id}_avg_dwell""
    type: u16
";
        var result = Decode(yaml, new byte[] { 0x03, 0x10, 0x00 });
        Assert.True(result.ContainsKey("region_3_avg_dwell"));
        Assert.Equal(3, Convert.ToInt32(result["region_id"]));
    }

    [Fact]
    public void NameFromWithAnUnresolvedReferenceThrows()
    {
        const string yaml = @"
name: bad
fields:
  - name: v
    type: u8
    name_from: ""x_${nope}""
";
        Assert.Throws<InvalidOperationException>(() => Decode(yaml, new byte[] { 0x01 }));
    }

    const string Tagged = @"
name: tags
endian: little
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
        - name: channel_type
          type: u8
      tag_key: [channel_id, channel_type]
      cases:
        ""[1, 200]"":
          - name: exact
            type: u8
        ""[1, !0]"":
          - name: any_but_zero
            type: u8
        ""[2, *]"":
          - name: any_type
            type: u8
";

    [Theory]
    [InlineData(new byte[] { 0x01, 0xc8, 0x07 }, "exact")]
    [InlineData(new byte[] { 0x01, 0x05, 0x01 }, "any_but_zero")]
    [InlineData(new byte[] { 0x02, 0x63, 0x0a }, "any_type")]
    public void CaseKeysMatchWithCorrectPrecedence(byte[] payload, string expected)
    {
        Assert.True(Decode(Tagged, payload).ContainsKey(expected));
    }

    [Fact]
    public void NegatedKeyExcludesItsValue()
    {
        Assert.False(Decode(Tagged, new byte[] { 0x01, 0x00, 0x09 }).ContainsKey("any_but_zero"));
    }

    [Fact]
    public void CompositeCaseKeysWithSpacesMatch()
    {
        // The corpus writes "[1, 117]"; a string comparison against "[1,117]" missed.
        const string yaml = @"
name: spaced
endian: little
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
        - name: channel_type
          type: u8
      tag_key: [channel_id, channel_type]
      cases:
        ""[1, 117]"":
          - name: battery
            type: u8
";
        Assert.Equal(90, Convert.ToInt32(Decode(yaml, new byte[] { 0x01, 0x75, 0x5a })["battery"]));
    }
}
