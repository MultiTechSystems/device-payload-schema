// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// 0.5.2 wave 5, CR-2026-060: a lookup default may carry the value it could not map. The
/// fixture lookup-default-template.yaml holds the decode and round trip; these hold the
/// rejections, a value with a fraction, and the value's rendering.
/// </summary>
public class Wave5Tests
{
    const string Schema = "name: p\nfields:\n"
        + "  - {name: sensor, type: u8, lookup: {0: Battery Voltage, 1: AIN1, default: \"AnalogSensor${value}\"}}\n"
        + "  - {name: half, type: u8, div: 2, lookup: {1: one, default: \"v${value}/${value}\"}}\n";

    [Theory]
    [InlineData("0102", "AIN1", "one")]
    [InlineData("0705", "AnalogSensor7", "v2.5/2.5")]     // 2.5 is no key, not key 2
    [InlineData("FF02", "AnalogSensor255", "one")]
    public void DecodesAndRecovers(string hex, string sensor, string half)
    {
        var schema = SchemaParser.Parse(Schema);
        var payload = Convert.FromHexString(hex);
        var output = SchemaDecoder.Decode(schema, payload);
        Assert.Equal(sensor, output["sensor"]);
        Assert.Equal(half, output["half"]);
        var back = SchemaEncoder.Encode(schema, output!);
        Assert.Empty(back.Errors);
        Assert.Equal(payload, back.Payload);
    }

    [Fact]
    public void Rejections()
    {
        var schema = SchemaParser.Parse(Schema);
        Assert.NotEmpty(SchemaEncoder.Encode(schema,
            new Dictionary<string, object?> { ["sensor"] = "Sensor7", ["half"] = "one" }).Errors);
        Assert.NotEmpty(SchemaEncoder.Encode(schema,
            new Dictionary<string, object?> { ["sensor"] = "AIN1", ["half"] = "v2.5/3" }).Errors);
        Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(
            "name: p\nfields:\n  - {name: x, type: u8, lookup: {1: 10, default: \"x${value}\"}}\n"));
    }

    [Fact]
    public void LabelIsNotATemplate()
    {
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: x, type: u8, lookup: {1: \"a${value}\"}}\n");
        Assert.Equal("a${value}", SchemaDecoder.Decode(schema, new byte[] { 1 })["x"]);
    }

    [Theory]
    [InlineData(1, "100000", "x0.00001")]
    [InlineData(1, "10000000", "x1e-7")]
    [InlineData(15, "10000000", "x0.0000015")]
    [InlineData(1, "100000000", "x1e-8")]
    public void ValueRenderingMatchesJavaScript(int raw, string divisor, string want)
    {
        // raw / divisor, rendered as JavaScript's String(number) renders it.
        var schema = SchemaParser.Parse("name: p\nfields:\n  - {name: x, type: u8, div: " + divisor
            + ", lookup: {0: zero, default: \"x${value}\"}}\n");
        Assert.Equal(want, SchemaDecoder.Decode(schema, new[] { (byte)raw })["x"]);
    }
}
