package org.lora.schema;

import java.util.Map;

/**
 * CR-2026-159: constructs withdrawn for want of any use or of any test, rejected at load
 * with what replaces them. The texts are the specification prototype's WITHDRAWN table and
 * the {@code min}/{@code max} message of its check_field, word for word.
 */
final class Cr159 {
    private Cr159() {}

    static final String FORMULA = "`formula` is withdrawn; use `compute`, `polynomial`, "
            + "`transform` or `guard` (CR-2026-159, PS-448)";
    static final String ENCODE_FORMULA =
            "`encode_formula` is withdrawn with `formula` (CR-2026-159, PS-448)";
    static final String CLAMP = "`clamp` is withdrawn; use a `floor` stage and a `ceiling` "
            + "stage (CR-2026-159, PS-115)";
    static final String CONFIRMED = "`confirmed` is withdrawn: confirmed or unconfirmed is "
            + "chosen per transmission, not by the schema (CR-2026-159, PS-448)";

    /**
     * {@code formula}, {@code encode_formula}, and {@code min} or {@code max} on a field that
     * is no repeat (they bound a repeat's element count; a value range is
     * {@code valid_range}, PS-395). A transform's {@code clamp} is checkStage's.
     */
    static void checkField(Map<?, ?> fm) {
        if (fm.containsKey("formula")) throw new SchemaException(FORMULA);
        if (fm.containsKey("encode_formula")) throw new SchemaException(ENCODE_FORMULA);
        if (!"repeat".equals(fm.get("type"))) {
            Object name = fm.containsKey("name") ? fm.get("name") : "<unnamed>";
            for (String bound : new String[] {"min", "max"}) {
                if (fm.containsKey(bound)) {
                    throw new SchemaException("`" + bound + "` on field '" + name + "' is "
                            + "withdrawn: it bounds a repeat's element count only; declare a "
                            + "value range with `valid_range` (CR-2026-159, PS-395)");
                }
            }
        }
    }

    /** A port's {@code confirmed}. */
    static void checkPorts(Map<String, Object> raw) {
        if (raw.get("ports") instanceof Map<?, ?> ports) {
            for (Object port : ports.values()) {
                if (port instanceof Map<?, ?> pm && pm.containsKey("confirmed")) {
                    throw new SchemaException(CONFIRMED);
                }
            }
        }
    }
}
