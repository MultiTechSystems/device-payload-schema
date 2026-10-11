package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * CR-2026-110: {@code on_repeat} combines a name reported by more than one tlv entry
 * (PS-157, PS-525 to PS-527). The messages are the reference's
 * (tests/test_cr_2026_110_on_repeat.py); the decodes that succeed are the
 * {@code _language-conformance/tlv-repeat-*} fixtures.
 */
public class CR2026110Test {

    private static final String LORAMAC = "name: t\nfields:\n- tlv:\n"
            + "    tag_size: 2\n    on_repeat: merge\n    cases:\n"
            + "      0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}]}]\n"
            + "      0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8}]}]\n"
            + "      0x0001: [{name: battery, type: u8}]\n";

    private static String event(String extra) {
        return "name: t\nfields:\n- tlv:\n" + extra
                + "    cases:\n"
                + "      0x3a: [{name: event, type: u8}]\n"
                + "      0x40: [{name: event, type: u8}]\n";
    }

    private static byte[] hex(String s) {
        return HexFormat.of().parseHex(s);
    }

    @Test
    public void mergeCombinesObjectsAndTheDefaultSparesOtherFields() {
        Map<String, Object> out = Schema.fromYaml(LORAMAC).decode(hex("031001031105000102000103"));
        assertEquals(Map.of("adr", 1L, "dr", 5L), normalise(out.get("loramac")));
        assertEquals(List.of(2L, 3L), normalise(out.get("battery")));
    }

    @Test
    public void aMemberReportedTwiceIsAnError() {
        SchemaException e = assertThrows(SchemaException.class,
                () -> Schema.fromYaml(LORAMAC).decode(hex("031001031002")));
        assertTrue(e.getMessage().contains(
                "tlv: the merged object 'loramac' reports its member 'adr' twice (PS-525)"),
                e.getMessage());
    }

    @Test
    public void lastKeepsTheLastValueAndCollectIsTheDefault() {
        assertEquals(2L, normalise(Schema.fromYaml(event("    on_repeat: last\n"))
                .decode(hex("3a014002")).get("event")));
        assertEquals(List.of(1L, 2L), normalise(Schema.fromYaml(event(""))
                .decode(hex("3a014002")).get("event")));
    }

    @Test
    public void aCollectedListValueIsAnElementNotSpliced() {
        String src = "name: t\nfields:\n- tlv:\n    cases:\n"
                + "      1: [{name: vals, type: repeat, count: 2, fields: [{name: v, type: u8}]}]\n";
        Object vals = normalise(Schema.fromYaml(src).decode(hex("010102010304")).get("vals"));
        assertEquals(List.of(List.of(Map.of("v", 1L), Map.of("v", 2L)),
                List.of(Map.of("v", 3L), Map.of("v", 4L))), vals);
    }

    @Test
    public void mergeEncodesOneEntryPerCaseInDeclarationOrder() {
        Map<String, Object> loramac = new LinkedHashMap<>();
        loramac.put("dr", 5);
        loramac.put("adr", 1);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("loramac", loramac);
        assertArrayEquals(hex("031001031105"), Schema.fromYaml(LORAMAC).encode(data).getPayload());
    }

    @Test
    public void lastEncodesOnceUnderTheFirstCase() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("event", 2);
        assertArrayEquals(hex("3a02"),
                Schema.fromYaml(event("    on_repeat: last\n")).encode(data).getPayload());
    }

    private static void rejected(String src, String message) {
        SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(src));
        assertEquals(message, e.getMessage());
    }

    @Test
    public void mergeOnAScalarIsRejected() {
        rejected("name: t\nfields:\n- tlv:\n    cases:\n"
                        + "      1: [{name: e, type: u8, on_repeat: merge}]\n",
                "Field 'e': only a `type: object` field may declare `on_repeat: merge` (PS-526)");
    }

    @Test
    public void disagreeingDeclarationsAreRejected() {
        rejected("name: t\nfields:\n- tlv:\n    cases:\n"
                        + "      1: [{name: e, type: u8, on_repeat: last}]\n"
                        + "      2: [{name: e, type: u8}]\n",
                "tlv: the fields reporting 'e' declare different `on_repeat`, last and collect (PS-526)");
    }

    @Test
    public void anUnknownModeIsRejected() {
        rejected("name: t\nfields:\n- tlv:\n    on_repeat: first\n    cases:\n"
                        + "      1: [{name: e, type: u8}]\n",
                "tlv `on_repeat` is one of collect, merge, last, not 'first' (PS-525)");
        rejected("name: t\nfields:\n- tlv:\n    cases:\n"
                        + "      1: [{name: e, type: u8, on_repeat: keep}]\n",
                "Field 'e': `on_repeat` is one of collect, merge, last, not 'keep' (PS-525)");
    }

    /** Integers widened to long, so the assertions do not depend on the boxing. */
    private static Object normalise(Object v) {
        if (v instanceof Number n && !(v instanceof Double d && d != Math.rint(d))) {
            return n.longValue();
        }
        if (v instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, x) -> out.put(String.valueOf(k), normalise(x)));
            return out;
        }
        if (v instanceof List<?> l) {
            return l.stream().map(CR2026110Test::normalise).toList();
        }
        return v;
    }

    /** PS-502's one entry per element is not supported, so a collected array is refused. */
    @Test
    public void aCollectedArrayIsRefusedByName() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("event", List.of(1, 2));
        List<String> errors = Schema.fromYaml(event("")).encode(data).getErrors();
        assertTrue(errors.stream().anyMatch(e -> e.contains("was collected into an array (PS-157)")),
                errors.toString());
    }
}
