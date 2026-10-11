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
    static readonly string[] GuardOps = { "gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in" };
    /// <summary>The set operators (PS-543): each takes a list of numbers.</summary>
    static readonly string[] SetOps = { "in", "not_in" };
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

    /// <summary>What an `element` may not be (PS-545): it is one data field, not a construct entry.</summary>
    static readonly string[] ElementConstructs = { "match", "byte_group", "flagged", "tlv", "records" };

    /// <summary>
    /// `$name[i]`: the value a sibling repeat of single values reported at this position
    /// (PS-546), in a repeat's `identity` or an element's `unit` or `ipso.instance`.
    /// </summary>
    internal static readonly Regex SiblingReference = new(@"^\$(\w+)\[(\w+)\]$");

    /// <summary>
    /// The `$name[i]` references a repeat declares (PS-546): its identity, and its
    /// elements' `unit` and `ipso.instance`, as (key, sibling, index) triples. Mirrors
    /// sibling_references.
    /// </summary>
    internal static List<(string key, string name, string index)> SiblingReferences(YamlMappingNode repeat)
    {
        var refs = new List<(string, YamlNode?)> { ("identity", Get(repeat, "identity")) };
        foreach (var m in RepeatMembers(repeat).members?.Children.OfType<YamlMappingNode>()
                          ?? Enumerable.Empty<YamlMappingNode>())
        {
            refs.Add(("unit", Get(m, "unit")));
            refs.Add(("ipso.instance", Get(m, "ipso") is YamlMappingNode ipso ? Get(ipso, "instance") : null));
        }
        var found = new List<(string, string, string)>();
        foreach (var (key, node) in refs)
            if (YamlString(node) is { } text && SiblingReference.Match(text) is { Success: true } m)
                found.Add((key, m.Groups[1].Value, m.Groups[2].Value));
        return found;
    }

    /// <summary>
    /// PS-546: each `$name[i]` names a repeat declaring `element`, decoded earlier in the
    /// same field list, and `i` is this repeat's `index`; the sibling's element carries a
    /// lookup or is a literal (PS-374). Worded as sibling_errors.
    /// </summary>
    static void SiblingErrors(YamlMappingNode repeat, Dictionary<string, YamlMappingNode> earlier,
        string at, List<string> errors)
    {
        var index = YamlString(Get(repeat, "index"));
        foreach (var (key, name, ix) in SiblingReferences(repeat))
        {
            var label = $"{at}: {key} `${name}[{ix}]`";
            if (ix != index)
            {
                errors.Add($"{label} must index by the repeat's own `index` (PS-546)");
                continue;
            }
            if (!earlier.TryGetValue(name, out var sibling))
            {
                errors.Add($"{label} names no repeat declaring `element` earlier in the same "
                    + "field list (PS-546)");
                continue;
            }
            var element = (YamlMappingNode)Get(sibling, "element")!;
            if (!(Has(element, "lookup") || IsLiteralNode(element)))
                errors.Add($"{label}: the elements of '{name}' identify values but carry no "
                    + "lookup and are not a literal (PS-374, PS-546)");
        }
    }

    /// <summary>The name an `element` (PS-528) is decoded under where it declares none.</summary>
    public const string ElementName = "element";

    /// <summary>
    /// A repeat's element fields and, for a repeat of single values, the element's name
    /// (repeat_members in the reference). An `element` is decoded as a one-field list whose
    /// field is the element under its own name, or <see cref="ElementName"/>, so
    /// `present_if` and `name_from` in it can refer to the current value.
    /// </summary>
    internal static (YamlSequenceNode? members, string? elementName) RepeatMembers(YamlMappingNode repeat)
    {
        if (Get(repeat, "element") is YamlMappingNode element && !Has(repeat, "fields"))
        {
            var name = YamlString(Get(element, "name")) is { Length: > 0 } n ? n : ElementName;
            var copy = new YamlMappingNode();
            if (name.StartsWith('_'))
            {
                // PS-545: the element's name is never reported, so an internal name hides
                // nothing. It is decoded under ElementName, and `var` keeps `$_x` bound.
                copy.Add("name", ElementName);
                if (!Has(element, "var")) copy.Add("var", name);
                name = ElementName;
            }
            else
                copy.Add("name", name);
            foreach (var kv in element.Children)
                if (Scalar(kv.Key) != "name") copy.Add(kv.Key, kv.Value);
            return (new YamlSequenceNode(copy), name);
        }
        return (Get(repeat, "fields") as YamlSequenceNode, null);
    }

    /// <summary>
    /// PS-083, PS-528, PS-530: exactly one of `fields` and `element`, an `element` that is
    /// one field definition, and an integer `index_base`. Worded as the spec prototype.
    /// </summary>
    static void ElementRepeatErrors(YamlMappingNode f, List<string> errors)
    {
        var label = $"Repeat '{(Get(f, "name") is { } nm ? Scalar(nm) : "<unnamed>")}'";
        bool hasFields = Has(f, "fields"), hasElement = Has(f, "element");
        if (hasFields == hasElement)
            errors.Add($"{label} declares {(hasFields ? "both" : "neither")} of `fields` and `element`; "
                + "it declares exactly one (PS-083, PS-528)");
        else if (hasElement && Get(f, "element") is not YamlMappingNode)
            errors.Add($"{label}: `element` is a single field definition (PS-528)");
        else if (hasElement && Get(f, "element") is YamlMappingNode element
                 && (!Has(element, "type") || ElementConstructs.Any(c => Has(element, c))))
            errors.Add($"{label}: `element` is a data field that declares `type`, not a "
                + "construct (PS-545)");
        if (Get(f, "index_base") is { } ib && YamlInt(ib) == null)
            errors.Add($"{label}: `index_base` is an integer (PS-530)");
    }

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
        RecordsErrors(root, errors);                                               // PS-541
        ConditionErrors(root, errors);                                             // PS-543
        IpsoInstanceErrors(root, errors);                                          // PS-548
        if (errors.Count > 0)
            throw new InvalidOperationException(string.Join("; ", errors));
    }

    static bool Typed(YamlMappingNode f) => Get(f, "type") is { } t && Scalar(t) != "";

    /// <summary>
    /// Whether a field list reaches a `records` entry (PS-541): directly, or through a
    /// match case or default (records_reachable in the reference).
    /// </summary>
    internal static bool RecordsReachable(YamlSequenceNode? fields)
    {
        foreach (var f in fields?.Children.OfType<YamlMappingNode>() ?? Enumerable.Empty<YamlMappingNode>())
        {
            if (Has(f, "records") && !Typed(f)) return true;
            if (Get(f, "match") is YamlMappingNode match && !Typed(f))
            {
                var bodies = (Get(match, "cases") as YamlMappingNode)?.Children.Values.ToList() ?? new List<YamlNode>();
                if (Get(match, "default") is { } d) bodies.Add(d);
                if (bodies.Any(b => b is YamlSequenceNode bs && RecordsReachable(bs))) return true;
            }
        }
        return false;
    }

    /// <summary>
    /// PS-541: a `records` entry is a non-empty list of field lists, in a repeat's element
    /// or a match case within it, and never reached from an `element` (PS-528). Worded as
    /// records_errors in the reference.
    /// </summary>
    static void RecordsErrors(YamlMappingNode root, List<string> errors)
    {
        void Walk(YamlNode? fields, bool inElement)
        {
            if (fields is not YamlSequenceNode seq) return;
            foreach (var item in seq.Children.OfType<YamlMappingNode>())
            {
                if (Has(item, "records") && !Typed(item))
                {
                    if (!inElement)
                        errors.Add("`records` belongs in a repeat's element, or a match case within it (PS-541)");
                    if (Get(item, "records") is not YamlSequenceNode bodies || bodies.Children.Count == 0
                        || bodies.Children.Any(b => b is not YamlSequenceNode))
                    {
                        errors.Add("`records` is a non-empty list of field lists (PS-541)");
                        continue;
                    }
                    foreach (var body in bodies.Children) Walk(body, false);
                    continue;
                }
                if (YamlString(Get(item, "type")) == "repeat")
                {
                    if (Get(item, "element") is YamlMappingNode element)
                    {
                        if (RecordsReachable(new YamlSequenceNode(element)))
                            errors.Add($"Repeat '{(Get(item, "name") is { } nm ? Scalar(nm) : "<unnamed>")}': "
                                + "an `element` cannot reach a `records` entry (PS-541)");
                        else
                            Walk(new YamlSequenceNode(element), false);
                    }
                    Walk(Get(item, "fields"), true);
                    Walk(Get(item, "trailer"), false);
                    continue;
                }
                if (Get(item, "match") is YamlMappingNode match && !Typed(item))
                {
                    if (Get(match, "cases") is YamlMappingNode cases)
                        foreach (var body in cases.Children.Values) Walk(body, inElement);
                    Walk(Get(match, "default"), inElement);
                }
                if (Get(item, "cases") is YamlMappingNode legacy)
                    foreach (var body in legacy.Children.Values) Walk(body, false);
                Walk(Get(item, "fields"), false);
                if (Get(item, "tlv") is YamlMappingNode tlv && Get(tlv, "cases") is YamlMappingNode tlvCases)
                    foreach (var body in tlvCases.Children.Values) Walk(body, false);
                if (Get(item, "flagged") is YamlMappingNode flagged && Get(flagged, "groups") is YamlSequenceNode groups)
                    foreach (var g in groups.Children.OfType<YamlMappingNode>()) Walk(Get(g, "fields"), false);
                var group = Get(item, "byte_group");
                Walk(group is YamlMappingNode gm ? Get(gm, "fields") : group, false);
            }
        }

        Walk(Get(root, "fields"), false);
        if (Get(root, "ports") is YamlMappingNode ports)
            foreach (var kv in ports.Children)
                Walk(kv.Value is YamlMappingNode entry ? Get(entry, "fields") : kv.Value, false);
    }

    /// <summary>
    /// PS-543: a condition's `in` or `not_in` is a list of numbers, and the condition's one
    /// operator (PS-126), over every guard condition and `present_if` (condition_errors).
    /// </summary>
    static void ConditionErrors(YamlMappingNode root, List<string> errors)
    {
        void Check(YamlNode? node)
        {
            if (node is not YamlMappingNode cond || !SetOps.Any(op => Has(cond, op))) return;
            if (GuardOps.Count(op => Has(cond, op)) != 1)
                errors.Add("a condition with `in` or `not_in` has no other comparison operator (PS-126, PS-543)");
            foreach (var op in SetOps)
                if (Get(cond, op) is { } values && !(values is YamlSequenceNode vs
                        && vs.Children.All(v => YamlNumber(v))))
                    errors.Add($"a condition's `{op}` is a list of numbers (PS-543)");
        }

        void Walk(YamlNode node)
        {
            if (node is YamlMappingNode map)
            {
                Check(Get(map, "present_if"));
                if (Get(map, "guard") is YamlMappingNode guard && Get(guard, "when") is YamlSequenceNode when)
                    foreach (var c in when.Children) Check(c);
                foreach (var kv in map.Children)
                    if (Scalar(kv.Key) != "test_vectors") Walk(kv.Value);
            }
            else if (node is YamlSequenceNode seq)
                foreach (var item in seq.Children) Walk(item);
        }

        Walk(root);
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
        var earlier = new Dictionary<string, YamlMappingNode>();   // PS-546: element repeats before this field
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
                ElementRepeatErrors(f, errors);                                    // PS-528, PS-530
                var (members, elementName) = RepeatMembers(f);
                var memberKey = elementName != null ? "element" : "fields";
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

                SiblingErrors(f, earlier, at, errors);                             // PS-546
                if (Get(f, "identity") is { } identityNode)
                {
                    var identity = Scalar(identityNode);
                    var index = Get(f, "index") is { } ix ? Scalar(ix) : null;
                    var ok = identity == index
                             || (YamlString(identityNode) is { } sid && SiblingReference.IsMatch(sid))
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
                                $"{at}.{memberKey}[{j}] ({(Get(mm, "name") is { } n2 ? Scalar(n2) : "?")})", errors);
                    }
                }
                IteratorErrors(members, enclosing.Concat(own).ToList(), true, $"{at}.{memberKey}", errors);
                if (Get(f, "trailer") is YamlSequenceNode trailerList)
                    IteratorErrors(trailerList, enclosing, false, $"{at}.trailer", errors);
                if (Get(f, "element") is YamlMappingNode && YamlString(Get(f, "name")) is { } repeatName)
                    earlier[repeatName] = f;
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
        // A `$name[i]` names a sibling repeat (PS-546), which SiblingErrors checks.
        if (YamlString(Get(member, "unit")) is { } unit && unit.StartsWith('$') && !SiblingReference.IsMatch(unit))
            refs.Add(("unit", unit[1..]));
        if (Get(member, "ipso") is YamlMappingNode ipso && YamlString(Get(ipso, "instance")) is { } inst
            && inst.StartsWith('$') && !SiblingReference.IsMatch(inst))
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
                    Walk(kv.Value, inElements || (key is "fields" or "element" && isRepeat));
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

