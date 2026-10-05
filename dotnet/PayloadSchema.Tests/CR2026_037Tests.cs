// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-037: the type vocabulary is closed. An unknown or absent type is rejected when
/// the schema is loaded (PS-327, PS-328, PS-334); the PS-049 alias table is exhaustive
/// (PS-326) and case-sensitive (PS-333); udec and sdec are required (PS-329, PS-330).
/// </summary>
public class CR2026_037Tests
{
    static string One(string type) => $"name: probe\nfields:\n  - name: v\n    type: {type}\n";

    [Theory]
    [InlineData("float")]
    [InlineData("double")]
    [InlineData("UDec")]
    [InlineData("U8")]
    [InlineData("byte")]
    [InlineData("uint")]
    [InlineData("float16")]
    [InlineData("switch")]
    [InlineData("ctrl-switch")]
    [InlineData("version_string")]
    [InlineData("hex:upper")]
    [InlineData("tlv")]
    public void AnUndefinedSpellingIsRejectedNamingFieldAndType(string type)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(One(type)));
        Assert.Contains("'v'", thrown.Message);
        Assert.Contains(type, thrown.Message);
    }

    [Fact]
    public void AFieldWithNoTypeAndNoConstructIsRejected()
    {
        var thrown = Assert.Throws<InvalidOperationException>(
            () => SchemaParser.Parse("name: probe\nfields:\n  - name: v\n"));
        Assert.Contains("declares no type", thrown.Message);
    }

    [Theory]
    [InlineData("uint8", new byte[] { 0x07 }, 7.0)]
    [InlineData("uint64", new byte[] { 0, 0, 0, 0, 0, 0, 0, 9 }, 9.0)]
    [InlineData("int8", new byte[] { 0xFE }, -2.0)]
    [InlineData("i24", new byte[] { 0xFF, 0xFF, 0xFB }, -5.0)]
    [InlineData("int64", new byte[] { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xF6 }, -10.0)]
    [InlineData("udec", new byte[] { 0x25 }, 2.5)]
    [InlineData("sdec", new byte[] { 0xE5 }, -1.5)]
    public void EveryAliasAndTheNibbleDecimalsDecode(string type, byte[] payload, double expected)
    {
        var decoded = SchemaDecoder.Decode(SchemaParser.Parse(One(type)), payload);
        Assert.Equal(expected, Convert.ToDouble(decoded["v"]));
    }

    [Theory]
    [InlineData("udec", 2.5, 0x25)]
    [InlineData("sdec", -1.5, 0xE5)]
    [InlineData("sdec", 7.9, 0x79)]
    public void TheNibbleDecimalsEncode(string type, double value, byte expected)
    {
        var result = SchemaEncoder.Encode(SchemaParser.Parse(One(type)),
            new Dictionary<string, object?> { ["v"] = value });
        Assert.Empty(result.Errors);
        Assert.Equal(new[] { expected }, result.Payload);
    }

    const string Integer = @"
name: probe
fields:
  - name: a
    type: u8
  - name: half
    type: integer
    compute: {op: div, a: $a, b: 2}
";

    [Fact]
    public void AnIntegerComputedFieldReportsAnInteger()
    {
        var decoded = SchemaDecoder.Decode(SchemaParser.Parse(Integer), new byte[] { 8 });
        Assert.IsType<long>(decoded["half"]);
        Assert.Equal(4L, decoded["half"]);
    }

    [Fact]
    public void AFractionalIntegerIsAnErrorNotARounding()
    {
        Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.Decode(SchemaParser.Parse(Integer), new byte[] { 7 }));
    }

    [Fact]
    public void TheVocabularyFixtureRoundTrips()
    {
        var dir = AppContext.BaseDirectory;
        while (dir != null && !Directory.Exists(Path.Combine(dir, "schemas", "devices")))
            dir = Path.GetDirectoryName(dir);
        Assert.NotNull(dir);
        var path = Path.Combine(dir!, "schemas", "devices", "_language-conformance", "type-vocabulary.yaml");
        var schema = SchemaParser.Parse(File.ReadAllText(path));
        var payload = Convert.FromHexString(
            "010203040506" + "0708090A" + "0000000000000001" + "FFFEFFFDFFFCFFFFFBFFFFFA"
            + "FFFFFFF9FFFFFFF8" + "FFFFFFFFFFFFFFF7FFFFFFFFFFFFFFF6FFFFFFFFFFFFFFF5"
            + "3C0025E5414243");
        var decoded = SchemaDecoder.Decode(schema, payload);
        var encoded = SchemaEncoder.Encode(schema, decoded);
        Assert.Empty(encoded.Errors);
        Assert.Equal(Convert.ToHexString(payload), Convert.ToHexString(encoded.Payload));
    }
}
