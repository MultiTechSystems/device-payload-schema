// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-094: SenML output takes primary units (PS-478 amended, PS-487, PS-488). A
/// secondary unit is re-expressed in its primary unit, in decimal; <c>%</c> is written
/// <c>/</c>; an unregistered unit is left out with a warning. Mirrors
/// tests/test_cr_2026_094_senml_units.py; the expected values are the specification
/// prototype's, from the CR.
/// </summary>
public class CR2026_094Tests
{
    [Theory]
    [InlineData(3284L, "mV", "3.284", "V")]
    [InlineData(412L, "ppm", "0.000412", "/")]
    [InlineData(1013.2, "hPa", "101320", "Pa")]
    [InlineData(-97L, "dBm", "-127", "dBW")]
    [InlineData(90L, "/100", "0.9", "/")]
    [InlineData(35L, "cm", "0.35", "m")]
    [InlineData(50L, "%", "50", "/")]
    [InlineData(21.5, "Cel", "21.5", "Cel")]
    [InlineData(36L, "km/h", "10", "m/s")]
    public void ARecordTakesThePrimaryUnit(object value, string unit, string expected, string primary)
    {
        var (got, u, unregistered) = SenmlUnits.Resolve(value, unit);
        Assert.Equal(primary, u);
        Assert.Null(unregistered);
        // In decimal: the exact re-expression, not 3.2840000000000003.
        Assert.Equal(expected, Convert.ToString(got, System.Globalization.CultureInfo.InvariantCulture));
    }

    [Fact]
    public void AnUnregisteredUnitIsLeftOut()
        => Assert.Equal((12.5, (string?)null, (string?)"kPa"), ((double)SenmlUnits.Resolve(12.5, "kPa").Value!,
            SenmlUnits.Resolve(12.5, "kPa").Unit, SenmlUnits.Resolve(12.5, "kPa").Unregistered));

    [Fact]
    public void TheTablesAreTheRegistry()
    {
        Assert.Equal(67, SenmlUnits.Units.Count);
        Assert.Equal(39, SenmlUnits.Secondary.Count);
        Assert.All(SenmlUnits.Secondary.Values, e => Assert.Contains(e.Primary, SenmlUnits.Units));
    }

    const string Schema = @"name: units
fields:
  - {name: battery, type: u16, unit: mV}
  - {name: pressure, type: u16, unit: kPa}
  - {name: rh, type: u8, unit: ""%"", senml: {unit: /100}}
  - name: probe
    type: object
    fields:
      - {name: depth, type: u8, unit: inch}
";

    [Fact]
    public void TheRecordsAndTheWarnings()
    {
        var schema = SchemaParser.Parse(Schema);
        var decoded = SchemaDecoder.Decode(schema, Convert.FromHexString("0CD400645A07"));
        var warnings = new List<string>();
        var records = SemanticFormatter.ToSenML(schema, decoded, null, warnings)
            .Select(r => ((string)r["n"]!, r.TryGetValue("u", out var u) ? (string?)u : null,
                Convert.ToString(r["v"], System.Globalization.CultureInfo.InvariantCulture)))
            .ToList();
        Assert.Equal(new List<(string, string?, string?)>
        {
            ("battery", "V", "3.284"), ("pressure", null, "100"), ("rh", "/", "0.9"), ("probe/depth", null, "7"),
        }, records);
        Assert.Equal(new List<string>
        {
            "pressure: unit 'kPa' is not a registered SenML unit, so its record has no 'u' (PS-488)",
            "probe/depth: unit 'inch' is not a registered SenML unit, so its record has no 'u' (PS-488)",
        }, warnings);
        // The decoded output keeps the author's values; only SenML is re-expressed.
        Assert.Equal(3284L, Convert.ToInt64(decoded["battery"]));
    }
}