public static partial class SchemaParser
{
    /// <summary>The largest LwM2M object instance ID; 65535 is reserved (PS-548).</summary>
    const long IpsoInstanceMax = 65534;

    /// <summary>
    /// PS-548: an `ipso.instance` is an integer from 0 to 65534, or a reference every value
    /// of which is one - a repeat's index whose range from `index_base` fits, a literal
    /// such integer, or a lookup whose labels are all such integers with no `${value}`
    /// default. Checked through the listing `_meta` would carry (PS-375, PS-547), so an
    /// own-element reference (PS-373) and a sibling's `$name[i]` (PS-546) are held to the
    /// same rule. Mirrors ipso_instance_errors.
    /// </summary>
    static void IpsoInstanceErrors(YamlMappingNode root, List<string> errors)
    {
        static bool Valid(object? v) => v is long n && n >= 0 && n <= IpsoInstanceMax;

        void Check(YamlMappingNode field, YamlMappingNode? repeat, Dictionary<string, YamlMappingNode> siblings)
        {
            var ipso = (YamlMappingNode)Get(field, "ipso")!;
            var listing = Meta.Reference(Meta.FromYaml(Get(ipso, "instance")), repeat, siblings: siblings);
            bool ok;
            if (listing is Dictionary<string, object?> d && d.ContainsKey("index"))
            {
                if (d["count"] is not long count) return;   // PS-374 reports an index with no literal count or max
                var low = d.TryGetValue("index_base", out var b) && b is long lb ? lb : 0L;
                ok = Valid(low) && Valid(low + Math.Max(count, 1) - 1);
            }
            else if (listing is Dictionary<string, object?> l)
                ok = !(l.TryGetValue("open", out var o) && o is true)
                     && (l.TryGetValue("values", out var vs) && vs is List<object?> values ? values.All(Valid) : true);
            else
                ok = Valid(listing);
            if (!ok)
            {
                var name = YamlString(Get(field, "name")) is { } n ? n : "<unnamed>";
                errors.Add($"Field '{name}': its ipso.instance can be other than an integer from 0 "
                    + $"to {IpsoInstanceMax} (PS-548)");
            }
        }

        void Walk(YamlNode? node, YamlMappingNode? repeat, Dictionary<string, YamlMappingNode> siblings)
        {
            if (node is YamlSequenceNode seq)
            {
                var here = Meta.ElementRepeats(seq.Children.OfType<YamlMappingNode>());
                foreach (var item in seq.Children)
                    WalkItem(item, repeat, siblings, here);
            }
            else if (node is YamlMappingNode)
                WalkItem(node, repeat, siblings, siblings);
        }

        void WalkItem(YamlNode item, YamlMappingNode? repeat, Dictionary<string, YamlMappingNode> siblings,
            Dictionary<string, YamlMappingNode> here)
        {
            if (item is not YamlMappingNode map)
            {
                Walk(item, repeat, siblings);
                return;
            }
            if (Get(map, "ipso") is YamlMappingNode ipso && Has(ipso, "instance"))
                Check(map, repeat, siblings);
            var isRepeat = YamlString(Get(map, "type")) == "repeat";
            foreach (var kv in map.Children)
            {
                var key = Scalar(kv.Key);
                if (key is "test_vectors" or "definitions" or "ipso" or "senml" or "lookup") continue;
                if (isRepeat && key is "fields" or "element")
                {
                    // A member's references name this repeat's index and fields, and the
                    // element repeats beside it (PS-546).
                    Walk(kv.Value is YamlSequenceNode ? kv.Value : new YamlSequenceNode(kv.Value), map, here);
                }
                else if (kv.Value is YamlMappingNode)
                    WalkItem(kv.Value, repeat, siblings, here);
                else
                    Walk(kv.Value, repeat, siblings);
            }
        }

        WalkItem(root, null, new Dictionary<string, YamlMappingNode>(), new Dictionary<string, YamlMappingNode>());
    }
}
