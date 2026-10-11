// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// CR-2026-131: the bytes a byte_group covers (PS-070).
/// </summary>
public static class Cr131
{
    static readonly HashSet<FieldType> IntegerTypes = new()
    {
        FieldType.U8, FieldType.U16, FieldType.U24, FieldType.U32, FieldType.U64,
        FieldType.S8, FieldType.S16, FieldType.S24, FieldType.S32, FieldType.S64,
        FieldType.U32LE16, FieldType.S32LE16, FieldType.U32BE16LE, FieldType.S32BE16LE,
    };

    /// <summary>
    /// The bytes a member's base type spans: <c>u16[0:3]</c> and <c>u16</c> are 2. A bool
    /// member is one byte, and so is anything unrecognised.
    /// </summary>
    public static int MemberWidth(SchemaField member)
    {
        if (Helpers.ParseBitRange(member.RawType) != null)
            return Math.Max(1, member.BitBaseBytes);
        return IntegerTypes.Contains(member.Type) ? Helpers.InferLengthFromType(member.Type) : 1;
    }

    /// <summary>
    /// With no <c>size</c>, a group covers its widest member's base type, whichever bytes
    /// that member's bits fall in; <c>size</c> may widen it, never narrow it. Decoding read
    /// a group with no size as one byte, so a <c>u16[8:15]</c> member decoded from the wrong
    /// byte, while encoding already widened to the base.
    /// </summary>
    public static int ByteGroupWidth(SchemaField group)
    {
        int width = Math.Max(1, group.Size);
        foreach (var member in group.ByteGroup)
            width = Math.Max(width, MemberWidth(member));
        return width;
    }
}
