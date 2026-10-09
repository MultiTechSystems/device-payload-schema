// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

public static partial class SchemaParser
{
    public static PayloadSchemaDefinition Parse(string yamlOrJson)
    {
        var yaml = new YamlStream();
        yaml.Load(new StringReader(yamlOrJson));
        var root = (YamlMappingNode)yaml.Documents[0].RootNode;
        // CR-2026-045: splice every $ref first, rejecting what cannot be (PS-345 to PS-349).
        return ParseRoot(ExpandRefs(root));
    }

    const string RefPrefix = "#/definitions/";

    /// <summary>
    /// Splice every `{$ref: '#/definitions/name'}` into the field list it sits in, before
    /// anything is parsed, so a reference works in any field list - a port entry, an
    /// object, a repeat, a case (PS-346, PS-347). Rejected at load: a definition that is
    /// not a field group (PS-345), a reference that does not resolve (PS-348) or names
    /// another document (PS-462, optional and not supported here), a pointer of another
    /// form (PS-461), a cycle (PS-349). Mirrors expand_refs in tools/schema_interpreter.py.
    /// </summary>
    static YamlMappingNode ExpandRefs(YamlMappingNode root)
    {
        var definitions = new Dictionary<string, YamlSequenceNode>();
        if (root.TryGetValue("definitions", out var defsNode))
        {
            if (defsNode is not YamlMappingNode defsMap)
                throw new InvalidOperationException("'definitions' must map names to field groups (PS-345)");
            foreach (var kv in defsMap.Children)
            {
                var name = Scalar(kv.Key);
                if (kv.Value is not YamlMappingNode group || !group.TryGetValue("fields", out var f)
                    || f is not YamlSequenceNode fields)
                    throw new InvalidOperationException(
                        $"definition '{name}' is not a field group with a `fields` array (PS-345)");
                definitions[name] = fields;
            }
        }

        YamlNode Expand(YamlNode node, List<string> stack)
        {
            switch (node)
            {
                case YamlMappingNode map:
                {
                    var outMap = new YamlMappingNode();
                    foreach (var kv in map.Children) outMap.Add(kv.Key, Expand(kv.Value, stack));
                    return outMap;
                }
                case YamlSequenceNode seq:
                {
                    var outSeq = new YamlSequenceNode();
                    foreach (var item in seq.Children)
                    {
                        if (item is YamlMappingNode m && m.TryGetValue("$ref", out var refNode))
                            foreach (var spliced in Resolve(refNode, stack)) outSeq.Add(spliced);
                        else
                            outSeq.Add(Expand(item, stack));
                    }
                    return outSeq;
                }
                default:
                    return node;
            }
        }

        IEnumerable<YamlNode> Resolve(YamlNode refNode, List<string> stack)
        {
            var text = refNode is YamlScalarNode sc ? sc.Value ?? "" : "";
            if (!text.StartsWith(RefPrefix, StringComparison.Ordinal))
            {
                if (text.Contains('#') && !text.StartsWith('#'))
                    throw new InvalidOperationException($"$ref {text} names another document; this "
                        + "implementation resolves only #/definitions/<name> (PS-462)");
                throw new InvalidOperationException($"$ref {text} is not of the form #/definitions/<name> (PS-461)");
            }
            var name = text[RefPrefix.Length..];
            if (stack.Contains(name))
                throw new InvalidOperationException($"$ref cycle: {string.Join(" -> ", stack)} -> {name} (PS-349)");
            if (!definitions.TryGetValue(name, out var fields))
                throw new InvalidOperationException($"$ref {text} does not resolve to a field group (PS-348)");
            return ((YamlSequenceNode)Expand(fields, new List<string>(stack) { name })).Children;
        }

        var outRoot = new YamlMappingNode();
        foreach (var kv in root.Children)
        {
            var key = Scalar(kv.Key);
            outRoot.Add(kv.Key, key is "definitions" or "test_vectors" ? kv.Value : Expand(kv.Value, new List<string>()));
        }
        foreach (var (name, fields) in definitions)
            Expand(fields, new List<string> { name });
        return outRoot;
    }

    static PayloadSchemaDefinition ParseRoot(YamlMappingNode root)
    {
        var schema = new PayloadSchemaDefinition();

        if (root.TryGetValue("name", out var name))
            schema.Name = Scalar(name);
        if (root.TryGetValue("version", out var ver))
            schema.Version = Int(ver);
        if (root.TryGetValue("description", out var desc))
            schema.Description = Scalar(desc);
        if (root.TryGetValue("endian", out var endian))
            schema.Endian = Scalar(endian);
        if (string.IsNullOrEmpty(schema.Endian))
            schema.Endian = "big";
        if (root.TryGetValue("direction", out var direction))
            schema.Direction = Scalar(direction);
        CheckPortDeclarations(root);

        // No `header:` block. It was never in the specification, and honouring it
        // here while Python and Go ignored it meant the same schema decoded
        // differently per language - silently, since the ignoring implementations
        // read the header's bytes as the first fields rather than erroring. Use a
        // `definitions:` entry and `$ref` instead, which is specified and works
        // everywhere; schemas/library/common/headers.yaml does exactly that.

        if (root.TryGetValue("fields", out var fields) && fields is YamlSequenceNode fieldsSeq)
            schema.Fields = ParseFields(fieldsSeq);
        // The repeat iterator's rules and a tlv's reserve (CR-2026-048 to -055, -081),
        // over the whole document so an enclosing repeat's names are known.
        CheckIterator(root);
        CheckWave6b(root);    // PS-404, PS-433
        schema.RepeatOnlyNames = RepeatOnlyNames(root);

        if (root.TryGetValue("definitions", out var defs) && defs is YamlMappingNode defsMap)
        {
            schema.Definitions = new Dictionary<string, DefinitionDef>();
            foreach (var kv in defsMap.Children)
            {
                var defName = Scalar(kv.Key);
                if (kv.Value is YamlMappingNode defMap)
                {
                    var dd = new DefinitionDef();
                    if (defMap.TryGetValue("fields", out var defFields) && defFields is YamlSequenceNode defFieldsSeq)
                        dd.Fields = ParseFields(defFieldsSeq);
                    schema.Definitions[defName] = dd;
                }
            }
        }

        if (root.TryGetValue("ports", out var ports) && ports is YamlMappingNode portsMap)
        {
            schema.Ports = new Dictionary<string, PortDef>();
            foreach (var kv in portsMap.Children)
            {
                var portKey = Scalar(kv.Key);
                if (kv.Value is YamlMappingNode portMap)
                {
                    var pd = new PortDef();
                    if (portMap.TryGetValue("direction", out var dir))
                        pd.Direction = Scalar(dir);
                    if (portMap.TryGetValue("description", out var pdesc))
                        pd.Description = Scalar(pdesc);
                    if (portMap.TryGetValue("fields", out var pf) && pf is YamlSequenceNode pfSeq)
                        pd.Fields = ParseFields(pfSeq);
                    schema.Ports[portKey] = pd;
                }
            }
        }

        return schema;
    }

