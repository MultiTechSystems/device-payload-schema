package org.lora.schema;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 0.5.2 wave 6a: the repeat iterator and reserved trailers (CR-2026-048, -053, -054, -055,
 * -080, -081). The schema rules, checked once over the raw document after every
 * {@code $ref} is spliced. Each method mirrors the function of the same purpose in
 * tools/schema_interpreter.py: {@code iterator_errors}, {@code _repeat_member_ref_errors},
 * {@code tlv_reserve_errors}, {@code repeat_only_names} and {@code fixed_element_size}.
 *
 * <p>A walk of its own rather than a check in {@code parseFields}, because PS-369 needs the
 * names of every enclosing repeat and PS-381 whether a field sits directly in a repeat's
 * elements - neither is known to a check that sees one list member at a time.
 */
final class Wave6a {
    private Wave6a() {}

    private static final List<String> ITERATOR_KEYS = List.of("index", "count_as");
    private static final List<String> GUARD_OPS = List.of("gt", "gte", "lt", "lte", "eq", "ne");
    private static final List<String> COMPUTED_TYPES = List.of("number", "integer");
    private static final Pattern TEMPLATE_REF = Pattern.compile("\\$\\{([^}]+)\\}");

    /** Rejects the schema when any wave 6a rule fails, naming every failure. */
    static void checkSchema(Map<String, Object> raw) {
        List<String> errors = schemaErrors(raw);
        if (!errors.isEmpty()) {
            throw new SchemaException(String.join("; ", errors));
        }
    }

    /** iterator_errors over the top-level and port field lists, and every tlv's reserve. */
    static List<String> schemaErrors(Map<String, Object> raw) {
        List<String> errors = new ArrayList<>(iteratorErrors(raw.get("fields"), List.of(), false, "fields"));
        if (raw.get("ports") instanceof Map<?, ?> ports) {
            for (Map.Entry<?, ?> e : ports.entrySet()) {
                Object group = e.getValue() instanceof Map<?, ?> entry ? entry.get("fields") : e.getValue();
                errors.addAll(iteratorErrors(group, List.of(), false, "ports[" + e.getKey() + "].fields"));
            }
        }
        collectTlvReserveErrors(raw, errors);
        return errors;
    }

    private static boolean isInt(Object v) {
        return v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte
                || v instanceof java.math.BigInteger;
    }

    private static boolean isLiteral(Map<?, ?> f) {
        Object type = f.get("type");
        return ("string".equals(type) || "number".equals(type)) && f.containsKey("value");
    }

    /** PS-373, PS-374: a per-element unit, IPSO instance or SenML name, and what it names. */
    private static List<String> memberRefErrors(Map<?, ?> repeat, Map<?, ?> member, String where) {
        List<String> errors = new ArrayList<>();
        Map<Object, Map<?, ?>> byName = new HashMap<>();
        if (repeat.get("fields") instanceof List<?> members) {
            for (Object m : members) {
                if (m instanceof Map<?, ?> mm) byName.put(mm.get("name"), mm);
            }
        }
        Object index = repeat.get("index");
        List<String[]> refs = new ArrayList<>();
        if (member.get("unit") instanceof String unit && unit.startsWith("$")) {
            refs.add(new String[] {"unit", unit.substring(1)});
        }
        if (member.get("ipso") instanceof Map<?, ?> ipso && ipso.get("instance") instanceof String instance
                && instance.startsWith("$")) {
            refs.add(new String[] {"ipso.instance", instance.substring(1)});
        }
        if (member.get("senml") instanceof Map<?, ?> senml && senml.get("name") instanceof String name) {
            Matcher m = TEMPLATE_REF.matcher(name);
            while (m.find()) refs.add(new String[] {"senml.name", m.group(1)});
        }
        for (String[] ref : refs) {
            String key = ref[0], name = ref[1];
            if (name.equals(index)) {
                if (!(isInt(repeat.get("count")) || repeat.containsKey("max"))) {
                    errors.add(where + ": " + key + " uses the index '" + name + "', so the repeat needs a "
                            + "literal count or a max (PS-374)");
                }
                continue;
            }
            Map<?, ?> target = byName.get(name);
            if (target == null) {
                errors.add(where + ": " + key + " names '" + name + "', which is neither a field of the "
                        + "element nor the repeat's index (PS-373)");
            } else if (!(target.containsKey("lookup") || isLiteral(target))) {
                errors.add(where + ": " + key + " names '" + name + "', which must carry a lookup or be a "
                        + "literal so its values are known from the schema (PS-374)");
            }
        }
        return errors;
    }

