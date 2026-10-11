// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-131 (PS-070): a byte_group covers its widest member's base, <c>size</c> may
/// widen it and a narrower <c>size</c> is rejected at load; a member is a bit range, a
/// whole integer or a bool with <c>bit</c>, each read from the group's start. The
/// messages are the spec prototype's.
/// </summary>
public class CR2026_131Tests
{
    static PayloadSchemaDefinition Parse(string fields) =>
        SchemaParser.Parse("name: t\nendian: big\nfields:\n" + fields);

    static string Rejection(string fields) =>
        Assert.Throws<InvalidOperationException>(() => Parse(fields)).Message;

    [Fact]
    public void ASizeNarrowerThanAMemberIsRejected()
    {
        const string want = "byte_group declares size 5, narrower than its widest member's 8 bytes (PS-070)";
        Assert.Equal(want, Rejection("  - byte_group: {size: 5, fields: [{name: a, type: 'u64[0:3]'}]}\n"));
        Assert.Equal(want, Rejection("  - byte_group: [{name: a, type: 'u64[0:3]'}]\n    size: 5\n"));
    }

    [Fact]
    public void ANarrowSizeInATlvCaseIsRejected()
    {
        var message = Rejection("  - tlv:\n      tag_size: 1\n      cases:\n        1:\n"
            + "          - byte_group: {size: 1, fields: [{name: a, type: 'u16[0:3]'}]}\n");
        Assert.Contains("byte_group declares size 1, narrower than its widest member's 2 bytes (PS-070)", message);
    }

    [Fact]
    public void MemberKindsAreChecked()
    {
        Assert.Equal("byte_group members 'w' and 'f' overlap (PS-397)",
            Rejection("  - byte_group: [{name: w, type: u8}, {name: f, type: bool, bit: 0}]\n"));
        Assert.Equal("byte_group member 'f' is a bool at bit 9; a bool member is one bit of a u8, 0 to 7 (PS-070)",
            Rejection("  - byte_group: [{name: f, type: bool, bit: 9}]\n"));
        Assert.Equal("byte_group member 'f' is not a bit range, a whole integer or a bool with `bit` (PS-364)",
            Rejection("  - byte_group: [{name: f, type: bool}]\n"));
    }

    [Fact]
    public void AWholeIntegerReadsFromTheGroupsStart()
    {
        var schema = Parse("  - byte_group: {endian: little, fields: [{name: lo, type: u8}, {name: hi, type: 'u16[8:11]'}]}\n"
            + "  - {name: n, type: u8}\n");
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString("050307"));
        Assert.Equal(5.0, Convert.ToDouble(output["lo"]));
        Assert.Equal(3.0, Convert.ToDouble(output["hi"]));
        Assert.Equal(7.0, Convert.ToDouble(output["n"]));
        Assert.Equal(Convert.FromHexString("050307"), SchemaEncoder.Encode(schema, output).Payload);
    }

    [Fact]
    public void ASizelessGroupIsItsWidestBase()
    {
        var schema = Parse("  - byte_group: [{name: lo, type: 'u16[0:3]'}]\n  - {name: n, type: u8}\n");
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString("A00507"));
        Assert.Equal(5.0, Convert.ToDouble(output["lo"]));
        Assert.Equal(7.0, Convert.ToDouble(output["n"]));
    }
}
