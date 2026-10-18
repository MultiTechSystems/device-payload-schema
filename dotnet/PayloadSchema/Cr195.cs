using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-195 (PS-525 amended, PS-632 to PS-634): flat lists. `on_repeat: concat` on a tlv
/// reports a name as one array however many entries report it, splicing a list value
/// (PS-632); `flatten` on a repeat with `fields` reports its element's inner repeat's
/// arrays as one list (PS-633). Both decode; a concat whose case reports a list, and any
/// flatten, are refused for encoding (PS-634). The messages are the spec prototype's.
/// </summary>
internal static class Cr195
{
    static YamlNode? Get(YamlMappingNode map, string key) =>
        map.Children.TryGetValue(new YamlScalarNode(key), out var v) ? v : null;

    static string? Text(YamlNode? node) => (node as YamlScalarNode)?.Value;

    /// <summary>One more report of a concat name: appended, its elements spliced where it is
    /// a list (PS-632).</summary>
    internal static List<object?> Concat(object? existing, object? value)
    {
        var output = existing as List<object?> ?? new List<object?>();
        if (value is System.Collections.IList list)
            foreach (var item in list) output.Add(item);
        else
            output.Add(value);
        return output;
    }

    internal static InvalidOperationException ConcatListError(string name) =>
        new($"tlv: '{name}' is concatenated from lists, which cannot be split back into entries; "
            + "the schema decodes but is not encoded (PS-634)");

    internal static InvalidOperationException FlattenError(string name) =>
        new($"Repeat '{name}' flattens its groups into one list, which cannot be split back into "
            + "groups; the schema decodes but is not encoded (PS-634)");

    /// <summary>The prototype's check_flatten for one field mapping (PS-633).</summary>
    internal static void Check(YamlMappingNode fm)
    {
        if (Get(fm, "flatten") is not { } targetNode || Get(fm, "type") == null) return;
        var where = $"Repeat '{Text(Get(fm, "name")) ?? "None"}'";
        var target = Text(targetNode);
        var fields = Text(Get(fm, "type")) == "repeat" ? Get(fm, "fields") as YamlSequenceNode : null;
        var inner = fields?.Children.OfType<YamlMappingNode>()
            .FirstOrDefault(f => target != null && Text(Get(f, "name")) == target);
        if (inner == null || Text(Get(inner, "type")) != "repeat")
            throw new InvalidOperationException($"{where}: flatten names a repeat among its element's fields (PS-633)");
        var others = fields!.Children.OfType<YamlMappingNode>()
            .Where(f => Get(f, "type") != null)
            .Select(f => Text(Get(f, "name")))
            .Where(n => n != null && n != target && !n.StartsWith("_"))
            .Select(n => $"'{n}'")
            .ToList();
        if (others.Count > 0)
            throw new InvalidOperationException($"{where}: with flatten, its element's other fields are "
                + $"internal; {string.Join(", ", others)} would be reported (PS-633)");
    }
}
