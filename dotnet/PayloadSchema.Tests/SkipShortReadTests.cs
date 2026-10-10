// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// A skip that runs past the end of the payload is a short read like any other. Python and
/// the generated codec decoded it with success; this pins that C# does not.
/// </summary>
public class SkipShortReadTests
{
    [Fact]
    public void ASkipPastTheEndIsAnError()
    {
        var schema = SchemaParser.Parse(
            "name: p\nfields:\n- {name: a, type: u8}\n- {name: pad, type: skip, length: 4}\n");
        Assert.ThrowsAny<Exception>(() => SchemaDecoder.Decode(schema, new byte[] { 1, 2 }));
    }
}
