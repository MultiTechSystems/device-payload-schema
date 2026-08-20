// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema.Tests;

/// <summary>
/// Numeric comparison for decoded values, whatever channel they came through.
///
/// These tests used to assert <c>Assert.Equal(500.0, result["v"])</c>, which pins the
/// representation rather than the value: since CR-2026-011 an integer-typed field carrying
/// no modifier is reported as <c>ulong</c> or <c>long</c> so that a u64 above 2^53 survives
/// (PS-293, PS-294), and a boxed ulong never equals a boxed double. Tests that care about
/// the channel assert the type explicitly; these care about the number.
/// </summary>
internal static class Decoded
{
    public static double Num(object? value) => value switch
    {
        double d => d,
        float f => f,
        ulong u => u,
        long l => l,
        int i => i,
        _ => throw new InvalidOperationException($"not a number: {value} ({value?.GetType().Name})"),
    };
}
