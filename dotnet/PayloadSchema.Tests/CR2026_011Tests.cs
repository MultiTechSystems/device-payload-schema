// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// PS-279, PS-293 to PS-295 (CR-2026-011): integer typing and exactness.
///
/// This decoder cast every integer to double at the read site, so a u64 of 2^64-1 came
/// back as 1.8446744073709552E+19; and s64 minimum wrapped to s64 maximum, because C#
/// masks a shift count to the operand's width and <c>1L &lt;&lt; 64</c> is <c>1L &lt;&lt; 0</c>.
/// </summary>
public class CR2026_011Tests
{
    static object? Decode(string declared, string hex)
    {
        var schema = SchemaParser.Parse(
            $"name: t\nendian: big\nfields:\n  - {{name: v, {declared}}}\n");
        return SchemaDecoder.Decode(schema, Convert.FromHexString(hex))["v"];
    }

    [Theory]
    [InlineData("u8", "01")]
    [InlineData("u16", "003C")]
    [InlineData("u32", "0000003C")]
    [InlineData("u64", "000000000000003C")]
    public void An_integer_width_reports_through_an_integer_channel(string type, string hex)
    {
        // PS-293.
        var value = Decode($"type: {type}", hex);
        Assert.True(value is ulong or long, $"{type} reported as {value?.GetType().Name}");
    }

    [Fact]
    public void A_modifier_makes_the_field_a_number()
    {
        // PS-279.
        Assert.Equal(23.5, Assert.IsType<double>(Decode("type: s16, div: 10", "00EB")));
    }

    [Fact]
    public void A_u64_is_exact_at_the_top_of_its_range()
    {
        // PS-294, and PS-295's prohibition on a sign-changed value.
        Assert.Equal(ulong.MaxValue, Assert.IsType<ulong>(Decode("type: u64", "FFFFFFFFFFFFFFFF")));
    }

    [Fact]
    public void A_u64_just_above_the_double_range_is_exact()
    {
        Assert.Equal(9007199254740993UL, Assert.IsType<ulong>(Decode("type: u64", "0020000000000001")));
    }

    [Fact]
    public void An_s64_at_the_bottom_of_its_range_is_exact()
    {
        Assert.Equal(long.MinValue, Assert.IsType<long>(Decode("type: s64", "8000000000000000")));
    }

    [Fact]
    public void A_signed_width_below_64_bits_still_sign_extends()
    {
        Assert.Equal(-1L, Assert.IsType<long>(Decode("type: s16", "FFFF")));
        Assert.Equal(int.MinValue, Assert.IsType<long>(Decode("type: s32", "80000000")));
    }
}
