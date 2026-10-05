// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>CR-2026-044: a ragged tail under `until: end` is an error naming the repeat (PS-343 to PS-344a).</summary>
public class CR2026_044Tests
{
    [Fact]
    public void ARaggedTailIsAnError()
    {
        var schema = SchemaParser.Parse("name: p\nfields:\n"
            + "- {name: r, type: repeat, until: end, fields: [{name: a, type: u8}, {name: b, type: u16}]}\n");
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaDecoder.Decode(schema, new byte[] { 1, 0, 2, 2 }));
        Assert.Contains("'r' ends in a ragged tail", thrown.Message);
        Assert.Contains("fewer than the 3", thrown.Message);
        var whole = SchemaDecoder.Decode(schema, new byte[] { 1, 0, 2, 2, 0, 3 });
        Assert.Equal(2, ((System.Collections.IList)whole["r"]!).Count);
    }
}
