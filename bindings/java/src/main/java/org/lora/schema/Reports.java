package org.lora.schema;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

/**
 * CR-2026-161 (PS-570, PS-571): {@code _meta} carries each entry's reported type as
 * {@code reports}, and a lookup's labels are of one type. Each method mirrors the function of
 * the same purpose in tools/schema_interpreter.py, whose wording and rules are the spec
 * prototype's ({@code reports_of}, {@code check_lookup_types}).
 */
final class Reports {
    private Reports() {}

    private static final Pattern BIT_RANGE = Pattern.compile("^[us]\\d+\\[\\d+:\\d+\\]$");
    private static final Pattern INTEGER_TYPE = Pattern.compile(
            "^(?:[usi]|uint|int)(?:8|16|24|32|64)$|^(?:u32le16|s32le16|u32be16le|s32be16le)$");
    private static final Set<String> FLOATS = Set.of("number", "f16", "bf16", "f32", "f64", "f32le16",
            "f32be16le", "uflt16", "sflt16", "sflt24", "udec", "sdec");

    /** A lookup's labels, and its {@code default} where that is not a {@code ${value}} template. */
    static List<Object> labels(Object lookup) {
        List<Object> out = new ArrayList<>();
        if (lookup instanceof List<?> list) {
            out.addAll(list);
        } else if (lookup instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if ("default".equals(String.valueOf(e.getKey())) && e.getValue() instanceof String text
                        && text.contains(Wave5.VALUE_TOKEN)) {
                    continue;
                }
                out.add(e.getValue());
            }
        }
        return out;
    }

    /** The JSON type of a lookup label: boolean, integer, number or string. */
    static String jsonKind(Object v) {
        if (v instanceof Boolean) return "boolean";
        if (v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte
                || v instanceof BigInteger) {
            return "integer";
        }
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            return d == Math.rint(d) && !Double.isInfinite(d) ? "integer" : "number";
        }
        return "string";
    }

    /** PS-570's reports_of; null where nothing declares one. */
    static String of(Map<?, ?> decl) {
        if (decl.get("_reports") instanceof String r) return r;                // a match discriminator
        if (decl.get("tlv") instanceof Map<?, ?> && !Meta.truthy(decl.get("type"))) return "array"; // channels
        Object lookup = decl.get("lookup");
        if (lookup != null) {
            if (lookup instanceof Map<?, ?> m && m.get("default") instanceof String d
                    && d.contains(Wave5.VALUE_TOKEN)) {
                return "string";
            }
            Set<String> kinds = new TreeSet<>();
            for (Object v : labels(lookup)) kinds.add(jsonKind(v));
            if (kinds.equals(Set.of("integer", "number"))) return "number";
            return kinds.size() == 1 ? kinds.iterator().next() : null;
        }
        Object declared = decl.get("type");
        String t = declared == null ? "" : String.valueOf(declared).toLowerCase();
        boolean hasValue = decl.containsKey("value");
        if (t.equals("repeat")) return "array";
        if (t.equals("object")) return "object";
        if ((t.equals("string") || t.equals("number") || t.equals("boolean")) && hasValue) return t;
        if (t.equals("bytes") && "array".equals(decl.get("format"))) return "array";
        if (t.equals("bool") || t.equals("boolean")) return "boolean";
        if (t.equals("ascii")) return Meta.truthy(decl.get("numeral")) ? "integer" : "string";
        if (t.equals("string") || t.equals("bitfield_string") || t.equals("enum") || t.equals("bytes")
                || t.equals("hex") || t.equals("base64")) {
            return "string";
        }
        for (String k : new String[] {"mult", "div", "add", "polynomial", "transform"}) {
            if (decl.containsKey(k)) return "number";
        }
        if (t.equals("integer")) return "integer";
        if (FLOATS.contains(t)) return "number";
        if (BIT_RANGE.matcher(t).matches() || INTEGER_TYPE.matcher(t).matches()) return "integer";
        return null;
    }

    /** Throws the first PS-571 violation in the header, fields, ports and definitions. */
    static void check(Map<String, Object> raw) {
        for (String key : new String[] {"header", "fields", "ports", "definitions"}) {
            walk(raw.get(key));
        }
    }

    private static void walk(Object node) {
        if (node instanceof List<?> list) {
            for (Object item : list) walk(item);
        } else if (node instanceof Map<?, ?> map) {
            if (map.containsKey("lookup") && map.get("type") != null) {
                Set<String> kinds = new TreeSet<>();
                for (Object v : labels(map.get("lookup"))) {
                    String k = jsonKind(v);
                    kinds.add(k.equals("integer") ? "number" : k);
                }
                if (kinds.size() > 1) {
                    Object name = map.get("name");
                    throw new SchemaException("Field '" + (name instanceof String ? name : "None")
                            + "': its lookup mixes " + String.join(", ", kinds)
                            + " labels; a lookup's labels are of one type (PS-571)");
                }
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!"test_vectors".equals(String.valueOf(e.getKey()))) walk(e.getValue());
            }
        }
    }
}
