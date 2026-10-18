package org.lora.schema;

import java.util.List;
import java.util.Map;

/**
 * CR-2026-196 (PS-635 to PS-637): a {@code type: number} field declaring {@code source}
 * reports a value of the frame - {@code $fPort}, {@code $payloadLength} or
 * {@code $remaining} - reading no bytes, through its modifiers once. The reference's
 * {@code frame_source_errors}, {@code _frame_source} and {@code frame_source_mismatch}.
 */
final class FrameSource {
    private FrameSource() { }

    static final List<String> SOURCES = List.of("$fPort", "$payloadLength", "$remaining");

    /** PS-635, at load, in the spec prototype's text. */
    static void check(Map<String, Object> fm) {
        if (!fm.containsKey("type") || !fm.containsKey("source")) return;
        boolean clash = fm.containsKey("value") || fm.containsKey("compute")
                || fm.containsKey("ref") || fm.containsKey("polynomial")
                || (fm.get("time") instanceof Map<?, ?> t && t.containsKey("parts"));
        String type = String.valueOf(fm.get("type")).toLowerCase();
        if (!(type.equals("number") || type.equals("integer"))
                || !SOURCES.contains(fm.get("source")) || clash) {
            throw new SchemaException("Field '" + fm.get("name") + "': source is one of "
                    + String.join(", ", SOURCES) + ", on a type: number field that computes "
                    + "nothing else (PS-635)");
        }
    }

    /**
     * PS-635, PS-636: the integer {@code source} names, through the field's modifiers; null
     * (the field omitted) with a warning where no FPort was given. {@code $remaining} is the
     * bytes left in the data being read, which ends at a tlv entry's end or a reserve.
     */
    static Double decode(Field field, DecodeContext ctx) {
        Integer raw = switch (field.getSource()) {
            case "$remaining" -> Math.max(0, ctx.remaining());
            case "$payloadLength" -> ctx.getPayloadLength();
            default -> ctx.getFramePort();
        };
        if (raw == null) {
            ctx.addWarning("Field '" + field.getName() + "': no FPort was given, so it is "
                    + "omitted (PS-636)");
            return null;
        }
        return Schema.applyArithmetic(raw, field);
    }

    /** PS-637: what the field reports for {@code raw} on the encode side; null for none. */
    static Double value(Field field, Integer raw) {
        if (raw == null) return null;
        double v = Schema.applyArithmetic(raw, field);
        return Double.isNaN(v) || Double.isInfinite(v) ? null : v + 0.0;
    }
}
