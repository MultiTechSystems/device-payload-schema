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

    // CR-2026-125 and CR-2026-128 (PS-525 to PS-527): the spec prototype's
    // TestCR2026125OnRepeatEncoding and TestCR2026128FirstCaseThatFits.

    private static final String LORAMAC_SHARED = "name: t\nfields:\n- tlv:\n"
            + "    tag_size: 2\n    on_repeat: merge\n    cases:\n"
            + "      0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}, {name: dr, type: u8, on_repeat: last}]}]\n"
            + "      0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8, on_repeat: last}]}]\n";
    private static final String COLLECTED = "name: t\nfields:\n- tlv:\n    tag_size: 2\n    cases:\n"
            + "      0x0310: [{name: mac, type: object, fields: [{name: adr, type: u8}]}]\n"
            + "      0x0311: [{name: mac, type: object, fields: [{name: dr, type: u8}]}]\n";
    private static final String SHARED_LAST = "name: t\nfields:\n- tlv:\n    on_repeat: merge\n    cases:\n"
            + "      1: [{name: o, type: object, fields: [{name: a, type: u8}, {name: m, type: u8, on_repeat: last}]}]\n"
            + "      2: [{name: o, type: object, fields: [{name: b, type: u8}, {name: m, type: u8, on_repeat: last}]}]\n";

    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    private static void encodesTo(String yaml, Map<String, Object> data, String want) {
        EncodeResult result = Schema.fromYaml(yaml).encode(data);
        assertTrue(result.getErrors().isEmpty(), result.getErrors().toString());
        assertArrayEquals(hex(want), result.getPayload());
    }

    @Test
    public void eachValueGoesUnderTheFirstCaseItFills() {
        encodesTo(event(""), map("event", List.of(1, 2)), "3a013a02");
        encodesTo(event(""), map("event", 3), "3a03");
        encodesTo(COLLECTED, map("mac", List.of(map("adr", 1), map("dr", 5))), "031001031105");
        encodesTo(LORAMAC_SHARED, map("loramac", map("dr", 5)), "031105");
        encodesTo(LORAMAC_SHARED, map("loramac", map("adr", 1, "dr", 5)), "03100105");
        encodesTo(SHARED_LAST, map("o", map("a", 1, "b", 2, "m", 9)), "010109020209");
    }

    @Test
    public void aValueNoCaseTakesInFullIsAnError() {
        List<String> errors = Schema.fromYaml(LORAMAC_SHARED)
                .encode(map("loramac", map("adr", 1, "dr", 5, "fw", 3))).getErrors();
        assertTrue(errors.stream().anyMatch(e -> e.contains(
                "tlv: the merged object 'loramac' is given ['fw'], which no case declares (PS-527)")),
                errors.toString());
        errors = Schema.fromYaml(COLLECTED).encode(map("mac", List.of(map("adr", 1, "dr", 5)))).getErrors();
        assertTrue(errors.stream().anyMatch(e -> e.contains("tlv: no case takes the value of 'mac'")),
                errors.toString());
    }

    @Test
    public void aSharedMemberWithoutLastIsRejectedAtLoad() {
        rejected(LORAMAC_SHARED.replace(", on_repeat: last", ""),
                "tlv: cases share the member 'dr' of the merged object 'loramac' without `on_repeat: last` (PS-526)");
    }

    @Test
    public void onRepeatWhereItMeansNothingIsRejected() {
        String tail = "' has no effect there: it belongs on a tlv, a field of a tlv case, or a"
                + " merged object's member (PS-525)";
        rejected("name: t\nfields:\n- {name: a, type: u8, on_repeat: last}\n", "`on_repeat` on 'a" + tail);
        rejected("name: t\nfields:\n- tlv:\n    cases:\n"
                + "      1: [{name: o, type: object, fields: [{name: m, type: u8, on_repeat: last}]}]\n",
                "`on_repeat` on 'm" + tail);
    }

    @Test
    public void lastOnAMergedMemberIsAccepted() {
        Schema s = Schema.fromYaml("name: t\nfields:\n- tlv:\n    on_repeat: merge\n    cases:\n"
                + "      1: [{name: o, type: object, fields: [{name: m, type: u8, on_repeat: last}]}]\n");
        assertEquals(Map.of("o", Map.of("m", 7L)), normalise(s.decode(hex("01050107"))));
    }

    /** CR-2026-129: an object case is as wide as its members. */
    @Test
    public void anObjectCaseIsAsWideAsItsMembers() {
        String wider = "name: t\nfields:\n- tlv:\n    tag_size: 2\n    on_repeat: merge\n    cases:\n"
                + "      0x0310: [{name: loramac, type: object, fields: [{name: dr, type: u8, on_repeat: last}]}]\n"
                + "      0x0311: [{name: loramac, type: object, fields: [{name: adr, type: u8}, {name: dr, type: u8, on_repeat: last}]}]\n";
        encodesTo(wider, map("loramac", map("adr", 1, "dr", 5)), "03110105");
    }
}
