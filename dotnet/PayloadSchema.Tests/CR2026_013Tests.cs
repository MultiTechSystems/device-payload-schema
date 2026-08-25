// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// PS-301 to PS-303 (CR-2026-013): an unknown TLV tag has to be visible.
///
/// <see cref="DecodeContext"/> already carried a warnings list and the decoder already
/// reported <c>_quality</c>; the warnings went nowhere, so a payload that stopped at an
/// undescribed tag came back as a successful decode carrying fewer fields. <c>raw</c>
/// behaved as <c>skip</c>, building no entry at all.
/// </summary>
public class CR2026_013Tests
{
    /// Tag 0x01 carries a u16 of 60; tag 0x09 is not described. Tag-only, so nothing
    /// delimits the unknown entry and the two trailing bytes cannot be reached.
    const string TagOnly = "01003C090BB8";
    /// The same with a length byte after each tag, so the entry can be stepped over.
    const string Delimited = "0102003C09020BB8";

    static Dictionary<string, object?> Decode(string? mode, int lengthSize, string hex)
    {
        var yaml = "name: t\nendian: big\nfields:\n  - tlv:\n      tag_size: 1\n";
        if (mode != null) yaml += $"      unknown: {mode}\n";
        if (lengthSize > 0) yaml += "      length_size: 1\n";
        yaml += "      cases:\n        1:\n          - {name: known, type: u16}\n";
        return SchemaDecoder.Decode(SchemaParser.Parse(yaml), Convert.FromHexString(hex));
    }

    static List<string> Warnings(Dictionary<string, object?> result) =>
        result.TryGetValue("_warnings", out var value) && value is List<string> list
            ? list
            : new List<string>();

    [Fact]
    public void Stopping_short_is_reported()
    {
        // PS-301, PS-302. The fields before the tag are reported unchanged, which is why
        // this is a warning and not an error.
        var result = Decode("skip", 0, TagOnly);
        Assert.Equal(60L, Convert.ToInt64(result["known"]));
        Assert.Equal(
            new[] { "unknown TLV tag (0x09) at offset 3: 3 of 6 byte(s) left undecoded" },
            Warnings(result));
    }

    [Fact]
    public void Skip_is_what_happens_without_the_parameter()
    {
        Assert.Equal(Warnings(Decode(null, 0, TagOnly)), Warnings(Decode("skip", 0, TagOnly)));
    }

    [Fact]
    public void A_delimited_entry_is_stepped_over_and_reported()
    {
        var result = Decode("skip", 1, Delimited);
        Assert.Equal(60L, Convert.ToInt64(result["known"]));
        Assert.Equal(
            new[] { "unknown TLV tag (0x09) skipped, 2 byte(s) discarded" },
            Warnings(result));
    }

    [Fact]
    public void A_clean_decode_carries_no_warning_key()
    {
        Assert.False(Decode("skip", 0, "01003C").ContainsKey("_warnings"));
    }

    [Fact]
    public void Error_mode_fails_naming_the_tag()
    {
        var thrown = Assert.Throws<InvalidOperationException>(
            () => Decode("error", 0, TagOnly));
        Assert.Contains("0x09", thrown.Message);
    }

    [Fact]
    public void Raw_reports_its_entry_when_output_is_merged()
    {
        // PS-303. `merge` defaults to true, which is every schema that does not set it.
        var result = Decode("raw", 0, TagOnly);
        var entries = Assert.IsType<List<Dictionary<string, object?>>>(result["unknown_tags"]);
        Assert.Single(entries);
        Assert.Equal("0bb8", entries[0]["raw"]);
    }

    [Fact]
    public void A_delimited_raw_capture_takes_only_its_own_bytes()
    {
        var result = Decode("raw", 1, Delimited);
        var entries = Assert.IsType<List<Dictionary<string, object?>>>(result["unknown_tags"]);
        Assert.Single(entries);
        Assert.Equal("0bb8", entries[0]["raw"]);
        Assert.Empty(Warnings(result));
    }

    [Fact]
    public void The_warning_names_every_tag_component()
    {
        // The Milesight shape: channel then type. Both are needed to add the missing case.
        var yaml = "name: t\nendian: big\nfields:\n  - tlv:\n"
                 + "      tag_fields:\n        - {name: channel, type: u8}\n"
                 + "        - {name: kind, type: u8}\n"
                 + "      tag_key: [channel, kind]\n"
                 + "      cases:\n        \"[1, 117]\":\n"
                 + "          - {name: battery, type: u8}\n";
        var result = SchemaDecoder.Decode(
            SchemaParser.Parse(yaml), Convert.FromHexString("0175640569"));
        Assert.Contains("0x05, 0x69", Assert.Single(Warnings(result)));
    }
}
