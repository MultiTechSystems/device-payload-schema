// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-038, -041, -042: a ports schema decoded with no FPort is an error (PS-459); a
/// port key is 1 to 255 (PS-018); a top-level fPort is a port, every ports key beside it
/// equals it, and it is never consulted when decoding (PS-335 to PS-337).
/// </summary>
public class CR2026_038Tests
{
    const string Ported =
        "name: p\nports:\n  1:\n    fields: [{name: a, type: u8}]\n  default:\n    fields: [{name: b, type: u8}]\n";

    [Fact]
    public void NoFPortIsAnError()
    {
        var schema = SchemaParser.Parse(Ported);
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaDecoder.Decode(schema, new byte[] { 7 }));
        Assert.Contains("no FPort was supplied", thrown.Message);
    }

    [Theory]
    [InlineData("name: p\nports:\n  0:\n    fields: [{name: a, type: u8}]\n")]
    [InlineData("name: p\nports:\n  256:\n    fields: [{name: a, type: u8}]\n")]
    [InlineData("name: p\nfPort: 0\nfields: [{name: a, type: u8}]\n")]
    [InlineData("name: p\nfPort: 20\nports:\n  21:\n    fields: [{name: a, type: u8}]\n")]
    public void PortDeclarationsAreChecked(string yaml)
    {
        Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(yaml));
    }

    [Fact]
    public void TheTopLevelFPortIsNotConsulted()
    {
        var schema = SchemaParser.Parse("name: p\nfport: 225\nfields: [{name: a, type: u8}]\n");
        Assert.Equal(5.0, Convert.ToDouble(SchemaDecoder.Decode(schema, new byte[] { 5 })["a"]));
    }
}
