using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-087: SenML record names and units (PS-478, PS-479), mirroring
/// tests/test_cr_2026_087_senml.py. ToSenML looked fields up in the top-level list and the
/// plain nested lists only, so a port entry's, a case's or a group's fields had no name or
/// unit, and an object was one record holding the whole mapping.
/// </summary>
public class CR2026_087Tests
{
    const string Environmental = @"name: environmental_sensor
version: 1
fields:
  - {name: temperature, type: s16, div: 10, unit: ""°C"", senml: {name: temp, unit: Cel}}
  - {name: humidity, type: u8, mult: 0.5, unit: ""%RH"", senml: {name: rh, unit: ""%RH""}}
  - {name: battery_voltage, type: u16, div: 1000, unit: V, senml: {name: vbat, unit: V}}
";

    const string Placements = @"name: senml_placements
ports:
  1:
    fields:
    - {name: kind, type: u8}
    - byte_group:
      - {name: hi, type: ""u8[4:7]"", unit: V, senml: {name: high, unit: V}}
      - {name: lo, type: ""u8[0:3]"", consume: 1, unit: A}
    - {name: _internal, type: u8}
    - match:
        field: $kind
        cases:
          1:
          - {name: case_temp, type: u8, unit: Cel}
    - {name: flags, type: u8}
    - flagged:
        field: flags
        groups:
        - bit: 0
          fields:
          - {name: fv, type: u8, unit: lx}
    - name: env
      type: object
      fields:
      - {name: t, type: u8, unit: Cel}
      - name: probe
        type: object
        fields:
        - {name: depth, type: u8, unit: m}
      - {name: p, type: u8, senml: {name: pressure, unit: Pa}}
    - {name: missing, type: u8, lookup: {9: nine}}
    - tlv:
        tag_size: 1
        cases:
          3:
          - {name: tlv_rh, type: u8, unit: ""%RH"", senml: {name: rh}}
";

    /// <summary>Decode on <paramref name="decodePort"/>, then format with
    /// <paramref name="fPort"/>, which may be null: every entry's fields are then searched.</summary>
    static List<(string n, string? u, string v)> Records(string yaml, string hex, int? decodePort,
        int? fPort)
    {
        var schema = SchemaParser.Parse(yaml);
        var decoded = decodePort.HasValue
            ? SchemaDecoder.DecodeWithPort(schema, Convert.FromHexString(hex), decodePort.Value)
            : SchemaDecoder.Decode(schema, Convert.FromHexString(hex));
        return SemanticFormatter.ToSenML(schema, decoded, fPort)
            .Select(r => ((string)r["n"]!, r.TryGetValue("u", out var u) ? (string?)u : null,
                Convert.ToString(r["v"], System.Globalization.CultureInfo.InvariantCulture)!))
            .ToList();
    }

    [Fact]
    public void Clause7ExampleIsNamedByItsSenmlBlock()
    {
        Assert.Equal(new List<(string, string?, string)>
        {
            ("temp", "Cel", "25.5"), ("rh", "%RH", "65"), ("vbat", "V", "3.276"),
        }, Records(Environmental, "00FF820CCC", null, null));
    }

    [Fact]
    public void WithoutTheBlockTheReportedNameAndUnitAreUsed()
    {
        var bare = System.Text.RegularExpressions.Regex.Replace(Environmental, @", senml: \{[^}]*\}", "");
        Assert.Equal(new[] { ("temperature", (string?)"°C"), ("humidity", "%RH"), ("battery_voltage", "V") },
            Records(bare, "00FF820CCC", null, null).Select(r => (r.n, r.u)).ToArray());
    }

    [Theory]
    [InlineData(1)]
    [InlineData(null)]
    public void EveryReportedFieldHasARecordWhereverDeclared(int? fPort)
    {
        Assert.Equal(new List<(string, string?, string)>
        {
            ("kind", null, "1"), ("high", "V", "5"), ("lo", "A", "2"), ("case_temp", "Cel", "20"),
            ("flags", null, "1"), ("fv", "lx", "7"), ("env/t", "Cel", "21"),
            ("env/probe/depth", "m", "2"), ("pressure", "Pa", "100"), ("rh", "%RH", "42"),
        }, Records(Placements, "0152FF14010715026401032A", 1, fPort));
    }
}