    /**
     * The repeat iterator's schema rules: PS-350/PS-383/PS-384 (reserve and trailer), PS-369
     * (index and count_as names), PS-372 to PS-374 (identity and per-element references),
     * PS-381 (carry), and present_if's form (PS-386). Walks nested field lists so an
     * enclosing repeat's names are known.
     */
    static List<String> iteratorErrors(Object fieldsRaw, List<String> enclosing, boolean inElements,
                                       String where) {
        List<String> errors = new ArrayList<>();
        if (!(fieldsRaw instanceof List<?> fields)) return errors;
        for (int i = 0; i < fields.size(); i++) {
            if (!(fields.get(i) instanceof Map<?, ?> f)) continue;
            String at = where + "[" + i + "] (" + (f.containsKey("name") ? f.get("name") : "?") + ")";
            if (f.containsKey("carry")) {
                Object carry = f.get("carry");
                if (!inElements || !COMPUTED_TYPES.contains(f.get("type"))) {
                    errors.add(at + ": carry applies only to a computed field declared in a "
                            + "repeat's elements (PS-381)");
                } else if (!(carry instanceof Number || (carry instanceof String s && s.startsWith("$")))) {
                    errors.add(at + ": carry must be a numeric literal or a $ reference to a "
                            + "field decoded before the repeat (PS-381)");
                }
            }
            if ("repeat".equals(f.get("type"))) {
                List<?> members = f.get("fields") instanceof List<?> l ? l : List.of();
                Set<Object> names = new HashSet<>();
                for (Object m : members) {
                    if (m instanceof Map<?, ?> mm) names.add(mm.get("name"));
                }
                List<String> own = new ArrayList<>();
                for (String key : ITERATOR_KEYS) {
                    Object value = f.get(key);
                    if (value == null) continue;
                    if (!(value instanceof String s) || s.isEmpty()) {
                        errors.add(at + ": " + key + " must be a name (PS-366, PS-367)");
                        continue;
                    }
                    if (names.contains(s) || enclosing.contains(s) || own.contains(s)) {
                        errors.add(at + ": " + key + " '" + s + "' names a field of the element, or an "
                                + "index or count_as already in scope (PS-369)");
                    }
                    own.add(s);
                }
                if (f.containsKey("reserve")) {
                    Object reserve = f.get("reserve");
                    if (!"end".equals(f.get("until"))) {
                        errors.add(at + ": reserve applies only to a repeat with until: end (PS-350)");
                    } else if (!isInt(reserve) || ((Number) reserve).longValue() < 0) {
                        errors.add(at + ": reserve must be a non-negative integer (PS-350)");
                    }
                }
                if (f.containsKey("trailer")) {
                    Object trailer = f.get("trailer");
                    Integer size = trailer instanceof List<?> tl ? fixedElementSize(tl) : null;
                    if (!f.containsKey("reserve")) {
                        errors.add(at + ": trailer needs reserve (PS-383)");
                    } else if (size == null) {
                        errors.add(at + ": every trailer field needs a size known from the schema (PS-384)");
                    } else if (!(isInt(f.get("reserve")) && ((Number) f.get("reserve")).longValue() == size)) {
                        errors.add(at + ": the trailer's fields take " + size + " byte(s), and reserve is "
                                + f.get("reserve") + "; they must be equal (PS-384)");
                    }
                }
                Object presentIf = f.get("present_if");
                if (presentIf != null && !(presentIf instanceof Map<?, ?> p
                        && p.get("field") instanceof String
                        && GUARD_OPS.stream().anyMatch(p::containsKey))) {
                    errors.add(at + ": present_if must be one guard condition, {field: $name, <op>: value} "
                            + "(PS-386)");
                }
                Object identity = f.get("identity");
                if (identity != null && !(identity.equals(f.get("index"))
                        || (identity instanceof String id && id.startsWith("$") && names.contains(id.substring(1))))) {
                    errors.add(at + ": identity must be the repeat's index or a $ reference to a field "
                            + "of its elements (PS-372)");
                }
                for (int j = 0; j < members.size(); j++) {
                    if (members.get(j) instanceof Map<?, ?> member) {
                        errors.addAll(memberRefErrors(f, member, at + ".fields[" + j + "] ("
                                + (member.containsKey("name") ? member.get("name") : "?") + ")"));
                    }
                }
                List<String> deeper = new ArrayList<>(enclosing);
                deeper.addAll(own);
                errors.addAll(iteratorErrors(members, deeper, true, at + ".fields"));
                if (f.get("trailer") instanceof List<?> trailer) {
                    errors.addAll(iteratorErrors(trailer, enclosing, false, at + ".trailer"));
                }
                continue;
            }
            if (f.get("fields") instanceof List<?> nested) {
                errors.addAll(iteratorErrors(nested, enclosing, false, at + ".fields"));
            }
            for (String construct : List.of("match", "tlv", "flagged", "byte_group")) {
                if (!(f.get(construct) instanceof Map<?, ?> body)) continue;
                Collection<?> caseLists = body.get("cases") instanceof Map<?, ?> cm ? cm.values()
                        : body.get("cases") instanceof List<?> cl ? cl : List.of();
                for (Object c : caseLists) {
                    Object group = c instanceof List<?> ? c : c instanceof Map<?, ?> cmap ? cmap.get("fields") : null;
                    errors.addAll(iteratorErrors(group, enclosing, false, at + "." + construct));
                }
                if (body.get("fields") instanceof List<?> bf) {
                    errors.addAll(iteratorErrors(bf, enclosing, false, at + "." + construct));
                }
                if (body.get("groups") instanceof List<?> groups) {
                    for (Object g : groups) {
                        if (g instanceof Map<?, ?> gm) {
                            errors.addAll(iteratorErrors(gm.get("fields"), enclosing, false, at + "." + construct));
                        }
                    }
                }
            }
        }
        return errors;
    }

