// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text.RegularExpressions;
using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// 0.5.2 wave 6a: the repeat iterator and reserved trailers (CR-2026-048, -053, -054,
/// -055, -080, -081). The schema rules, checked when the schema is loaded. Each method
/// mirrors the function of the same purpose in tools/schema_interpreter.py:
/// <c>iterator_errors</c>, <c>_repeat_member_ref_errors</c>, <c>tlv_reserve_errors</c>
/// and <c>repeat_only_names</c>.
/// </summary>
public static partial class SchemaParser
{
    static readonly string[] IteratorKeys = { "index", "count_as" };
    static readonly string[] GuardOps = { "gt", "gte", "lt", "lte", "eq", "ne" };
    static readonly string[] IteratorConstructs = { "match", "tlv", "flagged", "byte_group" };

    static YamlNode? Get(YamlMappingNode map, string key)
    {
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return kv.Value;
        return null;
    }

    static bool Has(YamlMappingNode map, string key) => Get(map, key) != null;

    /// <summary>A plain scalar YAML resolves to something other than a string.</summary>
    static bool PlainNonString(YamlScalarNode s)
    {
        if (s.Style != ScalarStyle.Plain) return false;
        var v = s.Value ?? "";
        return v is "" or "~" or "null" or "Null" or "NULL" or "true" or "True" or "TRUE"
                   or "false" or "False" or "FALSE"
               || double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out _)
               || (v.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
                   && long.TryParse(v[2..], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out _));
    }

    /// <summary>The node's string value, or null where YAML reads it as anything else.</summary>
    static string? YamlString(YamlNode? node) =>
        node is YamlScalarNode s && !PlainNonString(s) ? s.Value ?? "" : null;

    /// <summary>The node as an integer where YAML reads it as one (not a bool or a float).</summary>
    static long? YamlInt(YamlNode? node)
    {
        if (node is not YamlScalarNode s || s.Style != ScalarStyle.Plain) return null;
        var v = s.Value ?? "";
        if (long.TryParse(v, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var n)) return n;
        if (v.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
            && long.TryParse(v[2..], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var h)) return h;
        return null;
    }

    static bool YamlNumber(YamlNode? node) =>
        node is YamlScalarNode s && s.Style == ScalarStyle.Plain && (YamlInt(node) != null
            || double.TryParse(s.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out _));

    static bool IsLiteralNode(YamlMappingNode f) =>
        YamlString(Get(f, "type")) is "string" or "number" && Has(f, "value");

    /// <summary>
    /// Rejects a schema breaking any of the iterator's rules, or a tlv's reserve rule
    /// (schema_iterator_errors in the reference).
    /// </summary>
    static void CheckIterator(YamlMappingNode root)
    {
        var errors = new List<string>();
        if (Get(root, "fields") is YamlSequenceNode top)
            IteratorErrors(top, new List<string>(), false, "fields", errors);
        if (Get(root, "ports") is YamlMappingNode ports)
        {
            foreach (var kv in ports.Children)
            {
                var group = kv.Value is YamlMappingNode entry ? Get(entry, "fields") : kv.Value;
                if (group is YamlSequenceNode seq)
                    IteratorErrors(seq, new List<string>(), false, $"ports[{Scalar(kv.Key)}].fields", errors);
            }
        }
        foreach (var node in TlvHolders(root))
            TlvReserveErrors(node, errors);
        if (errors.Count > 0)
            throw new InvalidOperationException(string.Join("; ", errors));
    }

    static IEnumerable<YamlMappingNode> TlvHolders(YamlNode node)
    {
        if (node is YamlMappingNode map)
        {
            var type = Get(map, "type");
            if (Has(map, "tlv") && (type == null || Scalar(type) == ""))
                yield return map;
            foreach (var kv in map.Children)
            {
                if (Scalar(kv.Key) is "test_vectors" or "definitions") continue;
                foreach (var found in TlvHolders(kv.Value)) yield return found;
            }
        }
        else if (node is YamlSequenceNode seq)
        {
            foreach (var item in seq.Children)
                foreach (var found in TlvHolders(item)) yield return found;
        }
    }

    /// <summary>PS-471: a tlv's reserve is a non-negative integer.</summary>
    static void TlvReserveErrors(YamlMappingNode holder, List<string> errors)
    {
        if (Get(holder, "tlv") is not YamlMappingNode body || Get(body, "reserve") is not { } reserve) return;
        if (YamlInt(reserve) is not { } n || n < 0)
            errors.Add($"tlv reserve must be a non-negative integer, got {Scalar(reserve)} (PS-471)");
    }

    static void IteratorErrors(YamlSequenceNode? fields, List<string> enclosing, bool inElements,
        string where, List<string> errors)
    {
        if (fields == null) return;
        int i = -1;
        foreach (var item in fields.Children)
        {
            i++;
            if (item is not YamlMappingNode f) continue;
            var at = $"{where}[{i}] ({(Get(f, "name") is { } nm ? Scalar(nm) : "?")})";
            var type = YamlString(Get(f, "type"));

            if (Get(f, "carry") is { } carry)
            {
                if (!inElements || type is not ("number" or "integer"))
                    errors.Add($"{at}: carry applies only to a computed field declared in a "
                        + "repeat's elements (PS-381)");
                else if (!(YamlNumber(carry) || (YamlString(carry) is { } cs && cs.StartsWith('$'))))
                    errors.Add($"{at}: carry must be a numeric literal or a $ reference to a "
                        + "field decoded before the repeat (PS-381)");
            }

            if (type == "repeat")
            {
                var members = Get(f, "fields") as YamlSequenceNode;
                var names = new HashSet<string>();
                foreach (var m in members?.Children.OfType<YamlMappingNode>() ?? Enumerable.Empty<YamlMappingNode>())
                    if (Get(m, "name") is { } mn) names.Add(Scalar(mn));
                var own = new List<string>();
                foreach (var key in IteratorKeys)
                {
                    var node = Get(f, key);
                    if (node == null || (node is YamlScalarNode ns && ns.Style == ScalarStyle.Plain
                                         && ns.Value is "~" or "null" or ""))
                        continue;
                    var value = YamlString(node);
                    if (string.IsNullOrEmpty(value))
                    {
                        errors.Add($"{at}: {key} must be a name (PS-366, PS-367)");
                        continue;
                    }
                    if (names.Contains(value) || enclosing.Contains(value) || own.Contains(value))
                        errors.Add($"{at}: {key} '{value}' names a field of the element, or an "
                            + "index or count_as already in scope (PS-369)");
                    own.Add(value);
                }

                var reserveNode = Get(f, "reserve");
                if (reserveNode != null)
                {
                    if (YamlString(Get(f, "until")) != "end")
                        errors.Add($"{at}: reserve applies only to a repeat with until: end (PS-350)");
                    else if (YamlInt(reserveNode) is not { } r || r < 0)
                        errors.Add($"{at}: reserve must be a non-negative integer (PS-350)");
                }

                if (Get(f, "trailer") is { } trailer)
                {
                    int? size = null;
                    if (trailer is YamlSequenceNode trailerSeq)
                    {
                        var parsed = ParseFields(trailerSeq);
                        var fixedSize = SchemaDecoder.FixedElementSize(parsed);
                        size = fixedSize > 0 ? fixedSize : null;
                    }
                    if (reserveNode == null)
                        errors.Add($"{at}: trailer needs reserve (PS-383)");
                    else if (size == null)
                        errors.Add($"{at}: every trailer field needs a size known from the schema (PS-384)");
                    else if (YamlInt(reserveNode) != size)
                        errors.Add($"{at}: the trailer's fields take {size} byte(s), and reserve is "
                            + $"{Scalar(reserveNode)}; they must be equal (PS-384)");
                }

                if (Get(f, "present_if") is { } presentIf
                    && !(presentIf is YamlMappingNode pm && YamlString(Get(pm, "field")) != null
                         && GuardOps.Any(op => Has(pm, op))))
                    errors.Add($"{at}: present_if must be one guard condition, "
                        + "{field: $name, <op>: value} (PS-386)");

                if (Get(f, "identity") is { } identityNode)
                {
                    var identity = Scalar(identityNode);
                    var index = Get(f, "index") is { } ix ? Scalar(ix) : null;
                    var ok = identity == index
                             || (YamlString(identityNode) is { } id && id.StartsWith('$')
                                 && names.Contains(id[1..]));
                    if (!ok)
                        errors.Add($"{at}: identity must be the repeat's index or a $ reference "
                            + "to a field of its elements (PS-372)");
                }

                if (members != null)
                {
                    int j = -1;
                    foreach (var member in members.Children)
                    {
                        j++;
                        if (member is YamlMappingNode mm)
                            RepeatMemberRefErrors(f, members, mm,
                                $"{at}.fields[{j}] ({(Get(mm, "name") is { } n2 ? Scalar(n2) : "?")})", errors);
                    }
                }
                IteratorErrors(members, enclosing.Concat(own).ToList(), true, $"{at}.fields", errors);
                if (Get(f, "trailer") is YamlSequenceNode trailerList)
                    IteratorErrors(trailerList, enclosing, false, $"{at}.trailer", errors);
                continue;
            }

            if (Get(f, "fields") is YamlSequenceNode nested)
                IteratorErrors(nested, enclosing, false, $"{at}.fields", errors);
            foreach (var construct in IteratorConstructs)
            {
                if (Get(f, construct) is not YamlMappingNode body) continue;
                var cases = Get(body, "cases");
                IEnumerable<YamlNode> caseNodes = cases switch
                {
                    YamlMappingNode cm => cm.Children.Values,
                    YamlSequenceNode cl => cl.Children,
                    _ => Enumerable.Empty<YamlNode>(),
                };
                foreach (var c in caseNodes)
                {
                    var group = c as YamlSequenceNode
                                ?? (c is YamlMappingNode cmap ? Get(cmap, "fields") as YamlSequenceNode : null);
                    IteratorErrors(group, enclosing, false, $"{at}.{construct}", errors);
                }
                if (Get(body, "fields") is YamlSequenceNode bodyFields)
                    IteratorErrors(bodyFields, enclosing, false, $"{at}.{construct}", errors);
                if (Get(body, "groups") is YamlSequenceNode groups)
                    foreach (var g in groups.Children.OfType<YamlMappingNode>())
                        IteratorErrors(Get(g, "fields") as YamlSequenceNode, enclosing, false,
                            $"{at}.{construct}", errors);
            }
        }
    }

    /// <summary>PS-373, PS-374: a per-element unit, IPSO instance or SenML name, and what it names.</summary>
    static void RepeatMemberRefErrors(YamlMappingNode repeat, YamlSequenceNode members,
        YamlMappingNode member, string where, List<string> errors)
    {
        var byName = new Dictionary<string, YamlMappingNode>();
        foreach (var m in members.Children.OfType<YamlMappingNode>())
            if (Get(m, "name") is { } n && !byName.ContainsKey(Scalar(n)))
                byName[Scalar(n)] = m;
        var index = Get(repeat, "index") is { } ix ? Scalar(ix) : null;
        var refs = new List<(string key, string name)>();
        if (YamlString(Get(member, "unit")) is { } unit && unit.StartsWith('$'))
            refs.Add(("unit", unit[1..]));
        if (Get(member, "ipso") is YamlMappingNode ipso && YamlString(Get(ipso, "instance")) is { } inst
            && inst.StartsWith('$'))
            refs.Add(("ipso.instance", inst[1..]));
        if (Get(member, "senml") is YamlMappingNode senml && YamlString(Get(senml, "name")) is { } sname)
            foreach (Match m in Regex.Matches(sname, @"\$\{([^}]+)\}"))
                refs.Add(("senml.name", m.Groups[1].Value));
        foreach (var (key, name) in refs)
        {
            if (name == index)
            {
                if (YamlInt(Get(repeat, "count")) == null && !Has(repeat, "max"))
                    errors.Add($"{where}: {key} uses the index '{name}', so the repeat needs a "
                        + "literal count or a max (PS-374)");
                continue;
            }
            if (!byName.TryGetValue(name, out var target))
                errors.Add($"{where}: {key} names '{name}', which is neither a field of the "
                    + "element nor the repeat's index (PS-373)");
            else if (!(Has(target, "lookup") || IsLiteralNode(target)))
                errors.Add($"{where}: {key} names '{name}', which must carry a lookup or be a "
                    + "literal so its values are known from the schema (PS-374)");
        }
    }

    /// <summary>Names declared in some repeat's elements and nowhere outside one (PS-368).</summary>
    static HashSet<string> RepeatOnlyNames(YamlMappingNode root)
    {
        var inside = new HashSet<string>();
        var outside = new HashSet<string>();

        void Walk(YamlNode node, bool inElements)
        {
            if (node is YamlMappingNode map)
            {
                if (YamlString(Get(map, "name")) is { } name && (Has(map, "type") || Has(map, "fields")))
                    (inElements ? inside : outside).Add(name);
                var isRepeat = Get(map, "type") is YamlScalarNode t && t.Value == "repeat";
                foreach (var kv in map.Children)
                {
                    var key = Scalar(kv.Key);
                    if (key is "test_vectors" or "definitions") continue;
                    Walk(kv.Value, inElements || (key == "fields" && isRepeat));
                }
            }
            else if (node is YamlSequenceNode seq)
            {
                foreach (var item in seq.Children) Walk(item, inElements);
            }
        }

        Walk(root, false);
        inside.ExceptWith(outside);
        return inside;
    }
}
