// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// CR-2026-131: the bytes a byte_group covers and the members it admits (PS-070). The
/// load-time rules are <c>SchemaParser.CheckByteGroup</c>.
/// </summary>
public static class Cr131
{
    /// <summary>The whole-integer types a member may name: <c>u16</c> is <c>u16[0:15]</c>.</summary>
    public static readonly Dictionary<string, int> WholeIntegerBytes = new()
    {
        ["u8"] = 1, ["u16"] = 2, ["u24"] = 3, ["u32"] = 4, ["u64"] = 8,
        ["uint8"] = 1, ["uint16"] = 2, ["uint24"] = 3, ["uint32"] = 4, ["uint64"] = 8,
        ["s8"] = 1, ["s16"] = 2, ["s24"] = 3, ["s32"] = 4, ["s64"] = 8,
        ["i8"] = 1, ["i16"] = 2, ["i24"] = 3, ["i32"] = 4, ["i64"] = 8,
        ["int8"] = 1, ["int16"] = 2, ["int24"] = 3, ["int32"] = 4, ["int64"] = 8,
    };

    /// <summary>
    /// A parsed member's base bytes and bits of the group's assembled value. A bit range is
    /// itself, a whole integer its full range, a bool its <c>bit</c> of a u8. Every member
    /// reads from the group's start; none moves the position.
    /// </summary>
    public static (int baseBytes, int low, int high, bool signed) MemberRange(SchemaField member)
    {
        var bitRange = Helpers.ParseBitRange(member.RawType);
        if (bitRange != null)
            return (Math.Max(1, member.BitBaseBytes), bitRange.Value.start, bitRange.Value.end,
                member.SignedBits);
        if (member.Type == FieldType.Bool)
            return (1, member.Bit, member.Bit, false);
        if (WholeIntegerBytes.TryGetValue(member.RawType, out var whole))
        {
            var raw = member.RawType;
            return (whole, 0, whole * 8 - 1, raw.StartsWith("s") || raw.StartsWith("i"));
        }
        return (1, 0, 7, false);   // rejected at load; a field built in code reads one byte
    }

    /// <summary>
    /// With no <c>size</c>, a group covers its widest member's base type, whichever bytes
    /// that member's bits fall in; <c>size</c> may widen it, and a narrower one is rejected
    /// at load. Decoding read a group with no size as one byte, so a <c>u16[8:15]</c>
    /// member decoded from the wrong byte, while encoding already widened to the base.
    /// </summary>
    public static int ByteGroupWidth(SchemaField group)
    {
        int width = Math.Max(1, group.Size);
        foreach (var member in group.ByteGroup)
            width = Math.Max(width, MemberRange(member).baseBytes);
        return width;
    }
}
