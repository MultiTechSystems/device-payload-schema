package org.lora.schema;

import java.math.BigDecimal;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 0.5.2 wave 5: a lookup default that names the value it could not map (CR-2026-060).
 * Each method mirrors the function of the same purpose in tools/schema_interpreter.py.
 */
final class Wave5 {
    private Wave5() {}

    /** What a mapping's {@code default} substitutes with the value (PS-406). */
    static final String VALUE_TOKEN = "${value}";

    /** A decimal as {@link #formatLookupValue} writes one. */
    private static final String NUMBER = "(-?\\d+(?:\\.\\d+)?(?:e[-+]?\\d+)?)";

    /** The field's mapping default where it carries ${value}, else null (PS-408). */
    static String template(Field field) {
        String fallback = field.getLookupDefault();
        return fallback != null && !field.isLookupSequence() && fallback.contains(VALUE_TOKEN)
                ? fallback : null;
    }

    /**
     * A value written as JavaScript's String(number) writes it, which is what the
     * reference interpreter and the generated codec write (PS-406, PS-280): the shortest
     * round-tripping digits, fixed for 1e-6 <= |v| < 1e21, and 1e-7 / 1.5e+21 outside.
     */
    static String formatLookupValue(double v) {
        if (v == 0) return "0";
        BigDecimal exact = new BigDecimal(Double.toString(v)).stripTrailingZeros();
        double abs = Math.abs(v);
        if (abs >= 1e-6 && abs < 1e21) return exact.toPlainString();
        String digits = exact.unscaledValue().abs().toString();
        int exponent = digits.length() - 1 - exact.scale();
        String mantissa = digits.length() > 1 ? digits.charAt(0) + "." + digits.substring(1)
                : digits;
        return (v < 0 ? "-" : "") + mantissa + "e" + (exponent < 0 ? "-" : "+")
                + Math.abs(exponent);
    }

    /** The number a ${value} default wrote into {@code text}, or null (PS-409). */
    static Double matchTemplate(String template, String text) {
        String[] parts = template.split(Pattern.quote(VALUE_TOKEN), -1);
        StringBuilder pattern = new StringBuilder(Pattern.quote(parts[0])).append(NUMBER);
        for (int i = 1; i < parts.length - 1; i++) {
            pattern.append(Pattern.quote(parts[i])).append("\\1");
        }
        pattern.append(Pattern.quote(parts[parts.length - 1]));
        Matcher m = Pattern.compile(pattern.toString()).matcher(text);
        return m.matches() ? Double.valueOf(m.group(1)) : null;
    }

    /** PS-407: a ${value} default reports a string, so every label must be one. */
    static void checkLookupTemplate(Map<String, Object> fm) {
        if (!(fm.get("lookup") instanceof Map<?, ?> lookup)) return;
        Object fallback = lookup.get("default");
        if (!(fallback instanceof String s) || !s.contains(VALUE_TOKEN)) return;
        long numeric = lookup.entrySet().stream()
                .filter(e -> !"default".equals(String.valueOf(e.getKey())))
                .filter(e -> !(e.getValue() instanceof String))
                .count();
        if (numeric > 0) {
            throw new SchemaException.ParseException("Field '" + fm.get("name")
                    + "': a lookup whose default carries ${value} reports a string, so its "
                    + "labels must be strings; " + numeric + " are not (PS-407)");
        }
    }
}
