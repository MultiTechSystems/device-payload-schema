// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>A field's `time` mapping (PS-610 to PS-614), as parsed once the load check passed.</summary>
public sealed class TimeSpec
{
    public string Format { get; set; } = "";
    public string? Epoch { get; set; }
    public string? Since { get; set; }
    public bool Milliseconds { get; set; }
    public int? Fraction { get; set; }
    public List<string>? MonthLabels { get; set; }
    /// <summary>Part name to member name (PS-412); null where none is declared.</summary>
    public Dictionary<string, string>? Keys { get; set; }
    public string? Pattern { get; set; }
    /// <summary>Part name to the name it references, `$` removed (PS-614), in TimeFields.Parts order.</summary>
    public Dictionary<string, string>? Parts { get; set; }
}

/// <summary>
/// CR-2026-186 (PS-610 to PS-615): a numeric field may declare `time`, and is then reported
/// as an instant in UTC in its format, in place of its number. The value after the field's
/// modifiers counts seconds from `epoch` (1970-01-01T00:00:00Z where absent); a reference
/// still binds the number. Instants are exact decimals, so a fractional value does not
/// drift; a millisecond count, `fraction` and %L truncate. Mirrors the CR-2026-186 block of
/// tools/schema_interpreter.py (time_field_error, format_time, time_from_parts,
/// time_readback_error, parse_time, time_input_seconds, expand_time_parts).
/// </summary>
internal static class TimeFields
{
    static readonly Dictionary<string, string[]> Formats = new()
    {
        ["unix"] = new[] { "since", "milliseconds" },
        ["iso8601"] = new[] { "fraction" },
        ["calendar"] = new[] { "month_labels", "keys" },
        ["pattern"] = new[] { "pattern" },
    };
    static readonly string[] Common = { "format", "epoch", "parts" };
    internal static readonly string[] Parts = { "year", "month", "day", "hour", "minute", "second" };
    static readonly string[] MonthAbbr =
        { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    static readonly string[] DayAbbr = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" };
    static readonly Regex Rfc3339 = new(
        @"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?(Z|\+00:00)$",
        RegexOptions.CultureInvariant);
    static readonly Regex EpochText = new(
        @"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$", RegexOptions.CultureInvariant);
    static readonly Regex PatternToken = new("%(.)", RegexOptions.Singleline | RegexOptions.CultureInvariant);
    static readonly Regex BitRange = new(@"^[us]\d+\[\d+:\d+\]$", RegexOptions.CultureInvariant);
    /// <summary>The numeric types a `time` may sit on (PS-610), besides the bit ranges.</summary>
    static readonly HashSet<FieldType> NumericTypes = new()
    {
        FieldType.U8, FieldType.U16, FieldType.U24, FieldType.U32, FieldType.U64,
        FieldType.U32LE16, FieldType.S32LE16, FieldType.U32BE16LE, FieldType.S32BE16LE,
        FieldType.S8, FieldType.S16, FieldType.S24, FieldType.S32, FieldType.S64,
        FieldType.F16, FieldType.BF16, FieldType.F32, FieldType.F64, FieldType.F32LE16,
        FieldType.F32BE16LE, FieldType.UFlt16, FieldType.SFlt16, FieldType.SFlt24,
        FieldType.UDec, FieldType.SDec, FieldType.Number, FieldType.Integer,
    };
    /// <summary>The types of a part field that reads no bytes (PS-615).</summary>
    static readonly string[] UnwrittenPartTypes = { "number", "integer", "string", "boolean" };

    // --- civil dates (proleptic Gregorian, Howard Hinnant's algorithms) -----------------

    static long FloorDiv(long a, long b)
    {
        long q = a / b;
        if (a % b != 0 && (a < 0) != (b < 0)) q--;
        return q;
    }

    static long FloorMod(long a, long b) => a - FloorDiv(a, b) * b;

    static long DaysFromCivil(long y, long m, long d)
    {
        if (m <= 2) y -= 1;
        long era = FloorDiv(y >= 0 ? y : y - 399, 400);
        long yoe = y - era * 400;
        long doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
        long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
    }

    static (long y, long m, long d) CivilFromDays(long z)
    {
        z += 719468;
        long era = FloorDiv(z >= 0 ? z : z - 146096, 146097);
        long doe = z - era * 146097;
        long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        long mp = (5 * doy + 2) / 153;
        long d = doy - (153 * mp + 2) / 5 + 1;
        long m = mp + (mp < 10 ? 3 : -9);
        return (yoe + era * 400 + (m <= 2 ? 1 : 0), m, d);
    }

    static bool ValidCivil(long y, long mo, long d, long h = 0, long mi = 0, long s = 0)
    {
        if (!(1 <= y && y <= 9999 && 1 <= mo && mo <= 12 && 0 <= h && h <= 23 && 0 <= mi && mi <= 59
              && 0 <= s && s <= 59))
            return false;
        return 1 <= d && d <= DateTime.DaysInMonth((int)y, (int)mo);
    }

    static long CivilSeconds(long y, long mo, long d, long h = 0, long mi = 0, long s = 0) =>
        DaysFromCivil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s;

    /// <summary>An `epoch` or `since`: an RFC 3339 date-time in UTC with a Z designator, as
    /// seconds since 1970 (PS-355); null where it is not one.</summary>
    static decimal? Instant(object? text) =>
        text is string s && EpochText.IsMatch(s) ? ParseRfc3339(s) : null;

    static decimal? ParseRfc3339(object? text)
    {
        if (text is not string s) return null;
        var m = Rfc3339.Match(s);
        if (!m.Success) return null;
        var p = Enumerable.Range(1, 6).Select(i => long.Parse(m.Groups[i].Value, CultureInfo.InvariantCulture)).ToArray();
        if (!ValidCivil(p[0], p[1], p[2], p[3], p[4], p[5])) return null;
        decimal frac = m.Groups[7].Success
            ? decimal.Parse("0" + m.Groups[7].Value, NumberStyles.Float, CultureInfo.InvariantCulture) : 0m;
        return CivilSeconds(p[0], p[1], p[2], p[3], p[4], p[5]) + frac;
    }

    // --- load (PS-610 to PS-614) -------------------------------------------------------

    static object? Value(YamlMappingNode map, string key) =>
        map.Children.TryGetValue(new YamlScalarNode(key), out var v) ? Meta.FromYaml(v) : null;

    static bool Has(YamlMappingNode map, string key) => map.Children.ContainsKey(new YamlScalarNode(key));

    static bool IsInteger(object? v) => v is long or int or ulong;

    static bool Numeric(string type) =>
        BitRange.IsMatch(type) || NumericTypes.Contains(Helpers.ParseFieldType(type));

    static bool Computes(YamlMappingNode field) =>
        new[] { "value", "compute", "ref", "polynomial" }.Any(k => Has(field, k));

    /// <summary>PS-610 to PS-614 for one field declaring `time`, in the spec prototype's text and
    /// order (check_time_fields); null where it is valid.</summary>
    internal static string? FieldError(YamlMappingNode field)
    {
        var name = Value(field, "name");
        var where = $"Field '{name}'";
        var specNode = field.Children[new YamlScalarNode("time")];
        var spec = specNode as YamlMappingNode;
        var format = spec != null ? Value(spec, "format") as string : null;
        if (spec == null || format == null || !Formats.ContainsKey(format))
            return $"{where}: `time` declares a format of unix, iso8601, calendar or pattern (PS-610)";
        var extra = spec.Children.Keys.Select(k => ((YamlScalarNode)k).Value ?? "")
            .Where(k => !Common.Contains(k) && !Formats[format].Contains(k))
            .OrderBy(k => k, StringComparer.Ordinal).ToList();
        if (extra.Count > 0)
            return $"{where}: {string.Join(", ", extra)} does not belong to the {format} format (PS-610)";
        var type = Value(field, "type");
        if (type is not string typeText || !Numeric(typeText) || Has(field, "lookup"))
            return $"{where}: `time` is declared on a numeric field, not {Meta.PyRepr(type)} (PS-610)";
        foreach (var key in new[] { "epoch", "since" })
            if (Has(spec, key) && Instant(Value(spec, key)) == null)
                return $"{where}: `{key}` {Meta.PyRepr(Value(spec, key))} is not an RFC 3339 date-time "
                    + "in UTC with a Z designator (PS-355, PS-610)";
        if (Has(spec, "milliseconds") && Value(spec, "milliseconds") is not bool)
            return $"{where}: `milliseconds` is true or false (PS-611)";
        var fraction = Value(spec, "fraction");
        if (fraction != null && !(IsInteger(fraction) && Convert.ToDecimal(fraction) is >= 1 and <= 9))
            return $"{where}: `fraction` is an integer from 1 to 9 (PS-611)";
        var labels = Value(spec, "month_labels");
        if (labels != null && !(labels is List<object?> list && list.Count == 12 && list.All(x => x is string)))
            return $"{where}: month_labels are exactly twelve strings (PS-411, PS-612)";
        var keys = Value(spec, "keys");
        if (keys != null)
        {
            if (keys is not Dictionary<string, object?> km || km.Keys.Any(k => !Parts.Contains(k))
                || !km.Values.All(v => v is string s && s.Length > 0))
                return $"{where}: keys map part names to member names (PS-412)";
            var names = Parts.Select(p => km.TryGetValue(p, out var v) ? (string)v! : p).ToList();
            if (names.Distinct().Count() != names.Count)
                return $"{where}: keys map two parts to one name (PS-412)";
        }
        if (format == "pattern")
        {
            if (Value(spec, "pattern") is not string pattern)
                return $"{where}: a pattern format declares `pattern` (PS-613)";
            var tokens = PatternToken.Matches(pattern).Select(m => m.Groups[1].Value).ToList();
            foreach (var token in tokens)
                if (!"YmdHMSLba%".Contains(token))
                    return $"{where}: %{token} is not a pattern directive (PS-613)";
            int percents = tokens.Count(t => t == "%");
            if (pattern.Count(c => c == '%') != 2 * percents + tokens.Count - percents)
                return $"{where}: a pattern ends with a lone % (PS-613)";
        }
        if (Has(spec, "parts"))
        {
            var parts = Value(spec, "parts");
            if (type as string != "number" || Computes(field))
                return $"{where}: `parts` belongs to a `type: number` field that computes nothing else (PS-614)";
            if (parts is not Dictionary<string, object?> pm || pm.Keys.Any(k => !Parts.Contains(k))
                || !new[] { "year", "month", "day" }.All(pm.ContainsKey)
                || !pm.Values.All(v => v is string s && s.StartsWith('$')))
                return $"{where}: `parts` maps year, month and day, and optionally hour, minute and "
                    + "second, to $references (PS-614)";
            if (Has(spec, "epoch"))
                return $"{where}: `epoch` does not apply to `parts` (PS-614)";
        }
        else if (type as string == "number" && !Computes(field))
            return $"{where}: a computed `time` field declares `parts` or a computation (PS-614)";
        return null;
    }

    /// <summary>The field's `time`, checked (PS-610 to PS-614), or null where it declares none.
    /// Throws the rule it breaks.</summary>
    internal static TimeSpec? Parse(YamlMappingNode field)
    {
        if (!Has(field, "time") || !Has(field, "type")) return null;
        if (FieldError(field) is { } error) throw new InvalidOperationException(error);
        var spec = (YamlMappingNode)field.Children[new YamlScalarNode("time")];
        var t = new TimeSpec
        {
            Format = (string)Value(spec, "format")!,
            Epoch = Value(spec, "epoch") as string,
            Since = Value(spec, "since") as string,
            Milliseconds = Value(spec, "milliseconds") is true,
            Fraction = Value(spec, "fraction") is { } f ? Convert.ToInt32(f, CultureInfo.InvariantCulture) : null,
            MonthLabels = (Value(spec, "month_labels") as List<object?>)?.Cast<string>().ToList(),
            Keys = (Value(spec, "keys") as Dictionary<string, object?>)?.ToDictionary(kv => kv.Key, kv => (string)kv.Value!),
            Pattern = Value(spec, "pattern") as string,
        };
        if (Value(spec, "parts") is Dictionary<string, object?> parts)
        {
            t.Parts = new Dictionary<string, string>();
            foreach (var p in Parts)
                if (parts.TryGetValue(p, out var r)) t.Parts[p] = ((string)r!)[1..];
        }
        return t;
    }

    // --- decode -------------------------------------------------------------------------

    /// <summary>A decoded or given number as an exact decimal: a double by its shortest
    /// round-trip text. Null where it is no number.</summary>
    static decimal? ToDecimal(object? value)
    {
        switch (value)
        {
            case bool or null or string: return null;
            case decimal m: return m;
            case double d:
                if (!double.IsFinite(d)) return null;
                return decimal.Parse(d.ToString("R", CultureInfo.InvariantCulture), NumberStyles.Float,
                    CultureInfo.InvariantCulture);
            case float f:
                return ToDecimal((double)f);
            case long or int or ulong or uint or short or ushort or byte or sbyte:
                return Convert.ToDecimal(value, CultureInfo.InvariantCulture);
            default: return null;
        }
    }

    static long Floor(decimal value) => (long)decimal.Floor(value);

    /// <summary>`count` fractional digits, truncated.</summary>
    static string FractionDigits(decimal fraction, int count)
    {
        decimal scale = 1m;
        for (int i = 0; i < count; i++) scale *= 10m;
        return Floor(fraction * scale).ToString(CultureInfo.InvariantCulture).PadLeft(count, '0');
    }

    static object JsNumber(decimal value) =>
        value == decimal.Truncate(value) ? (object)(long)value : (double)value;

    static decimal EpochOf(TimeSpec spec) => spec.Epoch != null ? Instant(spec.Epoch)!.Value : 0m;

    /// <summary>PS-610 to PS-613: the instant <paramref name="seconds"/> after the field's
    /// epoch, in its format. A value that is no number is returned as it is.</summary>
    internal static object? Format(SchemaField field, object? seconds)
    {
        var spec = field.Time!;
        if (ToDecimal(seconds) is not { } count0) return seconds;
        decimal instant = EpochOf(spec) + count0;
        var format = spec.Format;
        if (format == "unix")
        {
            var count = instant - (spec.Since != null ? Instant(spec.Since)!.Value : 0m);
            if (spec.Milliseconds) return Floor(count * 1000m);
            return JsNumber(count);
        }
        long whole = Floor(instant);
        decimal fraction = instant - whole;
        long days = FloorDiv(whole, 86400), rest = FloorMod(whole, 86400);
        var (year, month, day) = CivilFromDays(days);
        if (!(1 <= year && year <= 9999))
            throw new FieldErrorException($"Field '{field.Name}': the time "
                + $"{instant.ToString(CultureInfo.InvariantCulture)} s is outside years 1 to 9999 (PS-610)");
        long hour = rest / 3600, minute = rest / 60 % 60, second = rest % 60;
        if (format == "iso8601")
        {
            var text = $"{year:D4}-{month:D2}-{day:D2}T{hour:D2}:{minute:D2}:{second:D2}";
            if (spec.Fraction is { } digits)
                text += "." + FractionDigits(fraction, digits);
            else if (fraction != 0)
                text += "." + fraction.ToString("0.############################", CultureInfo.InvariantCulture)
                    .Split('.')[1];
            return text + "Z";
        }
        if (format == "calendar")
        {
            var values = new Dictionary<string, object?>
            {
                ["year"] = year,
                ["month"] = spec.MonthLabels != null ? spec.MonthLabels[(int)month - 1] : month,
                ["day"] = day, ["hour"] = hour, ["minute"] = minute, ["second"] = second,
            };
            var output = new Dictionary<string, object?>();
            foreach (var part in Parts)
                output[spec.Keys != null && spec.Keys.TryGetValue(part, out var k) ? k : part] = values[part];
            return output;
        }
        var outText = new Dictionary<string, string>
        {
            ["Y"] = year.ToString("D4", CultureInfo.InvariantCulture),
            ["m"] = month.ToString("D2", CultureInfo.InvariantCulture),
            ["d"] = day.ToString("D2", CultureInfo.InvariantCulture),
            ["H"] = hour.ToString("D2", CultureInfo.InvariantCulture),
            ["M"] = minute.ToString("D2", CultureInfo.InvariantCulture),
            ["S"] = second.ToString("D2", CultureInfo.InvariantCulture),
            ["L"] = FractionDigits(fraction, 3),
            ["b"] = MonthAbbr[month - 1],
            ["a"] = DayAbbr[FloorMod(days + 3, 7)],
            ["%"] = "%",
        };
        return PatternToken.Replace(spec.Pattern!, m => outText[m.Groups[1].Value]);
    }

    /// <summary>PS-614: the seconds since 1970 of the UTC instant the referenced parts name,
    /// or null where a part has no value (PS-314). A part that is not a whole number, and
    /// parts that name no valid date and time, are errors naming the field.</summary>
    internal static object? FromParts(SchemaField field, DecodeContext ctx)
    {
        var values = new List<(string part, long value)>();
        foreach (var part in Parts)
        {
            if (!field.Time!.Parts!.TryGetValue(part, out var key)) continue;
            if (!ctx.Variables.TryGetValue(key, out var value) || value == null
                || ReferenceEquals(value, SchemaDecoder.Omitted) || value is bool
                || Helpers.ToFloat64(value) is not (true, var number))
                return null;
            if (number != Math.Floor(number) || !double.IsFinite(number))
                throw new FieldErrorException($"Field '{field.Name}': its {part} is "
                    + $"{Meta.PyRepr(number)}, not a whole number (PS-614)");
            values.Add((part, (long)number));
        }
        long Get(string p) => values.FirstOrDefault(v => v.part == p).value;
        if (!ValidCivil(Get("year"), Get("month"), Get("day"), Get("hour"), Get("minute"), Get("second")))
        {
            var shown = string.Join(", ", values.Select(v => $"'{v.part}': {v.value}"));
            throw new FieldErrorException($"Field '{field.Name}': its parts {{{shown}}} do not name a "
                + "valid UTC date and time (PS-614)");
        }
        return CivilSeconds(Get("year"), Get("month"), Get("day"), Get("hour"), Get("minute"), Get("second"));
    }

    // --- encode (PS-615) ----------------------------------------------------------------

    /// <summary>PS-615: a pattern that cannot be parsed back to an instant decodes but is not
    /// encoded; null where it can be.</summary>
    internal static string? ReadbackError(SchemaField field)
    {
        var spec = field.Time!;
        if (spec.Format != "pattern") return null;
        var tokens = PatternToken.Matches(spec.Pattern!).Select(m => m.Groups[1].Value).ToHashSet();
        if (new[] { "Y", "d", "H", "M", "S" }.All(tokens.Contains) && (tokens.Contains("m") || tokens.Contains("b")))
            return null;
        return $"Field '{field.Name}': its pattern {Meta.PyRepr(spec.Pattern)} cannot be read back to an "
            + "instant; the schema decodes but is not encoded (PS-615)";
    }

    /// <summary>PS-615: a time field's value in its format, as seconds since 1970 (UTC).</summary>
    static decimal ParseTime(SchemaField field, object? value)
    {
        var spec = field.Time!;
        var format = spec.Format;
        FieldErrorException Bad(string why = "") => new($"Field '{field.Name}': {Meta.PyRepr(value)} "
            + $"is not a time in its {format} format{why} (PS-615)");
        decimal At(long y, long mo, long d, long h, long mi, long s, decimal frac)
        {
            if (!ValidCivil(y, mo, d, h, mi, s)) throw Bad();
            return CivilSeconds(y, mo, d, h, mi, s) + frac;
        }

        if (format == "unix")
        {
            if (ToDecimal(value) is not { } count) throw Bad();
            if (spec.Milliseconds) count /= 1000m;
            return count + (spec.Since != null ? Instant(spec.Since)!.Value : 0m);
        }
        if (format == "iso8601")
            return ParseRfc3339(value) ?? throw Bad();
        if (format == "calendar")
        {
            if (value is not Dictionary<string, object?> map) throw Bad();
            var got = new Dictionary<string, object?>();
            foreach (var part in Parts)
                got[part] = map.GetValueOrDefault(spec.Keys != null && spec.Keys.TryGetValue(part, out var k) ? k : part);
            if (spec.MonthLabels is { Count: > 0 } labels)
            {
                var index = got["month"] is string label ? labels.IndexOf(label) : -1;
                if (index < 0) throw Bad($"; month {Meta.PyRepr(got["month"])} is not a label");
                got["month"] = (long)(index + 1);
            }
            // A whole number. The reference takes an int only; here a whole double is taken
            // too, since this encoder hands a repeat element's numbers on as doubles.
            static bool Whole(object? v) => v is long or int
                || v is double d && double.IsFinite(d) && d == Math.Floor(d) && Math.Abs(d) < 1e15;
            if (!got.Values.All(Whole)) throw Bad();
            long G(string p) => Convert.ToInt64(got[p], CultureInfo.InvariantCulture);
            return At(G("year"), G("month"), G("day"), G("hour"), G("minute"), G("second"), 0m);
        }
        if (ReadbackError(field) is { } error) throw new FieldErrorException(error);
        var pattern = spec.Pattern!;
        var regex = new StringBuilder("^");
        var groups = new List<string>();
        int pos = 0;
        foreach (Match m in PatternToken.Matches(pattern))
        {
            regex.Append(Regex.Escape(pattern[pos..m.Index]));
            var token = m.Groups[1].Value;
            if (token == "%")
                regex.Append('%');
            else
            {
                regex.Append(token switch
                {
                    "Y" => "([0-9]{4})",
                    "L" => "([0-9]{3})",
                    "b" => "(" + string.Join("|", MonthAbbr) + ")",
                    "a" => "(" + string.Join("|", DayAbbr) + ")",
                    _ => "([0-9]{2})",
                });
                groups.Add(token);
            }
            pos = m.Index + m.Length;
        }
        regex.Append(Regex.Escape(pattern[pos..])).Append('$');
        var match = value is string text ? Regex.Match(text, regex.ToString(), RegexOptions.CultureInvariant) : null;
        if (match == null || !match.Success) throw Bad();
        var found = new Dictionary<string, string>();
        for (int i = 0; i < groups.Count; i++)
        {
            var piece = match.Groups[i + 1].Value;
            if (found.TryGetValue(groups[i], out var before) && before != piece) throw Bad();
            found[groups[i]] = piece;
        }
        long Int(string t) => long.Parse(found[t], CultureInfo.InvariantCulture);
        long month = found.ContainsKey("b") ? Array.IndexOf(MonthAbbr, found["b"]) + 1 : Int("m");
        if (found.ContainsKey("m") && found.ContainsKey("b") && Int("m") != month)
            throw Bad("; its month and month name disagree");
        decimal frac = found.ContainsKey("L") ? Int("L") / 1000m : 0m;
        var result = At(Int("Y"), month, Int("d"), Int("H"), Int("M"), Int("S"), frac);
        if (found.TryGetValue("a", out var weekday)
            && DayAbbr[FloorMod(FloorDiv(Floor(result), 86400) + 3, 7)] != weekday)
            throw Bad($"; {weekday} is not that date's weekday");
        return result;
    }

    /// <summary>PS-615: the number PS-610 reads for a time field's value - seconds since its
    /// epoch - which the field's modifiers then invert.</summary>
    internal static object InputSeconds(SchemaField field, object? value) =>
        JsNumber(ParseTime(field, value) - EpochOf(field.Time!));

    /// <summary>PS-615: a `parts` time field's value gives its part fields' values, which the
    /// encoder then writes. A part that reads no bytes cannot be written, and the field is
    /// refused for encoding. The input itself is not modified.</summary>
    internal static Dictionary<string, object?> ExpandParts(List<SchemaField> fields, Dictionary<string, object?> data)
    {
        Dictionary<string, object?>? output = null;
        foreach (var field in fields)
        {
            if (field.Time?.Parts == null) continue;
            if (!data.TryGetValue(field.Name ?? "", out var given) || given == null) continue;
            long whole = Floor(ParseTime(field, given));
            long days = FloorDiv(whole, 86400), rest = FloorMod(whole, 86400);
            var (year, month, day) = CivilFromDays(days);
            var moment = new Dictionary<string, long>
            {
                ["year"] = year, ["month"] = month, ["day"] = day,
                ["hour"] = rest / 3600, ["minute"] = rest / 60 % 60, ["second"] = rest % 60,
            };
            output ??= new Dictionary<string, object?>(data);
            foreach (var part in Parts)
            {
                if (!field.Time.Parts.TryGetValue(part, out var key)) continue;
                var source = fields.LastOrDefault(f => f.Name == key);
                if (source != null && UnwrittenPartTypes.Contains(source.RawType))
                    throw new FieldErrorException($"Field '{field.Name}': its {part} part '{key}' reads "
                        + "no bytes, so the schema decodes but is not encoded (PS-615)");
                var value = moment[part];
                if (output.TryGetValue(key, out var have) && have != null
                    && !(have is not bool && Helpers.ToFloat64(have) is (true, var n) && n == value))
                    throw new FieldErrorException($"Field '{field.Name}': its {part} is {value}, but "
                        + $"'{key}' is given {Meta.PyRepr(have)} (PS-615)");
                output[key] = value;
            }
        }
        return output ?? data;
    }
}