    /// <summary>
    /// PS-079, PS-391: a bytes format is one of four, and a separator applies to the two
    /// hex ones. `hex:lower` and any other spelling fell through to lowercase hex.
    /// </summary>
    static void CheckBytesFormat(YamlMappingNode fm)
    {
        if (!fm.TryGetValue("type", out var t) || Scalar(t) != "bytes") return;
        var format = fm.TryGetValue("format", out var f) ? Scalar(f) : "hex";
        var name = fm.TryGetValue("name", out var n) ? Scalar(n) : "?";
        if (format is not ("hex" or "hex:upper" or "base64" or "array"))
            throw new InvalidOperationException(
                $"Field '{name}': bytes format {format} is not one of hex, hex:upper, base64, array (PS-079)");
        if (fm.Children.ContainsKey(new YamlScalarNode("separator")) && format is not ("hex" or "hex:upper"))
            throw new InvalidOperationException(
                $"Field '{name}': `separator` applies only to the hex formats, not {format} (PS-391)");
    }

    /// <summary>
    /// The bits a byte_group member covers, counted from the group's first bit (most
    /// significant bit of its first byte), or null for a member that is neither a bit range
    /// nor a bool. Mapping onto the group lets members of different widths be compared.
    /// </summary>
    static HashSet<int>? ByteGroupMemberBits(YamlMappingNode member)
    {
        var type = member.TryGetValue("type", out var t) ? Scalar(t) : "";
        int width, start, end;
        var m = System.Text.RegularExpressions.Regex.Match(type, @"^u(\d+)\[(\d+):(\d+)\]$");
        if (m.Success)
        {
            width = int.Parse(m.Groups[1].Value);
            start = int.Parse(m.Groups[2].Value);
            end = int.Parse(m.Groups[3].Value);
        }
        else if (type == "bool")
        {
            width = 8;
            start = member.TryGetValue("bit", out var b) ? (int)Double(b) : 0;
            end = start;
        }
        else return null;
        var bits = new HashSet<int>();
        for (int i = start; i <= end; i++) bits.Add(width - 1 - i);
        return bits;
    }

    /// <summary>PS-397: bit ranges within one byte_group must not overlap. They were accepted.</summary>
    static void CheckByteGroupOverlap(YamlNode group)
    {
        var members = group as YamlSequenceNode
            ?? ((group as YamlMappingNode)?.TryGetValue("fields", out var f) == true ? f as YamlSequenceNode : null);
        if (members == null) return;
        var seen = new List<(string name, HashSet<int> bits)>();
        foreach (var raw in members.Children)
        {
            if (raw is not YamlMappingNode member) continue;
            var bits = ByteGroupMemberBits(member);
            if (bits == null) continue;
            var name = member.TryGetValue("name", out var n) ? Scalar(n) : "?";
            foreach (var other in seen)
                if (other.bits.Overlaps(bits))
                    throw new InvalidOperationException(
                        $"byte_group members '{other.name}' and '{name}' overlap (PS-397)");
            seen.Add((name, bits));
        }
    }

    /// <summary>
    /// PS-018: a port key is 1 to 255 (CR-2026-041). PS-335, PS-337: a top-level fPort is
    /// such a port, and beside `ports` every key equals it (CR-2026-042). The top-level key
    /// selects nothing (PS-336) and is not read again.
    /// </summary>
    static void CheckPortDeclarations(YamlMappingNode root)
    {
        var portKeys = root.TryGetValue("ports", out var portsNode) && portsNode is YamlMappingNode portsMap
            ? portsMap.Children.Keys.Select(Scalar).ToList() : new List<string>();
        foreach (var key in portKeys)
        {
            if (key == "default") continue;
            if (!int.TryParse(key, out var n) || n < 1 || n > 255)
                throw new InvalidOperationException(
                    $"ports.{key}: a port key must be an integer from 1 to 255 or default (PS-018)");
        }
        var name = root.Children.ContainsKey(new YamlScalarNode("fPort")) ? "fPort" : "fport";
        if (!root.TryGetValue(name, out var declaredNode)) return;
        var declared = Scalar(declaredNode);
        if (!int.TryParse(declared, out var port) || port < 1 || port > 255)
            throw new InvalidOperationException(
                $"top-level {name} must be an integer from 1 to 255, got {declared} (PS-335)");
        foreach (var key in portKeys)
            if (key != port.ToString())
                throw new InvalidOperationException(
                    $"top-level {name} is {port} but ports also declares {key}; every ports key must equal it (PS-337)");
    }

    static readonly string[] UnsignedTypes =
        { "u8", "u16", "u24", "u32", "u64", "uint8", "uint16", "uint24", "uint32", "uint64" };

    /// <summary>
    /// PS-426: match_value is withdrawn. PS-422: `encoding` is a named code on a uN. PS-430:
    /// a bitfield_string part is decimal, hex or hex:upper. PS-364: a byte_group member
    /// declares no endian.
    /// </summary>
    /// <summary>PS-407: a ${value} lookup default reports a string, so every label must be one.</summary>
    static void CheckLookupTemplate(YamlMappingNode fm)
    {
        if (!fm.TryGetValue("lookup", out var node) || node is not YamlMappingNode lookup) return;
        var fallback = lookup.Children.FirstOrDefault(kv => Scalar(kv.Key) == "default").Value;
        if (fallback is not YamlScalarNode s || !(s.Value ?? "").Contains(Wave5.ValueToken)) return;
        int numeric = lookup.Children.Count(kv => Scalar(kv.Key) != "default"
            && !(kv.Value is YamlScalarNode label
                 && (label.Style != YamlDotNet.Core.ScalarStyle.Plain || ParseScalarValue(label) is string)));
        if (numeric > 0)
        {
            var name = fm.TryGetValue("name", out var n) ? Scalar(n) : "?";
            throw new InvalidOperationException($"Field '{name}': a lookup whose default carries "
                + $"${{value}} reports a string, so its labels must be strings; {numeric} are not (PS-407)");
        }
    }

