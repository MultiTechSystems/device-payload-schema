package org.lora.schema;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CR-2026-176 (PS-361 amended, PS-590 to PS-592): a {@code type: string} field may declare
 * {@code template}, text joined from values decoded earlier. {@code ${name}} writes what a
 * key template writes (PS-532): a looked-up field's label, a number as JavaScript's
 * String(n), a string as it is, a boolean as true or false. {@code ${name:format}} writes the
 * number the reference binds (PS-523) in a part format of PS-430, through
 * {@link BitfieldParts#formatNumber}; {@code $$} writes {@code $}. A reference with no value
 * makes the field absent (PS-592); a format given a value it cannot write makes it absent
 * with a warning (PS-591). Mirrors render_string_template and string_template_errors in
 * tools/schema_interpreter.py; the texts are the spec prototype's.
 */
final class TemplateStrings {
    private TemplateStrings() {}

    /** {@code $$}, {@code ${name}} or {@code ${name:format}}. */
    static final Pattern TOKEN = Pattern.compile("\\$\\$|\\$\\{(\\w+)(?::([^}]*))?\\}");

    /** The names a template references, as the prototype's STRING_TEMPLATE_REFERENCE reads them. */
    static final Pattern REFERENCE = Pattern.compile("\\$\\{(\\w+)(?::[^}]*)?\\}");

    /** PS-361 for a string field declaring neither {@code value} nor {@code template}. */
    static String neitherMessage(Object name) {
        return "Field '" + name + "': `type: string` is a literal that declares `value`, or a "
                + "template that declares `template`; a string read from the payload is `ascii` "
                + "(PS-361)";
    }

    /**
     * The load-time rules for one field mapping of a field list: PS-361's one of value and
     * template, the template's placement (PS-590), its formats (PS-430), and no reference to
     * an object or repeat that list declared before it (PS-532). {@code aggregates} holds
     * those names.
     */
    static void check(Map<String, Object> fm, Set<String> aggregates) {
        Object type = fm.get("type");
        Object name = fm.containsKey("name") ? fm.get("name") : "<unnamed>";
        if ("string".equals(type) && !fm.containsKey("template") && !fm.containsKey("value")) {
            throw new SchemaException(neitherMessage(fm.get("name")));
        }
        if (!fm.containsKey("template")) return;
        if (!"string".equals(type) || fm.containsKey("value") || !(fm.get("template") instanceof String)) {
            throw new SchemaException("The template of '" + name + "' is a string on a "
                    + "`type: string` field that declares no `value` (PS-361, PS-590)");
        }
        String template = (String) fm.get("template");
        Matcher m = TOKEN.matcher(template);
        while (m.find()) {
            if (m.group(1) == null) continue;
            if (aggregates.contains(m.group(1))) {
                throw new SchemaException("The template of '" + name + "' references `"
                        + m.group(1) + "`, an object or repeat, which has no text (PS-532, PS-590)");
            }
            if (m.group(2) != null) BitfieldParts.parse(m.group(2));     // PS-430
        }
    }

    /** A value as a PS-591 warning writes it: the prototype's js_text. */
    private static String given(Object value) {
        if (value instanceof Boolean || value instanceof Number) return Wave5.renderTemplateValue(value);
        return "'" + value + "'";
    }

    /** The integer a format writes, or null where the value is not one (PS-591). */
    private static BigInteger integral(Object value) {
        if (value instanceof Boolean || !(value instanceof Number n)) return null;
        if (n instanceof BigInteger b) return b;
        if (n instanceof Long || n instanceof Integer || n instanceof Short || n instanceof Byte) {
            return BigInteger.valueOf(n.longValue());
        }
        double d = n.doubleValue();
        if (!Double.isFinite(d) || d != Math.rint(d)) return null;
        return new BigDecimal(d).toBigInteger();
    }

    /** The field's text, or null where it is absent (PS-591, PS-592). */
    static String render(Field field, DecodeContext ctx) {
        Matcher m = TOKEN.matcher(field.getTemplate());
        StringBuilder out = new StringBuilder();
        boolean absent = false;
        while (m.find()) {
            String reference = m.group(1);
            String format = m.group(2);
            String text = "";
            if (reference == null) {
                text = "$";
            } else if (ctx.getVariable(reference) == null) {
                absent = true;                                      // PS-592
            } else if (format == null) {
                Object value = ctx.reported(reference);             // PS-532: the label
                if (value == DecodeContext.NO_LABEL || value == null) {
                    absent = true;
                } else {
                    text = Wave5.renderTemplateValue(value);
                    if (text == null) {
                        throw new SchemaException(Wave5.templateShapeError(field.getTemplate(), reference));
                    }
                }
            } else {
                BitfieldParts.Format f = BitfieldParts.parse(format);
                Object number = ctx.getVariable(reference);         // PS-523: what it binds
                BigInteger whole = integral(number);
                if (whole == null || (f.base().startsWith("hex") && whole.signum() < 0)) {
                    ctx.addWarning("Field '" + field.getName() + "': ${" + reference + ":" + format
                            + "} is given " + given(number) + ", which the format cannot write, so "
                            + "the field is absent (PS-591)");
                    absent = true;
                } else {
                    text = BitfieldParts.formatNumber(whole.signum() < 0, whole.abs(), f.base(), f.width());
                }
            }
            m.appendReplacement(out, Matcher.quoteReplacement(text));
        }
        m.appendTail(out);
        return absent ? null : out.toString();
    }
}
