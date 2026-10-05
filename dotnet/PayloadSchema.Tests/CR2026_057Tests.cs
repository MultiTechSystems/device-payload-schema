// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-057: a zero divisor (PS-100) and the log of a non-positive number (PS-117)
/// leave the field absent, never NaN (PS-282), and the later fields still decode.
/// </summary>
public class CR2026_057Tests
{
    [Theory]
    [InlineData("{name: v, type: u8, div: 0}", "07")]
    [InlineData("{name: v, type: u8, transform: [{div: 0}]}", "07")]
    [InlineData("{name: v, type: u8, transform: [{log10: true}]}", "00")]
    [InlineData("{name: v, type: s8, transform: [{log: true}]}", "FF")]
    [InlineData("{name: v, type: u8, transform: [{log: true}], lookup: {0: zero}}", "00")]
    public void ArithmeticWithNoValueOmitsTheField(string field, string raw)
    {
        var schema = SchemaParser.Parse($"name: p\nfields:\n- {field}\n- {{name: w, type: u8}}\n");
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString(raw + "05"));
        Assert.False(output.ContainsKey("v"), $"v = {(output.TryGetValue("v", out var v) ? v : null)}");
        Assert.Equal(5.0, Convert.ToDouble(output["w"]));
    }
}