    /** PS-471: a tlv's reserve is a non-negative integer. Every tlv in the document. */
    private static void collectTlvReserveErrors(Object node, List<String> errors) {
        if (node instanceof Map<?, ?> map) {
            Object type = map.get("type");
            boolean typed = type != null && !"".equals(type) && !Boolean.FALSE.equals(type);
            if (map.containsKey("tlv") && !typed && map.get("tlv") instanceof Map<?, ?> body
                    && body.containsKey("reserve")) {
                Object reserve = body.get("reserve");
                if (!isInt(reserve) || ((Number) reserve).longValue() < 0) {
                    errors.add("tlv reserve must be a non-negative integer, got " + reserve + " (PS-471)");
                }
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if ("test_vectors".equals(e.getKey()) || "definitions".equals(e.getKey())) continue;
                collectTlvReserveErrors(e.getValue(), errors);
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) collectTlvReserveErrors(item, errors);
        }
    }

    /** Names declared in some repeat's elements and nowhere outside one (PS-368). */
    static Set<String> repeatOnlyNames(Map<String, Object> raw) {
        Set<String> inside = new HashSet<>();
        Set<String> outside = new HashSet<>();
        walkNames(raw, false, inside, outside);
        inside.removeAll(outside);
        return inside;
    }

    private static void walkNames(Object node, boolean inElements, Set<String> inside, Set<String> outside) {
        if (node instanceof Map<?, ?> map) {
            if (map.get("name") instanceof String name && (map.containsKey("type") || map.containsKey("fields"))) {
                (inElements ? inside : outside).add(name);
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if ("test_vectors".equals(e.getKey()) || "definitions".equals(e.getKey())) continue;
                walkNames(e.getValue(),
                        inElements || ("fields".equals(e.getKey()) && "repeat".equals(map.get("type"))),
                        inside, outside);
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) walkNames(item, inElements, inside, outside);
        }
    }

    private static final Map<String, Integer> FIXED_SIZES = new HashMap<>();
    static {
        String[][] sizes = {
            {"1", "u8", "uint8", "s8", "i8", "int8", "udec", "sdec"},
            {"2", "u16", "uint16", "s16", "i16", "int16", "f16", "uflt16", "sflt16"},
            {"3", "u24", "uint24", "s24", "i24", "int24", "sflt24"},
            {"4", "u32", "uint32", "s32", "i32", "int32", "u32le16", "s32le16", "f32le16",
                "u32be16le", "s32be16le", "f32be16le", "f32"},
            {"8", "u64", "uint64", "s64", "i64", "int64", "f64"},
        };
        for (String[] row : sizes) {
            for (int k = 1; k < row.length; k++) FIXED_SIZES.put(row[k], Integer.parseInt(row[0]));
        }
    }

    /** The bytes these fields always take, or null where it varies or is zero (PS-344a, PS-384). */
    static Integer fixedElementSize(List<?> fields) {
        int total = 0;
        for (Object raw : fields) {
            if (!(raw instanceof Map<?, ?> f)) return null;
            String type = f.get("type") == null ? "" : String.valueOf(f.get("type"));
            if (FIXED_SIZES.containsKey(type)) {
                total += FIXED_SIZES.get(type);
            } else if (COMPUTED_TYPES.contains(type) || ("string".equals(type) && f.containsKey("value"))) {
                continue;
            } else if (type.contains("[") || "bool".equals(type)) {
                Object consume = f.containsKey("consume") ? f.get("consume") : 0;
                if (!isInt(consume)) return null;
                total += ((Number) consume).intValue();
            } else if (List.of("bytes", "ascii", "hex", "base64", "skip").contains(type) && isInt(f.get("length"))) {
                total += ((Number) f.get("length")).intValue();
            } else {
                return null;
            }
        }
        return total == 0 ? null : total;
    }
}