    static void CheckWave4(YamlMappingNode fm)
    {
        var name = fm.TryGetValue("name", out var n) ? Scalar(n) : "?";
        var type = fm.TryGetValue("type", out var t) ? Scalar(t) : "";
        if (fm.Children.ContainsKey(new YamlScalarNode("match_value")))
            throw new InvalidOperationException($"Field '{name}': match_value is withdrawn; write a signed "
                + "type (sN), a signed bit range (sN[start:end]), encoding, match or guard instead (PS-426)");
        if (fm.TryGetValue("encoding", out var enc))
        {
            if (Scalar(enc) is not ("sign_magnitude" or "bcd" or "gray"))
                throw new InvalidOperationException($"Field '{name}': encoding {Scalar(enc)} is not one of "
                    + "sign_magnitude, bcd, gray (PS-422)");
            if (!UnsignedTypes.Contains(type))
                throw new InvalidOperationException($"Field '{name}': encoding applies only to an unsigned "
                    + $"integer type uN, not {type} (PS-422)");
        }
        if (type == "bitfield_string" && fm.TryGetValue("parts", out var partsNode) && partsNode is YamlSequenceNode parts)
            foreach (var part in parts.Children.OfType<YamlSequenceNode>())
                if (part.Children.Count > 2 && Scalar(part.Children[2]) is not ("decimal" or "hex" or "hex:upper"))
                    throw new InvalidOperationException($"Field '{name}': bitfield_string part format "
                        + $"{Scalar(part.Children[2])} is not one of decimal, hex, hex:upper (PS-430)");
        if (fm.TryGetValue("byte_group", out var group))
        {
            var members = group as YamlSequenceNode
                ?? ((group as YamlMappingNode)?.TryGetValue("fields", out var f) == true ? f as YamlSequenceNode : null);
            foreach (var member in members?.Children.OfType<YamlMappingNode>() ?? Enumerable.Empty<YamlMappingNode>())
                if (member.Children.ContainsKey(new YamlScalarNode("endian")))
                    throw new InvalidOperationException("byte_group member declares endian; the group's byte "
                        + "order is declared on the group (PS-364)");
        }
    }

    /// <summary>PS-358: a literal's value matches its type, and it carries no arithmetic.</summary>
    static void CheckLiteral(YamlMappingNode fm)
    {
        if (!fm.TryGetValue("value", out var valueNode) || !fm.TryGetValue("type", out var t)) return;
        var type = Scalar(t);
        if (type is not ("string" or "number")) return;
        var name = fm.TryGetValue("name", out var n) ? Scalar(n) : "?";
        var scalar = valueNode as YamlScalarNode;
        var quoted = scalar?.Style is YamlDotNet.Core.ScalarStyle.SingleQuoted or YamlDotNet.Core.ScalarStyle.DoubleQuoted;
        var numeric = scalar != null && !quoted && double.TryParse(scalar.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out _);
        if (type == "string" && (scalar == null || (numeric && !quoted) || scalar.Value is "true" or "false"))
            throw new InvalidOperationException($"Field '{name}': a string literal's value must be a string (PS-358)");
        if (type == "number" && !numeric)
            throw new InvalidOperationException($"Field '{name}': a number literal's value must be a number (PS-358)");
        foreach (var key in new[] { "ref", "polynomial", "compute", "lookup", "transform", "mult", "div", "add" })
            if (fm.Children.ContainsKey(new YamlScalarNode(key)))
                throw new InvalidOperationException($"Field '{name}': a literal must not declare {key} (PS-358)");
    }

    /// <summary>
    /// The operations a stage may hold: PS-098's arithmetic, the PS-115 table, and an
    /// `op:` stage. `decimals` and `ties` are parameters of an `op:` stage, part of its one
    /// operation rather than operations of their own (PS-452).
    /// </summary>
    static readonly string[] TransformOperations =
        { "add", "mult", "div", "sqrt", "abs", "pow", "floor", "ceiling", "clamp", "log10", "log", "op" };
    static readonly string[] TransformOpParameters = { "decimals", "ties" };

    /// <summary>
    /// PS-390 and PS-452: each stage holds exactly one operation the language defines.
    /// `{round: n}` and `{sub: n}` were accepted - the first doing nothing, the second
    /// read by C# alone. A stage holding two operations was decoded, each implementation
    /// in its own reading: here `{add: 1, mult: 2}` ran mult then add, and
    /// `{op: round, add: 1}` rounded and dropped the add. Refused at load instead.
    /// </summary>
    static void CheckStage(YamlNode node, int index, string fieldName)
    {
        var at = $"Field '{fieldName}': transform stage {index}";
        if (node is not YamlMappingNode stage)
            throw new InvalidOperationException($"{at} is {Scalar(node)}, not a mapping holding one operation (PS-452)");
        var keys = stage.Children.Keys.Select(Scalar).ToList();
        if (keys.Contains("round"))
            throw new InvalidOperationException(at + ": `{round: n}` is not a transform stage; "
                + "write {op: round, decimals: n} (PS-390)");
        var unknown = keys.Where(k => !TransformOperations.Contains(k) && !TransformOpParameters.Contains(k)).ToList();
        if (unknown.Count > 0)
            throw new InvalidOperationException(
                $"{at} names no operation of the PS-115 table: {string.Join(", ", unknown)} (PS-390)");
        var operations = keys.Where(TransformOperations.Contains).ToList();
        if (operations.Count != 1)
            throw new InvalidOperationException($"{at} must hold exactly one operation, and holds "
                + (operations.Count == 0 ? "none" : string.Join(", ", operations)) + " (PS-452)");
        if (stage.TryGetValue("op", out var op))
        {
            if (Scalar(op) != "round")
                throw new InvalidOperationException($"{at} names an unknown operation {Scalar(op)} (PS-390)");
            if (stage.TryGetValue("ties", out var ties) && Scalar(ties) is not ("even" or "away"))
                throw new InvalidOperationException(at + ": round `ties` must be even or away (PS-390)");
        }
        else if (keys.Any(TransformOpParameters.Contains))
            throw new InvalidOperationException(at + ": `decimals` and `ties` belong to an `op:` stage (PS-452)");
    }

