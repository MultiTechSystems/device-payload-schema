package org.lora.schema;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * CR-2026-156 (PS-567 to PS-569): {@code sampled} on a repeat.
 *
 * <p>{@code sampled} says a repeat's elements are samples of one measurement taken at a
 * fixed interval. It is an annotation: decoding and encoding ignore it. This class holds the
 * load check (PS-569) and the {@code _meta} entry (PS-568); both mirror {@code sampled_errors}
 * and {@code sampled_meta} in tools/schema_interpreter.py, whose wording is the spec
 * prototype's {@code check_sampled}. A {@code $} interval must name a field the repeat can
 * see, which {@link References} holds to PS-539 ({@code interval} is one of its reference keys).
 */
final class Sampled {
    private Sampled() {}

    /** PS-568: the declaration with order and at filled in; a {@code $name} interval is {field: name}. */
    static Map<String, Object> meta(Map<?, ?> sampled) {
        Object interval = sampled.get("interval");
        if (interval instanceof String s) {
            Map<String, Object> field = new LinkedHashMap<>();
            field.put("field", s.startsWith("$") ? s.substring(1) : s);
            interval = field;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("interval", interval);
        out.put("order", sampled.containsKey("order") ? sampled.get("order") : "newest_first");
        out.put("at", sampled.containsKey("at") ? sampled.get("at") : "rx_time");
        return out;
    }

    /** Throws the first PS-569 violation in the header, fields, ports and definitions. */
    static void check(Map<String, Object> raw) {
        Set<String> times = new HashSet<>();
        if (raw.get("metadata") instanceof Map<?, ?> metadata
                && metadata.get("timestamps") instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?> ts && ts.get("name") instanceof String name
                        && ("unix_epoch".equals(ts.get("mode")) || "iso8601".equals(ts.get("mode")))) {
                    times.add(name);
                }
            }
        }
        // PS-567 (CR-2026-192): fields declaring time of format unix or iso8601, collected in
        // source order, so a repeat sees only those decoded before it, as check_sampled does.
        Set<String> timeFields = new HashSet<>();
        for (String key : new String[] {"header", "fields", "ports", "definitions"}) {
            walk(raw.get(key), times, timeFields);
        }
    }

    private static void walk(Object node, Set<String> times, Set<String> timeFields) {
        if (node instanceof List<?> list) {
            for (Object item : list) walk(item, times, timeFields);
        } else if (node instanceof Map<?, ?> map) {
            if (map.containsKey("type") && map.get("time") instanceof Map<?, ?> spec
                    && ("unix".equals(spec.get("format")) || "iso8601".equals(spec.get("format")))
                    && map.get("name") instanceof String name) {
                timeFields.add(name);
            }
            if (map.containsKey("sampled") && map.get("type") != null) checkField(map, times, timeFields);
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!"test_vectors".equals(String.valueOf(e.getKey()))) walk(e.getValue(), times, timeFields);
            }
        }
    }

    private static SchemaException bad(Map<?, ?> field, String why) {
        Object name = field.get("name");
        return new SchemaException("Field '" + (name instanceof String ? name : "None")
                + "': sampled " + why + " (PS-569)");
    }

    private static void checkField(Map<?, ?> field, Set<String> times, Set<String> timeFields) {
        if (!"repeat".equals(field.get("type"))) {
            throw bad(field, "is defined only on a repeat, not on " + field.get("type"));
        }
        if (!(field.get("sampled") instanceof Map<?, ?> sampled) || !sampled.containsKey("interval")) {
            throw bad(field, "needs an interval");
        }
        Object interval = sampled.get("interval");
        boolean valid;
        if (interval instanceof Boolean) {
            valid = false;
        } else if (interval instanceof Number n) {
            valid = n.doubleValue() > 0;
        } else if (interval instanceof String s) {
            valid = s.startsWith("$");
        } else if (interval instanceof Map<?, ?> m) {
            valid = m.size() == 1 && m.get("configured") instanceof String;
        } else {
            valid = false;
        }
        if (!valid) {
            throw bad(field, "interval " + repr(interval) + " is not a number of seconds above 0, "
                    + "a $ reference or {configured: name}");
        }
        if (sampled.containsKey("order") && !"newest_first".equals(sampled.get("order"))
                && !"oldest_first".equals(sampled.get("order"))) {
            throw bad(field, "order " + repr(sampled.get("order")) + " is not newest_first or oldest_first");
        }
        if (sampled.containsKey("at")) {
            Object at = sampled.get("at");
            if (!(at instanceof String s
                    && (s.equals("rx_time") || times.contains(s) || timeFields.contains(s)))) {
                throw bad(field, "at " + repr(at) + " is neither rx_time, a unix_epoch or iso8601 "
                        + "timestamps entry, nor a unix or iso8601 time field decoded before the "
                        + "repeat (PS-567)");
            }
        }
    }

    /** A value as Python's repr writes it, so a message names it as the reference's does. */
    static String repr(Object v) {
        if (v == null) return "None";
        if (v instanceof String s) return "'" + s + "'";
        if (v instanceof Boolean b) return b ? "True" : "False";
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            String s = String.valueOf(d);
            if (d == Math.rint(d) && !Double.isInfinite(d) && Math.abs(d) < 1e16) {
                return String.valueOf((long) d) + ".0";
            }
            return s;
        }
        if (v instanceof List<?> list) {
            List<String> parts = new ArrayList<>();
            for (Object item : list) parts.add(repr(item));
            return "[" + String.join(", ", parts) + "]";
        }
        if (v instanceof Map<?, ?> map) {
            List<String> parts = new ArrayList<>();
            for (Map.Entry<?, ?> e : map.entrySet()) parts.add(repr(e.getKey()) + ": " + repr(e.getValue()));
            return "{" + String.join(", ", parts) + "}";
        }
        return String.valueOf(v);
    }
}
