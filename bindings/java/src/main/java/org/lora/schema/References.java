package org.lora.schema;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * PS-539 (CR-2026-117): every {@code $} reference names something the schema declares - a
 * field, a {@code var}, a tag component, a repeat's {@code index} or {@code count_as} - in
 * the header, the fields, a port or a definition, at any depth. A {@code metadata} include
 * or timestamp name counts as declared; a {@code metadata.include} {@code source} names
 * runtime input (PS-315) and is not checked. Whether the name is bound when it is read is a
 * decode matter (PS-314), so a name declared later, or in a branch not taken, is valid here.
 *
 * <p>The walk is the spec prototype's {@code key_vocabulary.check_references} and
 * {@code reference_errors} in the reference implementation; Go and C# walk the same way,
 * and all four give the message identically.
 */
final class References {
    private References() {}

    /** Keys whose string value may be a {@code $} reference. */
    static final Set<String> REFERENCE_KEYS = Set.of("ref", "a", "b", "field", "count",
            "byte_length", "carry", "instance", "unit", "identity");

    /** Keys holding data, not declarations or references. A match's {@code default} is a field list. */
    static final Set<String> DATA_KEYS = Set.of("lookup", "values", "else", "value");

    private static final Pattern TEMPLATE_REFERENCE = Pattern.compile("\\$\\{(\\w+)\\}");

    /** PS-539's wording, the spec prototype's. */
    static String message(String name, String key) {
        return "`$" + name + "` (" + key + ") names no field, var, tag component, index or "
                + "count_as the schema declares (PS-539)";
    }

    /** Every undeclared reference's message, in source order, each once. */
    static List<String> errors(Map<String, Object> raw) {
        Set<String> declared = new HashSet<>();
        List<String[]> references = new ArrayList<>();
        walk(raw.get("header"), null, false, declared, references);
        walk(raw.get("fields"), null, false, declared, references);
        for (String part : List.of("ports", "definitions")) {
            if (raw.get(part) instanceof Map<?, ?> groups) {
                for (Object group : groups.values()) walk(group, null, false, declared, references);
            }
        }
        if (raw.get("metadata") instanceof Map<?, ?> metadata) {
            for (String part : List.of("include", "timestamps")) {
                if (metadata.get(part) instanceof List<?> entries) {
                    for (Object entry : entries) {
                        if (entry instanceof Map<?, ?> m && m.get("name") instanceof String name) {
                            declared.add(name);
                        }
                    }
                }
            }
        }
        List<String> errors = new ArrayList<>();
        for (String[] reference : references) {
            if (!declared.contains(reference[0])) {
                String message = message(reference[0], reference[1]);
                if (!errors.contains(message)) errors.add(message);
            }
        }
        return errors;
    }

    /** Rejects the schema at the first undeclared reference. */
    static void check(Map<String, Object> raw) {
        List<String> errors = errors(raw);
        if (!errors.isEmpty()) throw new SchemaException(errors.get(0));
    }

    private static void walk(Object node, String key, boolean inSenml, Set<String> declared,
                             List<String[]> references) {
        if (node instanceof List<?> list) {
            for (Object item : list) walk(item, null, false, declared, references);
            return;
        }
        if (!(node instanceof Map<?, ?> map)) return;
        if (!inSenml) {
            for (String nameKey : List.of("name", "var", "index", "count_as")) {
                if (map.get(nameKey) instanceof String s) declared.add(s);
            }
            // An `element` that declares no name is decoded as `element` (PS-528), so its
            // `present_if` and `name_from` can name it.
            if (map.get("element") instanceof Map<?, ?> element && !(element.get("name") instanceof String)) {
                declared.add("element");
            }
        }
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            String k = String.valueOf(entry.getKey());
            Object v = entry.getValue();
            if (DATA_KEYS.contains(k)) continue;
            if (v instanceof String s) {
                if (REFERENCE_KEYS.contains(k) && s.startsWith("$")) {
                    references.add(new String[] {s.substring(1), k});
                } else if (k.equals("name_from") || (inSenml && k.equals("name"))) {
                    Matcher m = TEMPLATE_REFERENCE.matcher(s);
                    while (m.find()) references.add(new String[] {m.group(1), k});
                }
            } else if (k.equals("default")) {
                if ("match".equals(key) && (v instanceof Map || v instanceof List)) {
                    walk(v, null, false, declared, references);   // a match's default is a field list
                }
            } else {
                walk(v, k, k.equals("senml"), declared, references);
            }
        }
    }
}