    /// <summary>
    /// PS-445: a `guard` belongs to a computed field - `type: number` or `integer` with
    /// `ref` or `compute`. On a field read from the payload it was ignored with success,
    /// so a schema meaning "no reading below this" decoded every value; on a literal it
    /// replaced the constant. `sentinel` and `out_of_range: omit` say that for a read value.
    /// Checked on list members, so it reaches every construct body's fields.
    /// </summary>
    static void CheckGuard(YamlMappingNode fm)
    {
        if (!fm.Children.ContainsKey(new YamlScalarNode("guard"))) return;
        var type = fm.TryGetValue("type", out var t) ? Scalar(t) : "";
        bool computed = type is "number" or "integer"
            && (fm.Children.ContainsKey(new YamlScalarNode("ref"))
                || fm.Children.ContainsKey(new YamlScalarNode("compute")));
        if (computed) return;
        var name = fm.TryGetValue("name", out var n) ? Scalar(n) : "?";
        throw new InvalidOperationException($"Field '{name}': a guard is declared only on a computed "
            + "field (ref or compute); for a value read from the payload use sentinel or "
            + "out_of_range: omit (PS-445)");
    }

    /// <summary>The keys that make a field a construct, declaring no type of its own.</summary>
    static readonly string[] ConstructKeys = { "$ref", "flagged", "tlv", "byte_group", "match" };

    static List<SchemaField> ParseFields(YamlSequenceNode seq)
    {
        var fields = new List<SchemaField>();
        foreach (var item in seq.Children)
        {
            if (item is not YamlMappingNode fieldMap)
                continue;
            // PS-466 (CR-2026-074): the `object:` key is withdrawn; a nested group is
            // `type: object`.
            if (fieldMap.TryGetValue("object", out var objectName) && !fieldMap.Children.ContainsKey(new YamlScalarNode("type")))
                throw new InvalidOperationException("the `object:` key is withdrawn; write `type: object` "
                    + $"with `name: {Scalar(objectName)}` and `fields` (PS-466)");
            // PS-334: a field carrying no construct needs a type; none is supplied.
            // Checked on list members, because ParseField also parses construct bodies.
            var hasType = fieldMap.TryGetValue("type", out var typeNode)
                && !string.IsNullOrWhiteSpace(Scalar(typeNode));
            if (!hasType && !ConstructKeys.Any(key => fieldMap.Children.ContainsKey(new YamlScalarNode(key))))
            {
                var name = fieldMap.TryGetValue("name", out var n) ? Scalar(n) : "?";
                throw new InvalidOperationException($"Field '{name}' declares no type");
            }
            // CR-2026-037: an unknown type is rejected when the schema is loaded, naming
            // the field and the spelling (PS-327, PS-328).
            if (hasType)
            {
                var spelling = Scalar(typeNode!);
                // A bit range is uN[start:end]; its base carries no prefix (PS-053a).
                var isBitRange = System.Text.RegularExpressions.Regex.IsMatch(spelling, @"^[us]\d+\[\d+:\d+\]$");
                if (!isBitRange && Helpers.ParseFieldType(spelling) == FieldType.Unknown)
                {
                    var name = fieldMap.TryGetValue("name", out var n) ? Scalar(n) : "?";
                    throw new InvalidOperationException($"Field '{name}': unknown type: {spelling}");
                }
            }
            CheckBytesFormat(fieldMap);
            CheckLiteral(fieldMap);
            CheckWave4(fieldMap);
            CheckLookupTemplate(fieldMap);    // PS-407
            if (fieldMap.TryGetValue("byte_group", out var group))
                CheckByteGroupOverlap(group);
            // PS-399, PS-416: exactly one discriminator source. With both `field` and
            // `length`, `field` won and the `length` byte was left unread, misaligning
            // every later field. PS-414: `remaining` is only ever true.
            if (fieldMap.TryGetValue("match", out var matchNode) && matchNode is YamlMappingNode matchMap)
            {
                var sources = new[] { "field", "length", "remaining" }
                    .Count(key => matchMap.Children.ContainsKey(new YamlScalarNode(key)));
                if (sources != 1)
                    throw new InvalidOperationException("a match must declare exactly one of 'field', "
                        + "'length' and 'remaining' (PS-399, PS-416)");
                if (matchMap.TryGetValue("remaining", out var remainingNode)
                    && !(remainingNode is YamlScalarNode { Style: YamlDotNet.Core.ScalarStyle.Plain } rs
                         && rs.Value is "true" or "True"))
                    throw new InvalidOperationException("a match's remaining must be true (PS-414)");
            }
            CheckFieldRules(fieldMap);    // PS-427, PS-428
            CheckGuard(fieldMap);         // PS-445
            fields.Add(ParseField(fieldMap));
        }
        return fields;
    }

