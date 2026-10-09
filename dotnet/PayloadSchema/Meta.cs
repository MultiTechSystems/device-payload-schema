// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// The interpreter's input context (PS-495): the TS013 uplink's FPort and receive time,
/// and the device's EUI. Each is optional; an absent one is left out of `_meta`.
/// </summary>
public record InputContext(int? FPort = null, object? RecvTime = null, string? DevEUI = null);

/// <summary>
/// `_meta`, the interpreter output's description of a decoded result (Clause 7: PS-175,
/// PS-177, PS-178, PS-180, PS-181, PS-340 to PS-342, PS-371 to PS-376; CR-2026-088
/// PS-480/481; CR-2026-095 PS-489; CR-2026-096 PS-490 to PS-497).
///
/// A port of the `# --- _meta` section of tools/schema_interpreter.py, function by
/// function. It reads the declarations as written - the YAML nodes each field was parsed
/// from - because the typed model has already dropped what `_meta` reports: whether a
/// key was present, lookup labels in schema order, the declared type spelling.
/// </summary>
public static class Meta
{
    /// <summary>Alias type names written as their canonical name (PS-493).</summary>
    static readonly Dictionary<string, string> TypeAliases = BuildAliases();

    static Dictionary<string, string> BuildAliases()
    {
        var map = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var n in new[] { 8, 16, 24, 32, 64 })
        {
            map[$"uint{n}"] = $"u{n}";
            map[$"int{n}"] = $"s{n}";
            map[$"i{n}"] = $"s{n}";
        }
        return map;
    }

    static readonly Regex DevEuiPattern = new("^[0-9a-f]{16}$");
    static readonly Regex RecvTimePattern = new(
        @"^([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?"
        + @"(Z|z|[+-][0-9]{2}:?[0-9]{2})?$");

    // ---------------------------------------------------------------- YAML values

    static readonly Regex YamlInt = new(@"^[-+]?(0|[1-9][0-9_]*)$");
    static readonly Regex YamlHex = new(@"^[-+]?0x[0-9a-fA-F_]+$");
    static readonly Regex YamlOct = new(@"^[-+]?0[0-7_]+$");
    static readonly Regex YamlFloat = new(
        @"^([-+]?([0-9][0-9_]*)\.[0-9_]*([eE][-+][0-9]+)?|[-+]?\.[0-9_]+([eE][-+][0-9]+)?)$");

    /// <summary>
    /// A YAML node as the reference reads it (PyYAML's safe_load, YAML 1.1): a quoted
    /// scalar is a string; a plain one is null, a boolean, an integer (long), a float
    /// (double) or a string; a sequence a list and a mapping an ordered dictionary.
    /// </summary>
    public static object? FromYaml(YamlNode? node)
    {
        switch (node)
        {
            case null:
                return null;
            case YamlScalarNode scalar:
                return Scalar(scalar);
            case YamlSequenceNode seq:
                return seq.Children.Select(FromYaml).ToList();
            case YamlMappingNode map:
            {
                var result = new Dictionary<string, object?>();
                foreach (var kv in map.Children)
                    result[KeyText(kv.Key)] = FromYaml(kv.Value);
                return result;
            }
            default:
                return null;
        }
    }

    static object? Scalar(YamlScalarNode scalar)
    {
        var text = scalar.Value ?? "";
        if (scalar.Style is not (ScalarStyle.Plain or ScalarStyle.Any))
            return text;
        switch (text)
        {
            case "" or "~" or "null" or "Null" or "NULL":
                return null;
            case "yes" or "Yes" or "YES" or "true" or "True" or "TRUE" or "on" or "On" or "ON":
                return true;
            case "no" or "No" or "NO" or "false" or "False" or "FALSE" or "off" or "Off" or "OFF":
                return false;
            case ".inf" or ".Inf" or ".INF" or "+.inf" or "+.Inf" or "+.INF":
                return double.PositiveInfinity;
            case "-.inf" or "-.Inf" or "-.INF":
                return double.NegativeInfinity;
            case ".nan" or ".NaN" or ".NAN":
                return double.NaN;
        }
        var clean = text.Replace("_", "");
        var negative = clean.StartsWith('-');
        var unsigned = clean.TrimStart('-', '+');
        try
        {
            if (YamlInt.IsMatch(text))
                return long.Parse(clean, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
            if (YamlHex.IsMatch(text))
            {
                var v = long.Parse(unsigned[2..], NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                return negative ? -v : v;
            }
            if (YamlOct.IsMatch(text))
            {
                var v = Convert.ToInt64(unsigned, 8);
                return negative ? -v : v;
            }
        }
        catch (OverflowException)
        {
            return double.Parse(clean, NumberStyles.Float, CultureInfo.InvariantCulture);
        }
        if (YamlFloat.IsMatch(text))
            return double.Parse(clean, NumberStyles.Float, CultureInfo.InvariantCulture);
        return text;
    }

    /// <summary>A mapping key as the text Python's str() gives the resolved key.</summary>
    static string KeyText(YamlNode key) => key is YamlScalarNode s ? PyStr(Scalar(s)) : key.ToString() ?? "";

    static YamlNode? Get(YamlMappingNode? map, string key)
    {
        if (map == null) return null;
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return kv.Value;
        return null;
    }

    static bool Has(YamlMappingNode? map, string key)
    {
        if (map == null) return false;
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return true;
        return false;
    }

    static object? Value(YamlMappingNode? map, string key) => FromYaml(Get(map, key));

    static bool Truthy(object? value) => value switch
    {
        null => false,
        bool b => b,
        long l => l != 0,
        double d => d != 0,
        string s => s.Length > 0,
        System.Collections.ICollection c => c.Count > 0,
        _ => true,
    };

    // ---------------------------------------------------------------- declarations

    /// <summary>
    /// A `_meta` entry's `type` (PS-493): the declared type, an alias by its canonical
    /// name, a bit range as written. A `tlv` with `merge: false` reports its entries under
    /// `channels`, and that entry is `tlv`. A field with no type is a schema error (PS-011, PS-441), so
    /// none is invented: null leaves `type` out.
    /// </summary>
    public static string? MetaType(YamlMappingNode field)
    {
        if (Value(field, "type") is string declared)
            return TypeAliases.TryGetValue(declared, out var canonical) ? canonical : declared;
        if (Get(field, "tlv") is YamlMappingNode)
            return "tlv";
        return null;
    }

    static IEnumerable<YamlMappingNode> Items(YamlNode? list) =>
        list is YamlSequenceNode seq ? seq.Children.OfType<YamlMappingNode>() : Enumerable.Empty<YamlMappingNode>();

    static IEnumerable<YamlNode> CaseBodies(YamlNode? cases) =>
        cases is YamlMappingNode map ? map.Children.Values : Enumerable.Empty<YamlNode>();

    static bool MergeIsFalse(YamlMappingNode tlv) => Value(tlv, "merge") is false;

    /// <summary>
    /// Reported name -> declaration for one level of decoded output, first declaration
    /// first (PS-371, PS-481 as amended by CR-2026-096). A construct that merges into its
    /// parent (`match` cases and `default`, `tlv` cases, `flagged` groups, `byte_group`)
    /// contributes its fields at this level; an object or repeat contributes its own name.
    /// Internal fields (PS-494) and `name_from` fields (PS-492) have no entry here.
    /// Mirrors meta_declarations.
    /// </summary>
    public static Dictionary<string, YamlMappingNode> Declarations(IEnumerable<YamlMappingNode> fields)
    {
        var found = new Dictionary<string, YamlMappingNode>(StringComparer.Ordinal);

        void Visit(IEnumerable<YamlMappingNode> items)
        {
            foreach (var f in items)
            {
                var group = Get(f, "byte_group");
                if (group != null && !IsNullNode(group))
                    Visit(Items(group is YamlMappingNode gm ? Get(gm, "fields") : group));
                if (Get(f, "flagged") is YamlMappingNode flagged)
                    foreach (var g in Items(Get(flagged, "groups")))
                        Visit(Items(Get(g, "fields")));
                if (Get(f, "match") is YamlMappingNode match)
                {
                    foreach (var body in CaseBodies(Get(match, "cases")))
                        Visit(Items(body));
                    if (Get(match, "default") is YamlSequenceNode fallback)
                        Visit(Items(fallback));
                    if (Value(match, "name") is string label && !label.StartsWith('_') && !found.ContainsKey(label))
                    {
                        var length = Value(match, "length");
                        long width = length switch
                        {
                            long l => l,
                            double d => (long)d,
                            string s => long.Parse(s.Trim(), CultureInfo.InvariantCulture),
                            _ => 1,
                        };
                        found[label] = new YamlMappingNode(
                            new YamlScalarNode("name"), new YamlScalarNode(label) { Style = ScalarStyle.DoubleQuoted },
                            new YamlScalarNode("type"), new YamlScalarNode($"u{8 * width}") { Style = ScalarStyle.DoubleQuoted });
                    }
                }
                if (Value(f, "type") is "match" && Get(f, "cases") is YamlMappingNode legacy)
                    foreach (var body in legacy.Children.Values)
                        Visit(Items(body));
                if (Get(f, "tlv") is YamlMappingNode tlv)
                {
                    if (MergeIsFalse(tlv))
                    {
                        // Its entries are reported as a list under the fixed key `channels`
                        // (Clause 4), whose entry is `tlv` (PS-493); a tlv has no name (PS-448).
                        found.TryAdd("channels", f);
                        continue;
                    }
                    foreach (var body in CaseBodies(Get(tlv, "cases")))
                        Visit(Items(body));
                    continue;
                }
                if (Value(f, "name") is not string name || name.StartsWith('_') || Truthy(Value(f, "name_from")))
                    continue;
                if (Has(f, "match") && !Has(f, "type"))
                    continue;
                found.TryAdd(name, f);
            }
        }

        Visit(fields);
        return found;
    }

    static bool IsNullNode(YamlNode node) => node is YamlScalarNode s && Scalar(s) == null;

    /// <summary>
    /// A per-element `unit`, `ipso.instance` or `identity` as PS-375 lists it: the
    /// repeat's index as {index, count}, or an element field as {field, values} with a
    /// lookup's `default` last, marked `open` where that default carries `${value}`
    /// (PS-489). Anything else is itself. Mirrors meta_reference.
    /// </summary>
    public static object? Reference(object? value, YamlMappingNode? repeat, bool bare = false)
    {
        if (repeat == null || value is not string text)
            return value;
        if (!(bare || text.StartsWith('$')))
            return value;
        var name = text.StartsWith('$') ? text[1..] : text;
        if (Value(repeat, "index") is string index && index == name)
        {
            var count = Value(repeat, "count");
            if (count is not long)
                count = Value(repeat, "max");
            return new Dictionary<string, object?> { ["index"] = name, ["count"] = count };
        }
        var target = Items(Get(repeat, "fields")).FirstOrDefault(f => Value(f, "name") is string n && n == name);
        var values = new List<object?>();
        var lookup = Get(target, "lookup");
        bool open = false;
        if (lookup is YamlMappingNode lookupMap)
        {
            YamlNode? fallback = null;
            bool hasDefault = false;
            foreach (var kv in lookupMap.Children)
            {
                if (KeyText(kv.Key) == "default")
                {
                    hasDefault = true;
                    fallback = kv.Value;
                    continue;
                }
                values.Add(FromYaml(kv.Value));
            }
            if (hasDefault)
            {
                var label = FromYaml(fallback);
                values.Add(label);
                open = label is string s && s.Contains("${value}");    // PS-489
            }
        }
        else if (lookup is YamlSequenceNode lookupSeq)
            values.AddRange(lookupSeq.Children.Select(FromYaml));
        else if (Has(target, "value"))
            values.Add(Value(target, "value"));
        var listing = new Dictionary<string, object?> { ["field"] = name, ["values"] = values };
        if (open)
            listing["open"] = true;
        return listing;
    }

    /// <summary>
    /// One `_meta.fields` entry, derived from one declaration (PS-178, PS-493, PS-480,
    /// PS-371, PS-481). <paramref name="repeat"/> is the repeat whose elements the field
    /// belongs to, for PS-373 and PS-375. Mirrors field_meta.
    /// </summary>
    public static Dictionary<string, object?> FieldMeta(YamlMappingNode field, YamlMappingNode? repeat = null)
    {
        var entry = new Dictionary<string, object?>();
        var declaredType = MetaType(field);
        if (declaredType != null)
            entry["type"] = declaredType;
        var senml = Get(field, "senml") as YamlMappingNode;
        var senmlUnit = Value(senml, "unit");
        var unit = Truthy(senmlUnit) ? senmlUnit : Value(field, "unit");      // PS-178
        if (unit != null)
            entry["unit"] = Reference(unit, repeat);
        if (Get(field, "ipso") is YamlMappingNode ipso && Has(ipso, "object"))
            entry["ipso"] = new Dictionary<string, object?>
            {
                ["object"] = Value(ipso, "object"),
                ["instance"] = Reference(Has(ipso, "instance") ? Value(ipso, "instance") : 0L, repeat),
                ["resource"] = Has(ipso, "resource") ? Value(ipso, "resource") : 5700L,
            };
        if (Value(senml, "name") is string senmlName)
            entry["senml"] = new Dictionary<string, object?> { ["name"] = senmlName };   // PS-480
        var description = Value(field, "description");
        if (Truthy(description))
            entry["description"] = description;
        if (declaredType == "repeat")
        {
            var elements = new Dictionary<string, object?>();
            foreach (var (name, member) in Declarations(Items(Get(field, "fields"))))
                elements[name] = FieldMeta(member, field);                        // PS-371
            entry["elements"] = elements;
            if (Has(field, "identity"))
                entry["identity"] = Reference(Value(field, "identity"), field, bare: true);
        }
        else if (declaredType == "object")
        {
            var members = new Dictionary<string, object?>();
            foreach (var (name, member) in Declarations(Items(Get(field, "fields"))))
                members[name] = FieldMeta(member);                               // PS-481
            entry["fields"] = members;
        }
        return entry;
    }

    // ---------------------------------------------------------------- input context

    /// <summary>
    /// PS-496: 16 lower-case hexadecimal digits, `-`, `:` and spaces removed. Null where
    /// the input is not a device EUI once normalised.
    /// </summary>
    public static string? NormaliseDevEui(string? text)
    {
        if (text == null) return null;
        var eui = text.Replace("-", "").Replace(":", "").Replace(" ", "").ToLowerInvariant();
        return DevEuiPattern.IsMatch(eui) ? eui : null;
    }

    /// <summary>
    /// PS-177, PS-495: `recvTime` as numeric Unix seconds in UTC. A number is taken as
    /// seconds already; an ISO 8601 string keeps its milliseconds, the fraction rounded
    /// half to even at three digits in decimal. A whole number of seconds is a long,
    /// anything else the double nearest "&lt;seconds&gt;.&lt;3 digits&gt;". Null where the input
    /// is neither. Mirrors rx_time_seconds.
    /// </summary>
    public static object? RxTimeSeconds(object? recvTime)
    {
        switch (recvTime)
        {
            case null or bool:
                return null;
            case int i: return (long)i;
            case long l: return l;
            case short s: return (long)s;
            case byte b: return (long)b;
            case uint ui: return (long)ui;
            case double d: return d;
            case float f: return (double)f;
            case decimal m: return (double)m;
            case string text:
                break;
            default:
                return null;
        }
        var match = RecvTimePattern.Match(((string)recvTime).Trim());
        if (!match.Success) return null;
        int Group(int i) => int.Parse(match.Groups[i].Value, CultureInfo.InvariantCulture);
        int year = Group(1), month = Group(2), day = Group(3);
        if (year < 1 || month < 1 || month > 12) return null;    // as datetime.date refuses them
        // calendar.timegm: the day, hour, minute and second are arithmetic, not checked.
        long days = new DateOnly(year, month, 1).DayNumber - new DateOnly(1970, 1, 1).DayNumber + day - 1;
        long seconds = ((days * 24 + Group(4)) * 60 + Group(5)) * 60 + Group(6);
        var zone = match.Groups[8].Success ? match.Groups[8].Value : "";
        if (zone.Length > 0 && zone is not ("Z" or "z"))
        {
            var sign = zone[0] == '-' ? -1 : 1;
            var digits = zone[1..].Replace(":", "");
            seconds -= sign * (int.Parse(digits[..2], CultureInfo.InvariantCulture) * 3600
                               + int.Parse(digits[2..], CultureInfo.InvariantCulture) * 60);
        }
        var millis = RoundMillis(match.Groups[7].Success ? match.Groups[7].Value : "");
        long total = seconds * 1000 + millis;
        if (total % 1000 == 0)
            return total / 1000;
        var magnitude = Math.Abs(total);
        var decimalText = (total < 0 ? "-" : "") + (magnitude / 1000).ToString(CultureInfo.InvariantCulture)
                          + "." + (magnitude % 1000).ToString("D3", CultureInfo.InvariantCulture);
        return double.Parse(decimalText, NumberStyles.Float, CultureInfo.InvariantCulture);
    }

    /// <summary>A fraction's digits rounded half to even at three places, in thousandths (0..1000).</summary>
    static long RoundMillis(string digits)
    {
        var head = (digits + "000")[..3];
        var tail = digits.Length > 3 ? digits[3..] : "";
        long millis = long.Parse(head, CultureInfo.InvariantCulture);
        if (tail.Length == 0) return millis;
        var rest = tail.TrimEnd('0');
        int cmp = rest.Length == 0 ? -1 : rest[0] > '5' ? 1 : rest[0] < '5' ? -1 : rest.Length > 1 ? 1 : 0;
        if (cmp > 0 || (cmp == 0 && millis % 2 == 1))
            millis++;
        return millis;
    }

    // ---------------------------------------------------------------- PS-491

    static string AgreementKey(YamlMappingNode field, string component)
    {
        switch (component)
        {
            case "unit":
                return PyRepr(Value(field, "unit"));
            case "senml":
            {
                if (Get(field, "senml") is not YamlMappingNode senml)
                    return PyRepr(Value(field, "senml"));
                var pairs = senml.Children
                    .Select(kv => (KeyText(kv.Key), PyStr(FromYaml(kv.Value))))
                    .OrderBy(p => p.Item1, StringComparer.Ordinal)
                    .ThenBy(p => p.Item2, StringComparer.Ordinal)
                    .Select(p => "(" + PyRepr(p.Item1) + ", " + PyRepr(p.Item2) + ")");
                return "(" + string.Join(", ", pairs) + ")";
            }
            default:
            {
                if (Get(field, "ipso") is not YamlMappingNode ipso)
                    return PyRepr(Value(field, "ipso"));
                return "(" + PyRepr(Value(ipso, "object")) + ", "
                       + PyRepr(Has(ipso, "instance") ? Value(ipso, "instance") : 0L) + ", "
                       + PyRepr(Has(ipso, "resource") ? Value(ipso, "resource") : 5700L) + ")";
            }
        }
    }

    /// <summary>
    /// PS-491: the declarations of one reported name in one field list must agree on
    /// `unit`, `senml` and `ipso`. A field list is the top level, each port's, and each
    /// object's or repeat's members; `match` and `tlv` cases, `flagged` groups and
    /// `byte_group`s merge into theirs, and a `merge: false` tlv's cases are one list.
    /// Mirrors meta_declaration_errors.
    /// </summary>
    public static List<string> DeclarationErrors(YamlMappingNode root)
    {
        var errors = new List<string>();

        void Check(IEnumerable<YamlMappingNode> fields, string where)
        {
            var seen = new Dictionary<string, List<YamlMappingNode>>(StringComparer.Ordinal);
            var order = new List<string>();

            void Visit(IEnumerable<YamlMappingNode> items)
            {
                foreach (var f in items)
                {
                    var group = Get(f, "byte_group");
                    if (group != null && !IsNullNode(group))
                        Visit(Items(group is YamlMappingNode gm ? Get(gm, "fields") : group));
                    if (Get(f, "flagged") is YamlMappingNode flagged)
                        foreach (var g in Items(Get(flagged, "groups")))
                            Visit(Items(Get(g, "fields")));
                    if (Get(f, "match") is YamlMappingNode match)
                    {
                        foreach (var body in CaseBodies(Get(match, "cases")))
                            Visit(Items(body));
                        if (Get(match, "default") is YamlSequenceNode fallback)
                            Visit(Items(fallback));
                    }
                    if (Value(f, "type") is "match" && Get(f, "cases") is YamlMappingNode legacy)
                        foreach (var body in legacy.Children.Values)
                            Visit(Items(body));
                    if (Get(f, "tlv") is YamlMappingNode tlv)
                    {
                        var bodies = CaseBodies(Get(tlv, "cases")).OfType<YamlSequenceNode>().ToList();
                        if (MergeIsFalse(tlv))
                            Check(bodies.SelectMany(Items).ToList(),
                                $"{where}/{(Has(f, "name") ? PyStr(Value(f, "name")) : "tlv")}");
                        else
                            foreach (var body in bodies)
                                Visit(Items(body));
                        continue;
                    }
                    if (Value(f, "type") is "object" or "repeat" && Get(f, "fields") is YamlSequenceNode nested)
                        Check(Items(nested).ToList(), $"{where}/{(Has(f, "name") ? PyStr(Value(f, "name")) : "?")}");
                    if (Value(f, "name") is string name && !name.StartsWith('_') && !Truthy(Value(f, "name_from")))
                    {
                        if (!seen.TryGetValue(name, out var list))
                        {
                            seen[name] = list = new List<YamlMappingNode>();
                            order.Add(name);
                        }
                        list.Add(f);
                    }
                }
            }

            Visit(fields);
            foreach (var name in order)
            {
                var decls = seen[name];
                var differing = new[] { "unit", "senml", "ipso" }
                    .Where(component => decls.Select(d => AgreementKey(d, component)).Distinct().Count() > 1)
                    .ToList();
                if (differing.Count > 0)
                    errors.Add($"Field '{name}' is declared {decls.Count} times in {where} and its "
                               + $"declarations differ in {string.Join(", ", differing)}; the declarations "
                               + "of one reported name must agree on unit, senml and ipso (PS-491)");
            }
        }

        if (Get(root, "fields") is YamlSequenceNode top)
            Check(Items(top).ToList(), "fields");
        if (Get(root, "ports") is YamlMappingNode ports)
            foreach (var kv in ports.Children)
            {
                var group = kv.Value is YamlMappingNode entry ? Get(entry, "fields") : kv.Value;
                if (group is YamlSequenceNode seq)
                    Check(Items(seq).ToList(), $"port {KeyText(kv.Key)}");
            }
        return errors;
    }

    // ---------------------------------------------------------------- Python text

    /// <summary>Python's str() of a resolved YAML value.</summary>
    static string PyStr(object? value) => value is string s ? s : PyRepr(value);

    /// <summary>Python's repr() of a resolved YAML value, as the reference's messages write it.</summary>
    public static string PyRepr(object? value)
    {
        switch (value)
        {
            case null: return "None";
            case bool b: return b ? "True" : "False";
            case long or int: return Convert.ToString(value, CultureInfo.InvariantCulture)!;
            case double d:
                if (double.IsNaN(d)) return "nan";
                if (double.IsInfinity(d)) return d > 0 ? "inf" : "-inf";
                var r = d.ToString("R", CultureInfo.InvariantCulture);
                return r.Contains('.') || r.Contains('E') ? r : r + ".0";
            case string s:
            {
                var quote = s.Contains('\'') && !s.Contains('"') ? '"' : '\'';
                var sb = new StringBuilder().Append(quote);
                foreach (var c in s)
                {
                    if (c == '\\') sb.Append("\\\\");
                    else if (c == quote) sb.Append('\\').Append(c);
                    else if (c == '\n') sb.Append("\\n");
                    else if (c == '\r') sb.Append("\\r");
                    else if (c == '\t') sb.Append("\\t");
                    else if (c < 0x20 || c == 0x7f) sb.Append($"\\x{(int)c:x2}");
                    else sb.Append(c);
                }
                return sb.Append(quote).ToString();
            }
            case List<object?> list:
                return "[" + string.Join(", ", list.Select(PyRepr)) + "]";
            case Dictionary<string, object?> map:
                return "{" + string.Join(", ", map.Select(kv => PyRepr(kv.Key) + ": " + PyRepr(kv.Value))) + "}";
            default:
                return Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";
        }
    }
}
