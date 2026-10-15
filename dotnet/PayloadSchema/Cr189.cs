using System.Text.RegularExpressions;
using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-189 (PS-621 to PS-623): a repeat of single values whose elements are packed end
/// to end in `packed_bits` bits, most significant bit first, from `bit_offset` bits into the
/// byte at the read position. Mirrors packed_element_width and packed_repeat_error in
/// tools/schema_interpreter.py; the decode is DecodeRepeat's Packed and the encode
/// SchemaEncoder.EncodePacked.
/// </summary>
internal static class Cr189
{
    static readonly Regex Integer = new("^(u|s|uint|int|i)(8|16|24|32|64)$", RegexOptions.CultureInvariant);

    static YamlNode? Get(YamlMappingNode map, string key) =>
        map.Children.TryGetValue(new YamlScalarNode(key), out var v) ? v : null;

    /// <summary>The width in bits of a packed element's integer type and whether it is
    /// signed, or null for any other type.</summary>
    internal static (int width, bool signed)? Width(YamlMappingNode element)
    {
        if (Get(element, "type") is not YamlScalarNode t) return null;
        var m = Integer.Match(t.Value ?? "");
        if (!m.Success) return null;
        return (int.Parse(m.Groups[2].Value), m.Groups[1].Value is not ("u" or "uint"));
    }

    /// <summary>An integer as YAML reads one: a plain scalar, not a bool or a float.</summary>
    static long? PlainInt(YamlNode? node) =>
        node is YamlScalarNode { Style: ScalarStyle.Plain } s && long.TryParse(s.Value, out var n) ? n : null;

    /// <summary>PS-621's load rule over one field mapping, in the spec prototype's text.</summary>
    internal static void Check(YamlMappingNode fm)
    {
        var bitsNode = Get(fm, "packed_bits");
        var offsetNode = Get(fm, "bit_offset");
        if (Get(fm, "type") is not YamlScalarNode typeNode || (bitsNode == null && offsetNode == null)) return;
        var name = Get(fm, "name") is YamlScalarNode n ? n.Value : "None";
        var where = $"Repeat '{name}'";
        if (typeNode.Value != "repeat" || bitsNode == null)
        {
            var hint = typeNode.Value == "bool" ? "; did you mean `bit`?" : "";
            throw new InvalidOperationException($"Field '{name}': packed_bits and bit_offset belong to a "
                + $"repeat with `element` (PS-621){hint}");
        }
        var bits = PlainInt(bitsNode);
        if (bits is not (>= 1 and <= 32))
            throw new InvalidOperationException($"{where}: packed_bits is an integer from 1 to 32 (PS-621)");
        if (offsetNode != null && PlainInt(offsetNode) is not (>= 0 and <= 7))
            throw new InvalidOperationException($"{where}: bit_offset is an integer from 0 to 7 (PS-621)");
        var width = Get(fm, "fields") == null && Get(fm, "element") is YamlMappingNode element
            ? Width(element) : null;
        if (width is not { } w || w.width < bits)
            throw new InvalidOperationException($"{where}: a packed element is one field of an integer "
                + $"type at least {bits} bits wide (PS-621)");
        if (Get(fm, "count") == null && Get(fm, "byte_length") == null)
            throw new InvalidOperationException($"{where}: a packed repeat declares count or byte_length (PS-621)");
    }
}
