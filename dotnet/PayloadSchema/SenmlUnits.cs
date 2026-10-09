// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;

namespace PayloadSchema;

/// <summary>
/// The IANA SenML Units registry (RFC 8428 section 12.1, RFC 8798), as updated 2026-02-02,
/// and the unit a SenML record carries for a field (CR-2026-094: PS-478 amended, PS-487,
/// PS-488). The same tables as <c>SENML_UNITS</c> and <c>SENML_SECONDARY_UNITS</c> in
/// tools/schema_interpreter.py.
/// </summary>
public static class SenmlUnits
{
    /// <summary>Every unit a SenML record's <c>u</c> may carry.</summary>
    public static readonly HashSet<string> Units = new()
    {
        "m", "kg", "g", "s", "A", "K", "cd", "mol", "Hz", "rad", "sr", "N", "Pa", "J", "W", "C",
        "V", "F", "Ohm", "S", "Wb", "T", "H", "Cel", "lm", "lx", "Bq", "Gy", "Sv", "kat", "m2",
        "m3", "l", "m/s", "m/s2", "m3/s", "l/s", "W/m2", "cd/m2", "bit", "bit/s", "lat", "lon",
        "pH", "dB", "dBW", "Bspl", "count", "/", "%", "%RH", "%EL", "EL", "1/s", "1/min",
        "beat/min", "beats", "S/m", "B", "VA", "VAs", "var", "vars", "J/m", "kg/m3", "deg", "NTU",
    };

    /// <summary>Secondary units: name to (primary unit, scale, offset), as the registry
    /// writes them.</summary>
    public static readonly Dictionary<string, (string Primary, string Scale, string Offset)> Secondary = new()
    {
        ["ms"] = ("s", "1/1000", "0"),
        ["min"] = ("s", "60", "0"),
        ["h"] = ("s", "3600", "0"),
        ["MHz"] = ("Hz", "1000000", "0"),
        ["kW"] = ("W", "1000", "0"),
        ["kVA"] = ("VA", "1000", "0"),
        ["kvar"] = ("var", "1000", "0"),
        ["Ah"] = ("C", "3600", "0"),
        ["Wh"] = ("J", "3600", "0"),
        ["kWh"] = ("J", "3600000", "0"),
        ["varh"] = ("vars", "3600", "0"),
        ["kvarh"] = ("vars", "3600000", "0"),
        ["kVAh"] = ("VAs", "3600000", "0"),
        ["Wh/km"] = ("J/m", "3.6", "0"),
        ["KiB"] = ("B", "1024", "0"),
        ["GB"] = ("B", "1e9", "0"),
        ["Mbit/s"] = ("bit/s", "1000000", "0"),
        ["B/s"] = ("bit/s", "8", "0"),
        ["MB/s"] = ("bit/s", "8000000", "0"),
        ["mV"] = ("V", "1/1000", "0"),
        ["mA"] = ("A", "1/1000", "0"),
        ["dBm"] = ("dBW", "1", "-30"),
        ["ug/m3"] = ("kg/m3", "1e-9", "0"),
        ["mm/h"] = ("m/s", "1/3600000", "0"),
        ["m/h"] = ("m/s", "1/3600", "0"),
        ["ppm"] = ("/", "1e-6", "0"),
        ["/100"] = ("/", "1/100", "0"),
        ["/1000"] = ("/", "1/1000", "0"),
        ["hPa"] = ("Pa", "100", "0"),
        ["mm"] = ("m", "1/1000", "0"),
        ["cm"] = ("m", "1/100", "0"),
        ["km"] = ("m", "1000", "0"),
        ["km/h"] = ("m/s", "1/3.6", "0"),
        ["ppb"] = ("/", "1e-9", "0"),
        ["ppt"] = ("/", "1e-12", "0"),
        ["VAh"] = ("VAs", "3600", "0"),
        ["mg/l"] = ("kg/m3", "1/1000", "0"),
        ["ug/l"] = ("kg/m3", "1e-6", "0"),
        ["g/l"] = ("kg/m3", "1", "0"),
    };

    /// <summary>
    /// The value and <c>u</c> of a SenML record for a field declared in <paramref name="unit"/>.
    /// A secondary unit is re-expressed in its primary unit by the registry's scale and
    /// offset, computed in decimal so 3284 mV is 3.284 V and not 3.2840000000000003 (PS-487).
    /// <c>%</c> is written by its preferred name <c>/</c>, which changes no value. A unit the
    /// registry does not have gives no <c>u</c> and is returned as <c>Unregistered</c> for
    /// the warning PS-488 requires. Only a number is converted.
    /// </summary>
    public static (object? Value, string? Unit, string? Unregistered) Resolve(object? value, string? unit)
    {
        if (string.IsNullOrEmpty(unit)) return (value, null, null);
        if (unit == "%") return (value, "/", null);
        if (Units.Contains(unit)) return (value, unit, null);
        if (!Secondary.TryGetValue(unit, out var entry)) return (value, null, unit);
        return (Convert(value, entry.Scale, entry.Offset), entry.Primary, null);
    }

    static object? Convert(object? value, string scale, string offset)
    {
        decimal number;
        switch (value)
        {
            case bool or string or null:
                return value;
            case double d when !double.IsFinite(d):
                return value;
            case double d:
                number = decimal.Parse(d.ToString("R", CultureInfo.InvariantCulture),
                    NumberStyles.Float, CultureInfo.InvariantCulture);
                break;
            case float f:
                number = decimal.Parse(((double)f).ToString("R", CultureInfo.InvariantCulture),
                    NumberStyles.Float, CultureInfo.InvariantCulture);
                break;
            case IConvertible c:
                number = c.ToDecimal(CultureInfo.InvariantCulture);
                break;
            default:
                return value;
        }
        var result = Multiply(number, scale) + Number(offset);
        // An integral result is reported as an integer, as the reference reports it.
        if (result == decimal.Truncate(result) && Math.Abs(result) <= long.MaxValue)
            return (long)result;
        return (double)result;
    }

    /// <summary>A scale as the registry writes it, <c>1/1000</c>, <c>1e9</c>, <c>3.6</c> or
    /// <c>1/3.6</c>, applied by multiplying and then dividing, so <c>1/1000</c> is exact.</summary>
    static decimal Multiply(decimal value, string scale)
    {
        var slash = scale.IndexOf('/');
        if (slash < 0) return value * Number(scale);
        return value * Number(scale[..slash]) / Number(scale[(slash + 1)..]);
    }

    static decimal Number(string text)
        => decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
}
