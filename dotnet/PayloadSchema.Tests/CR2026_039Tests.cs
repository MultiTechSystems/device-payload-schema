// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-039: byte order is declared with `endian`, never in a type name. C# read the
/// le_/be_ prefixes as a field endian; they are withdrawn and rejected (PS-053a).
/// </summary>
public class CR2026_039Tests
{
    [Theory]
    [InlineData("le_u16")]
    [InlineData("be_u16")]
    [InlineData("le_f32")]
    [InlineData("le_u8[0:3]")]
    public void APrefixedTypeIsRejected(string type)
    {
        Assert.Throws<InvalidOperationException>(
            () => SchemaParser.Parse($"name: p\nfields:\n  - name: v\n    type: '{type}'\n"));
    }
}