    static SchemaField ParseField(YamlMappingNode fm)
    {
        var f = new SchemaField();

        if (fm.TryGetValue("name", out var name))
            f.Name = Scalar(name);

        if (fm.TryGetValue("type", out var typeNode))
        {
            f.RawType = Scalar(typeNode);
            // Byte order is declared with `endian`, never in the type name: the le_/be_
            // prefixes are withdrawn and rejected (PS-053a) by ParseFieldType returning
            // Unknown for them.
            var baseType = f.RawType;

            var bitRange = Helpers.ParseBitRange(f.RawType);
            if (bitRange != null)
            {
                f.BitOffset = bitRange.Value.start;
                f.BitCount = bitRange.Value.end - bitRange.Value.start + 1;
                // The base width is part of the type: u24[4:23] takes bits 4-23 of a
                // 24-bit big-endian value, so all three bytes are read before masking.
                var digits = new string(baseType.TakeWhile(char.IsDigit).ToArray());
                if (baseType.Length > 1)
                    digits = new string(baseType[1..].TakeWhile(char.IsDigit).ToArray());
                f.BitBaseBytes = int.TryParse(digits, out var wide) && wide >= 8 ? wide / 8 : 1;
            }

            f.Type = Helpers.ParseFieldType(f.RawType);
            // The inline `tlv:` block arrives here as a synthetic map typed `tlv`. No
            // schema spells that as a type; ParseFields rejects it on a list member.
            if (f.Type == FieldType.Unknown && f.RawType == "tlv")
                f.Type = FieldType.TLV;
            // `integer` is `number` declaring an integer result (PS-283).
            if (f.Type == FieldType.Integer)
            {
                f.Type = FieldType.Number;
                f.IntegerResult = true;
            }
            if (bitRange != null)
            {
                // ParseFieldType strips the range, so `u8[0:0]` resolved to U8 and the
                // whole byte was read instead of the bits - a packed flag byte decoded
                // as its raw value. A range makes this a bit field.
                f.Type = FieldType.Bits;
                f.SignedBits = f.RawType.StartsWith('s');
            }
        }

        if (fm.TryGetValue("length", out var len))
        {
            // `length: remaining` consumes to the end of the payload (PS-014), carried
            // as a negative sentinel. Int() would return its 0 default for the word and
            // the field would silently read a single byte.
            var lengthText = Scalar(len).Trim();
            if (string.Equals(lengthText, "remaining", StringComparison.OrdinalIgnoreCase))
                f.Length = -1;
            else if (System.Text.RegularExpressions.Regex.IsMatch(lengthText, @"^-?\d+$")
                     || lengthText.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
                f.Length = Int(len);
            else
                // PS-464: the name of a preceding field, with or without `$`, resolved
                // when the field is decoded (PS-465). Int() read it as 0 bytes.
                f.LengthRef = lengthText.StartsWith('$') ? lengthText[1..] : lengthText;
        }
        // Wave 6b (CR-2026-059, -065): optional, sentinel and out_of_range.
        if (fm.TryGetValue("optional", out var optionalNode))
            f.Optional = Scalar(optionalNode) is "true" or "True";
        if (fm.TryGetValue("sentinel", out var sentinelNode) && sentinelNode is YamlSequenceNode sentinelSeq)
            f.Sentinel = sentinelSeq.Children.Select(n => YamlInt(n)!.Value).ToList();
        if (fm.TryGetValue("out_of_range", out var oorNode))
            f.OutOfRangeOmit = Scalar(oorNode) == "omit";
        if (fm.TryGetValue("endian", out var endian))
            f.Endian = Scalar(endian);

        // Modifier key order is deliberately not tracked: the canonical order
        // (mult, div, add) applies however the source was written (PS-101).
        if (fm.TryGetValue("add", out var addV))
            f.Add = Double(addV);
        if (fm.TryGetValue("mult", out var multV))
            f.Mult = Double(multV);
        if (fm.TryGetValue("div", out var divV))
            f.Div = Double(divV);

        // Transform array
        if (fm.TryGetValue("transform", out var transformNode))
        {
            if (transformNode is not YamlSequenceNode transformSeq)
                throw new InvalidOperationException(
                    $"Field '{f.Name}': `transform` must be a list of stages (PS-102)");
            for (int index = 0; index < transformSeq.Children.Count; index++)
            {
                // Every stage holds exactly one operation (PS-452), checked before it is read.
                CheckStage(transformSeq.Children[index], index, f.Name);
                if (transformSeq.Children[index] is YamlMappingNode tMap)
                {
                    var stage = new TransformStage();
                    if (tMap.TryGetValue("add", out var ta)) stage.Add = Double(ta);
                    if (tMap.TryGetValue("mult", out var tm)) stage.Mult = Double(tm);
                    if (tMap.TryGetValue("div", out var td)) stage.Div = Double(td);
                    // {op: round, decimals: N}. Unparsed until now, so a schema
                    // rounding its output reported the unrounded value instead.
                    if (tMap.TryGetValue("op", out var top)) stage.Op = Scalar(top);
                    if (tMap.TryGetValue("decimals", out var tdec)) stage.Decimals = (int)Double(tdec);
                    // Unary maths stages. dl-blg's thermistor needs a natural log
                    // and a cube, and had no way to say so in this implementation.
                    if (tMap.TryGetValue("sqrt", out var tsq)) stage.Sqrt = Flag(tsq);
                    if (tMap.TryGetValue("abs", out var tab)) stage.Abs = Flag(tab);
                    if (tMap.TryGetValue("log10", out var tl10)) stage.Log10 = Flag(tl10);
                    if (tMap.TryGetValue("log", out var tlog)) stage.Log = Flag(tlog);
                    if (tMap.TryGetValue("pow", out var tpow)) stage.Pow = Double(tpow);
                    if (tMap.TryGetValue("floor", out var tfl)) stage.Floor = Double(tfl);
                    if (tMap.TryGetValue("ceiling", out var tce)) stage.Ceiling = Double(tce);
                    if (tMap.TryGetValue("clamp", out var tcl) && tcl is YamlSequenceNode clampSeq
                        && clampSeq.Children.Count == 2)
                        stage.Clamp = new[] { Double(clampSeq.Children[0]), Double(clampSeq.Children[1]) };
                    if (tMap.TryGetValue("ties", out var tties)) stage.Ties = Scalar(tties);
                    f.Transform.Add(stage);
                }
            }
        }

        if (fm.TryGetValue("var", out var varNode))
            f.Var = Scalar(varNode);
        if (fm.TryGetValue("on", out var onNode))
            f.On = Scalar(onNode);
        if (fm.TryGetValue("value", out var valueNode))
            f.Value = ParseScalarValue(valueNode);

        // Lookup table
        if (fm.TryGetValue("lookup", out var lookupNode))
        {
            if (lookupNode is YamlMappingNode lookupMap)
            {
                f.Lookup = new Dictionary<int, string>();
                foreach (var kv in lookupMap.Children)
                {
                    var keyText = Scalar(kv.Key);
                    if (keyText == "default")
                        f.LookupDefault = Scalar(kv.Value);
                    // int.TryParse does not read `0x01`, so a table written in hex
                    // parsed to no entries at all and the field reported its raw
                    // integer - or, with no default, nothing (PS-269).
                    else if (ParseScalarValue(kv.Key) is int key)
                        f.Lookup[key] = Scalar(kv.Value);
                }
            }
            else if (lookupNode is YamlSequenceNode lookupArr)
            {
                f.Lookup = new Dictionary<int, string>();
                // A sequence is indexed from zero and keeps the raw value when out
                // of range (PS-104), unlike a mapping which omits (PS-269).
                f.LookupIsSequence = true;
                for (int i = 0; i < lookupArr.Children.Count; i++)
                    f.Lookup[i] = Scalar(lookupArr.Children[i]);
            }
        }

        if (fm.TryGetValue("name_from", out var nameFromNode))
            f.NameFrom = Scalar(nameFromNode);

        // Nested fields
        if (fm.TryGetValue("fields", out var fieldsNode) && fieldsNode is YamlSequenceNode fieldsSeq)
            f.Fields = ParseFields(fieldsSeq);

        // Match cases (array format)
        if (fm.TryGetValue("cases", out var casesNode))
        {
            if (f.Type == FieldType.TLV)
            {
                if (casesNode is YamlMappingNode tlvCasesMap)
                {
                    f.TLVCases = new Dictionary<string, List<SchemaField>>();
                    foreach (var kv in tlvCasesMap.Children)
                    {
                        var rawKey = Scalar(kv.Key);
                        // Normalize hex keys: "0x01" -> "1", "[1, 117]" stays as-is
                        string caseKey;
                        if (rawKey.StartsWith("0x", StringComparison.OrdinalIgnoreCase) &&
                            int.TryParse(rawKey[2..], System.Globalization.NumberStyles.HexNumber, null, out int hexVal))
                            caseKey = hexVal.ToString();
                        else
                            caseKey = rawKey;
                        if (kv.Value is YamlSequenceNode caseFieldsSeq)
                            f.TLVCases[caseKey] = ParseFields(caseFieldsSeq);
                    }
                }
            }
            else if (casesNode is YamlSequenceNode casesSeq)
            {
                foreach (var cItem in casesSeq.Children)
                {
                    if (cItem is YamlMappingNode cMap)
                    {
                        var c = new MatchCase();
                        if (cMap.TryGetValue("case", out var cv))
                            c.CaseValue = ParseScalarValue(cv);
                        else if (cMap.TryGetValue("match", out var mv))
                            c.CaseValue = ParseScalarValue(mv);
                        if (cMap.TryGetValue("default", out var dv))
                            c.IsDefault = Scalar(dv) == "true" || Scalar(dv) == "True";
                        if (cMap.TryGetValue("fields", out var cf) && cf is YamlSequenceNode cfSeq)
                            c.Fields = ParseFields(cfSeq);
                        f.Cases.Add(c);
                    }
                }
            }
            else if (casesNode is YamlMappingNode casesMap)
            {
                // Option B: map-style cases for match. The key goes through
                // ParseScalarValue because int.TryParse does not read `0x00`, so a
                // schema writing its cases in hex kept them as strings and no case
                // ever matched the integer being switched on.
                foreach (var kv in casesMap.Children)
                {
                    var c = new MatchCase();
                    c.CaseValue = ParseScalarValue(kv.Key);
                    if (kv.Value is YamlSequenceNode cfSeq)
                        c.Fields = ParseFields(cfSeq);
                    f.Cases.Add(c);
                }
            }
        }

        // TLV fields
        if (fm.TryGetValue("tag_size", out var ts2)) f.TagSize = Int(ts2);
        if (fm.TryGetValue("length_size", out var ls)) f.LengthSize = Int(ls);
        if (fm.TryGetValue("tag_fields", out var tf) && tf is YamlSequenceNode tfSeq2)
            f.TagFields = ParseFields(tfSeq2);
        if (fm.TryGetValue("tag_key", out var tk)) f.TagKey = ParseScalarValue(tk);
        if (fm.TryGetValue("merge", out var mg)) f.Merge = Scalar(mg) == "true" || Scalar(mg) == "True";
        if (fm.TryGetValue("unknown", out var uk)) f.UnknownMode = Scalar(uk);

        // Repeat
        if (fm.TryGetValue("count", out var cnt))
        {
            var cntStr = Scalar(cnt);
            f.Count = int.TryParse(cntStr, out int ci) ? (object)ci : cntStr;
        }
        if (fm.TryGetValue("byte_length", out var bl))
        {
            var blStr = Scalar(bl);
            f.ByteLength = int.TryParse(blStr, out int bli) ? (object)bli : blStr;
        }
        if (fm.TryGetValue("until", out var until)) f.Until = Scalar(until);
        if (fm.TryGetValue("max", out var maxV)) f.Max = Int(maxV);
        if (fm.TryGetValue("min", out var minV)) f.Min = Int(minV);
        // The repeat iterator (CR-2026-048, -053, -054, -055). Their schema rules are
        // checked over the whole document by CheckIterator once the fields are parsed.
        if (fm.TryGetValue("index", out var indexNode)) f.Index = Scalar(indexNode);
        if (fm.TryGetValue("count_as", out var countAsNode)) f.CountAs = Scalar(countAsNode);
        if (fm.TryGetValue("identity", out var identityNode)) f.Identity = Scalar(identityNode);
        if (fm.TryGetValue("reserve", out var reserveNode)) f.Reserve = Int(reserveNode);
        if (fm.TryGetValue("trailer", out var trailerNode) && trailerNode is YamlSequenceNode trailerSeq)
            f.Trailer = ParseFields(trailerSeq);
        if (fm.TryGetValue("present_if", out var presentNode) && presentNode is YamlMappingNode presentMap)
            f.PresentIf = ParseGuardCondition(presentMap);
        if (fm.TryGetValue("carry", out var carryNode))
        {
            var carry = ParseScalarValue(carryNode);
            f.Carry = carry is int ci2 ? (double)ci2 : carry;
        }

        // Bytes format
        if (fm.TryGetValue("format", out var fmt)) f.Format = Scalar(fmt);
        if (fm.TryGetValue("separator", out var sep)) f.Separator = Scalar(sep);

        // Bool
        if (fm.TryGetValue("bit", out var bitNode)) f.Bit = Int(bitNode);
        if (fm.TryGetValue("consume", out var cons)) f.Consume = Int(cons);

        // Enum
        if (fm.TryGetValue("base", out var baseNode)) f.Base = Scalar(baseNode);
        if (fm.TryGetValue("values", out var valuesNode) && valuesNode is YamlMappingNode valuesMap)
        {
            f.Values = new Dictionary<int, string>();
            foreach (var kv in valuesMap.Children)
            {
                if (!int.TryParse(Scalar(kv.Key), out int vk))
                    continue;
                // The description form reports its `name` (PS-394). Scalar() of the
                // mapping reported a stringified node as the value.
                if (kv.Value is YamlMappingNode entry && entry.TryGetValue("name", out var entryName))
                    f.Values[vk] = Scalar(entryName);
                else
                    f.Values[vk] = Scalar(kv.Value);
            }
            // The value an unmapped enum reports (PS-068), read only alongside
            // `values` so it is not confused with a lookup's default.
            if (fm.TryGetValue("default", out var enumDefault))
                f.EnumDefault = Scalar(enumDefault);
        }

        // Byte group
        if (fm.TryGetValue("encoding", out var encodingNode)) f.Encoding = Scalar(encodingNode);
        if (fm.TryGetValue("byte_group", out var bgNode))
        {
            if (bgNode is YamlSequenceNode bgSeq)
            {
                f.ByteGroup = ParseFields(bgSeq);
            }
            else if (bgNode is YamlMappingNode bgMap)
            {
                if (bgMap.TryGetValue("size", out var bgSize)) f.Size = Int(bgSize);
                if (bgMap.TryGetValue("endian", out var bgEndian)) f.GroupEndian = Scalar(bgEndian);
                if (bgMap.TryGetValue("fields", out var bgf) && bgf is YamlSequenceNode bgfSeq)
                    f.ByteGroup = ParseFields(bgfSeq);
            }
        }
        if (fm.TryGetValue("size", out var sizeNode)) f.Size = Int(sizeNode);

        // $ref
        if (fm.TryGetValue("$ref", out var refNode)) f.Ref2 = Scalar(refNode);

        // Bitfield string
        if (fm.TryGetValue("delimiter", out var delim)) f.Delimiter = Scalar(delim);
        if (fm.TryGetValue("prefix", out var prefix)) f.Prefix = Scalar(prefix);
        if (fm.TryGetValue("parts", out var partsNode) && partsNode is YamlSequenceNode partsSeq)
        {
            foreach (var pItem in partsSeq.Children)
            {
                if (pItem is YamlSequenceNode partArr)
                {
                    var part = new List<object>();
                    foreach (var elem in partArr.Children)
                        part.Add(ParseScalarValue(elem)!);
                    f.Parts.Add(part);
                }
            }
        }

        // Formula (deprecated)
        if (fm.TryGetValue("formula", out var formula)) f.Formula = Scalar(formula);

        // Semantic
        if (fm.TryGetValue("valid_range", out var vrNode) && vrNode is YamlSequenceNode vrSeq)
        {
            f.ValidRange = vrSeq.Children.Select(n => Double(n)).ToArray();
        }
        if (fm.TryGetValue("resolution", out var res)) f.Resolution = Double(res);
        if (fm.TryGetValue("unece", out var unece)) f.UNECE = Scalar(unece);
        if (fm.TryGetValue("unit", out var unit)) f.Unit = Scalar(unit);
        if (fm.TryGetValue("semantic", out var semId)) f.SemanticId = Scalar(semId);
        if (fm.TryGetValue("ipso", out var ipsoNode) && ipsoNode is YamlMappingNode ipsoMap)
        {
            f.Ipso = new IpsoDef();
            if (ipsoMap.TryGetValue("object", out var io)) f.Ipso.Object = Int(io);
            if (ipsoMap.TryGetValue("instance", out var ii)) f.Ipso.Instance = Int(ii);
            if (ipsoMap.TryGetValue("resource", out var ir)) f.Ipso.Resource = Int(ir);
        }
        if (fm.TryGetValue("senml", out var senmlNode) && senmlNode is YamlMappingNode senmlMap)
        {
            f.Senml = new SenmlDef();
            if (senmlMap.TryGetValue("name", out var sn)) f.Senml.Name = Scalar(sn);
            if (senmlMap.TryGetValue("unit", out var su)) f.Senml.Unit = Scalar(su);
        }

        // Computed fields
        if (fm.TryGetValue("ref", out var refVal)) f.Ref = Scalar(refVal);
        if (fm.TryGetValue("polynomial", out var polyNode) && polyNode is YamlSequenceNode polySeq)
        {
            f.Polynomial = polySeq.Children.Select(n => Double(n)).ToArray();
        }

        // Compute
        if (fm.TryGetValue("compute", out var compNode) && compNode is YamlMappingNode compMap)
        {
            var cd = new ComputeDef();
            if (compMap.TryGetValue("op", out var op)) cd.Op = Scalar(op);
            if (compMap.TryGetValue("a", out var a)) cd.A = Scalar(a);
            if (compMap.TryGetValue("b", out var b)) cd.B = Scalar(b);
            f.Compute = cd;
        }

        // Guard
        if (fm.TryGetValue("guard", out var guardNode) && guardNode is YamlMappingNode guardMap)
        {
            var gd = new GuardDef();
            if (guardMap.TryGetValue("else", out var elseVal))
                gd.ElseValue = Double(elseVal);
            if (guardMap.TryGetValue("when", out var whenNode) && whenNode is YamlSequenceNode whenSeq)
            {
                foreach (var w in whenSeq.Children)
                {
                    if (w is YamlMappingNode wm)
                        gd.When.Add(ParseGuardCondition(wm));
                }
            }
            f.Guard = gd;
        }

        // Flagged construct (inline)
        if (fm.TryGetValue("flagged", out var flaggedNode) && flaggedNode is YamlMappingNode flaggedMap)
        {
            var fd = new FlaggedDef();
            if (flaggedMap.TryGetValue("field", out var ff)) fd.Field = Scalar(ff);
            if (flaggedMap.TryGetValue("groups", out var groups) && groups is YamlSequenceNode groupsSeq)
            {
                foreach (var g in groupsSeq.Children)
                {
                    if (g is YamlMappingNode gMap)
                    {
                        var fg = new FlaggedGroup();
                        if (gMap.TryGetValue("bit", out var gBit)) fg.Bit = Int(gBit);
                        if (gMap.TryGetValue("fields", out var gf) && gf is YamlSequenceNode gfSeq)
                            fg.Fields = ParseFields(gfSeq);
                        fd.Groups.Add(fg);
                    }
                }
            }
            f.Flagged = fd;
        }

        // TLV inline -- set type first so cases are parsed as TLV cases
        if (fm.TryGetValue("tlv", out var tlvNode) && tlvNode is YamlMappingNode tlvMap)
        {
            // Temporarily inject type so ParseField parses cases as TLV
            var syntheticMap = new YamlMappingNode();
            syntheticMap.Add("type", "tlv");
            foreach (var kv in tlvMap.Children)
                syntheticMap.Add(kv.Key, kv.Value);
            var tlvField = ParseField(syntheticMap);
            tlvField.Type = FieldType.TLV;
            f.TLVInline = tlvField;
        }

        // Match inline (Option B: `- match: { field: $var, cases: {...} }`)
        if (fm.TryGetValue("match", out var matchNode) && matchNode is YamlMappingNode matchMap)
        {
            var matchField = new SchemaField { Type = FieldType.Match };
            if (matchMap.TryGetValue("field", out var mf)) matchField.On = Scalar(mf);
            // The block's own `name` and `length`: a match with no `field:` reads its
            // discriminator from the payload, and both the decoder's read width and the
            // encoder's write width come from `length`. Dropping them left a two-byte
            // discriminator read as one byte.
            if (matchMap.TryGetValue("name", out var mn)) matchField.Name = Scalar(mn);
            if (matchMap.TryGetValue("length", out var ml)) matchField.Length = Int(ml);
            // PS-414: the discriminator is the number of bytes left; CheckWave6b has
            // already refused any value but true.
            if (matchMap.TryGetValue("remaining", out var mr)) matchField.MatchRemaining = Scalar(mr) is "true" or "True";
            if (matchMap.TryGetValue("var", out var mv)) matchField.Var = Scalar(mv);
            // `default`: "error", "skip", or a field list decoded when no case matches
            // (CR-2026-020). Parsed here rather than at decode time, because by then the
            // YAML nodes are gone.
            if (matchMap.TryGetValue("default", out var md))
            {
                matchField.MatchDefault = md is YamlSequenceNode mdSeq
                    ? ParseFields(mdSeq)
                    : Scalar(md);
            }
            if (matchMap.TryGetValue("cases", out var mc) && mc is YamlMappingNode mcMap)
            {
                // As above: hex keys need ParseScalarValue, not int.TryParse.
                foreach (var kv in mcMap.Children)
                {
                    var c = new MatchCase();
                    // A `default` key is the fallback, not a case whose value is the
                    // string "default" - which matched no integer, so the fallback never
                    // ran (CR-2026-020).
                    if (kv.Key is YamlScalarNode { Value: "default" })
                        c.IsDefault = true;
                    else
                        c.CaseValue = ParseScalarValue(kv.Key);
                    if (kv.Value is YamlSequenceNode cfSeq)
                        c.Fields = ParseFields(cfSeq);
                    matchField.Cases.Add(c);
                }
                // A default must be tried only after every explicit case, whatever order
                // the document lists them in; DecodeMatch returns on the first it sees.
                matchField.Cases = matchField.Cases
                    .OrderBy(entry => entry.IsDefault).ToList();
            }
            f.MatchInline = matchField;
        }

        return f;
    }

    static GuardCondition ParseGuardCondition(YamlMappingNode wm)
    {
        var gc = new GuardCondition();
        if (wm.TryGetValue("field", out var gf)) gc.Field = Scalar(gf);
        if (wm.TryGetValue("gt", out var gt)) gc.Gt = Double(gt);
        if (wm.TryGetValue("gte", out var gte)) gc.Gte = Double(gte);
        if (wm.TryGetValue("lt", out var lt)) gc.Lt = Double(lt);
        if (wm.TryGetValue("lte", out var lte)) gc.Lte = Double(lte);
        if (wm.TryGetValue("eq", out var eq)) gc.Eq = Double(eq);
        // ne was neither parsed nor evaluated, so a guard written with it could never
        // fail: vicki's _tempStandard stayed live on the firmware 3.5 path and was added
        // to _tempFw35, reporting 46.95 where the vendor gives 14.76.
        if (wm.TryGetValue("ne", out var ne)) gc.Ne = Double(ne);
        return gc;
    }

    // Helper: get scalar string value from a YAML node
    static string Scalar(YamlNode node) => node is YamlScalarNode s ? s.Value ?? "" : node.ToString() ?? "";

    static int Int(YamlNode node)
    {
        var s = Scalar(node);
        if (s.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
            return int.Parse(s[2..], NumberStyles.HexNumber);
        return int.TryParse(s, out int v) ? v : 0;
    }

    static double Double(YamlNode node)
    {
        var s = Scalar(node);
        return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out double v) ? v : 0;
    }

    /// <summary>Reads a flag written as a YAML boolean, or as 1/0 by a JSON producer.</summary>
    static bool Flag(YamlNode node)
    {
        var s = Scalar(node);
        if (bool.TryParse(s, out bool b)) return b;
        return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out double v) && v != 0;
    }

    static object? ParseScalarValue(YamlNode node)
    {
        if (node is YamlScalarNode scalar)
        {
            var s = scalar.Value ?? "";
            if (s.StartsWith("0x", StringComparison.OrdinalIgnoreCase) &&
                int.TryParse(s[2..], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out int hex))
                return hex;
            if (int.TryParse(s, out int i)) return i;
            if (double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out double d)) return d;
            if (s == "true" || s == "True") return true;
            if (s == "false" || s == "False") return false;
            return s;
        }
        if (node is YamlSequenceNode seq)
        {
            return seq.Children.Select(ParseScalarValue).ToList();
        }
        return null;
    }
}

file static class YamlMappingNodeExtensions
{
    public static bool TryGetValue(this YamlMappingNode map, string key, out YamlNode value)
    {
        foreach (var kv in map.Children)
        {
            if (kv.Key is YamlScalarNode scalar && scalar.Value == key)
            {
                value = kv.Value;
                return true;
            }
        }
        value = null!;
        return false;
    }
}
