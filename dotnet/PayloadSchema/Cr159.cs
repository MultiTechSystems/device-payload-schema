using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-159: constructs withdrawn for want of any use or of any test, rejected at load
/// with what replaces them. The texts are the specification prototype's WITHDRAWN table and
/// the `min`/`max` message of its check_field, word for word.
/// </summary>
internal static class Cr159
{
    public const string Formula = "`formula` is withdrawn; use `compute`, `polynomial`, "
        + "`transform` or `guard` (CR-2026-159, PS-448)";
    public const string EncodeFormula = "`encode_formula` is withdrawn with `formula` (CR-2026-159, PS-448)";
    public const string Clamp = "`clamp` is withdrawn; use a `floor` stage and a `ceiling` stage "
        + "(CR-2026-159, PS-115)";
    public const string Confirmed = "`confirmed` is withdrawn: confirmed or unconfirmed is chosen per "
        + "transmission, not by the schema (CR-2026-159, PS-448)";

    /// <summary>The legacy `type: match`, whose discriminator was `on:`; `match:` replaces it.</summary>
    public const string TypeMatch = "`type: match` with `on:` is withdrawn; write `match: {field: $name, "
        + "cases: ...}` (Clause 4)";

    static bool Has(YamlMappingNode map, string key) => map.Children.ContainsKey(new YamlScalarNode(key));

    /// <summary>
    /// `formula`, `encode_formula`, and `min` or `max` on a field that is no repeat (they bound
    /// a repeat's element count; a value range is `valid_range`, PS-395). A transform's `clamp`
    /// is CheckStage's.
    /// </summary>
    public static void CheckField(YamlMappingNode fm)
    {
        if (fm.Children.TryGetValue(new YamlScalarNode("type"), out var matchType)
            && matchType is YamlScalarNode { Value: "match" })
            throw new InvalidOperationException(TypeMatch);
        if (Has(fm, "formula")) throw new InvalidOperationException(Formula);
        if (Has(fm, "encode_formula")) throw new InvalidOperationException(EncodeFormula);
        var type = fm.Children.TryGetValue(new YamlScalarNode("type"), out var t) && t is YamlScalarNode ts
            ? ts.Value : null;
        if (type == "repeat") return;
        var name = fm.Children.TryGetValue(new YamlScalarNode("name"), out var n) && n is YamlScalarNode ns
            ? ns.Value : "<unnamed>";
        foreach (var bound in new[] { "min", "max" })
        {
            if (Has(fm, bound))
                throw new InvalidOperationException($"`{bound}` on field '{name}' is withdrawn: it bounds a "
                    + "repeat's element count only; declare a value range with `valid_range` "
                    + "(CR-2026-159, PS-395)");
        }
    }

    /// <summary>A port's `confirmed`.</summary>
    public static void CheckPorts(YamlMappingNode root)
    {
        if (!root.Children.TryGetValue(new YamlScalarNode("ports"), out var ports)
            || ports is not YamlMappingNode portsMap) return;
        foreach (var port in portsMap.Children.Values)
            if (port is YamlMappingNode portMap && Has(portMap, "confirmed"))
                throw new InvalidOperationException(Confirmed);
    }
}
