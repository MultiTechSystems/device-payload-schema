package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-118: several records from one repeat element (PS-541, PS-542) and a condition on
 * a set of numbers (PS-543). Mirrors the spec prototype's TestCR2026118RecordsAndSets, with
 * the messages tools/schema_interpreter.py gives.
 */
public class CR2026118Test {
    private static final String MESSAGES = "name: t\nendian: big\nfields:\n"
            + "  - name: messages\n    type: repeat\n    until: end\n    index: i\n    count_as: n\n"
            + "    present_if: {field: $_dataId, not_in: [1, 2, 3, 4, 5, 6]}\n"
            + "    fields:\n"
            + "      - {name: _dataId, type: u8}\n"
            + "      - {name: pos, type: number, ref: $i}\n"
            + "      - match:\n          field: $_dataId\n          cases:\n"
            + "            7:\n              - records:\n"
            + "                  - [{name: type, type: string, value: upload_battery}, {name: battery, type: u16}]\n"
            + "                  - [{name: type, type: string, value: upload_interval}, "
            + "{name: interval, type: u16, mult: 60}]\n"
            + "            3: [{name: x, type: u8}]\n"
            + "            0: [{name: hw, type: u8}]\n"
            + "  - {name: count, type: number, ref: $n}\n";

    private static byte[] hex(String text) {
        String h = text.replace(" ", "");
        byte[] out = new byte[h.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(h.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    private static String rejection(String yaml) {
        return assertThrows(SchemaException.class, () -> Schema.fromYaml(yaml)).getMessage();
    }

    @Test
    public void recordsBecomeConsecutiveElementsAndNotInDrops() {
        Map<String, Object> out = Schema.fromYaml(MESSAGES).decode(hex("070064003C0305000A"));
        List<?> messages = (List<?>) out.get("messages");
        assertEquals(3, messages.size());
        Map<?, ?> first = (Map<?, ?>) messages.get(0);
        assertEquals("upload_battery", first.get("type"));
        assertEquals(100.0, ((Number) first.get("battery")).doubleValue());
        Map<?, ?> second = (Map<?, ?>) messages.get(1);
        assertEquals("upload_interval", second.get("type"));
        assertEquals(3600.0, ((Number) second.get("interval")).doubleValue());
        assertEquals(10.0, ((Number) ((Map<?, ?>) messages.get(2)).get("hw")).doubleValue());
        // index counts source elements, count_as the records reported.
        assertEquals(0.0, ((Number) first.get("pos")).doubleValue());
        assertEquals(0.0, ((Number) second.get("pos")).doubleValue());
        assertEquals(2.0, ((Number) ((Map<?, ?>) messages.get(2)).get("pos")).doubleValue());
        assertEquals(3.0, ((Number) out.get("count")).doubleValue());
        assertFalse(first.containsKey("_dataId"));
    }

    @Test
    public void theElementsOtherFieldsGoIntoEachRecordFirst() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: m\n    type: repeat\n    count: 1\n"
                + "    fields:\n      - {name: channel, type: u8}\n"
                + "      - records: [[{name: a, type: u8}], [{name: b, type: u8}]]\n");
        List<?> m = (List<?>) schema.decode(hex("010203")).get("m");
        assertEquals(2, m.size());
        assertEquals(List.of("channel", "a"), List.copyOf(((Map<?, ?>) m.get(0)).keySet()));
        assertEquals(List.of("channel", "b"), List.copyOf(((Map<?, ?>) m.get(1)).keySet()));
    }

    @Test
    public void metaElementsCoverEveryRecord() {
        Schema schema = Schema.fromYaml(MESSAGES);
        Map<?, ?> fields = (Map<?, ?>) ((Map<?, ?>) schema.interpret(hex("070064003C"),
                new InputContext(null, null, null)).get("_meta")).get("fields");
        Map<?, ?> elements = (Map<?, ?>) ((Map<?, ?>) fields.get("messages")).get("elements");
        assertTrue(elements.keySet().containsAll(List.of("type", "battery", "interval")), elements.toString());
    }

    @Test
    public void recordsOutsideAnElementIsRejected() {
        assertTrue(rejection("name: t\nfields:\n  - records: [[{name: a, type: u8}]]\n").contains(
                "`records` belongs in a repeat's element, or a match case within it (PS-541)"));
    }

    @Test
    public void emptyRecordsIsRejected() {
        assertTrue(rejection("name: t\nfields:\n  - name: m\n    type: repeat\n    count: 1\n"
                + "    fields: [{records: []}]\n").contains("`records` is a non-empty list of field lists (PS-541)"));
    }

    @Test
    public void anElementReachingRecordsIsRejected() {
        assertTrue(rejection("name: t\nfields:\n  - name: m\n    type: repeat\n    count: 1\n"
                + "    element: {match: {length: 1, cases: {1: [{records: [[{name: a, type: u8}]]}]}}}\n")
                .contains("Repeat 'm': an `element` cannot reach a `records` entry (PS-541)"));
    }

    @Test
    public void encodingRecordsIsRefused() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: m\n    type: repeat\n    count: 1\n"
                + "    fields: [{records: [[{name: a, type: u8}]]}]\n");
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("m", List.of(Map.of("a", 1)));
        EncodeResult result = schema.encode(data);
        assertFalse(result.isSuccess());
        assertTrue(String.join("; ", result.getErrors()).contains(
                "Repeat 'm' has elements that yield several records, which cannot be encoded (PS-542)"),
                result.getErrors().toString());
    }

    @Test
    public void inAndNotIn() {
        String in = "name: t\nfields:\n  - name: v\n    type: repeat\n    count: 3\n"
                + "    present_if: {field: $x, in: [1, 3]}\n    element: {name: x, type: u8}\n";
        assertEquals(List.of(1L, 3L), longs(Schema.fromYaml(in).decode(hex("010203")).get("v")));
        String notIn = in.replace("in: [1, 3]", "not_in: [1, 3]");
        assertEquals(List.of(2L), longs(Schema.fromYaml(notIn).decode(hex("010203")).get("v")));
    }

    @Test
    public void aGuardTakesIn() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - {name: mode, type: u8}\n"
                + "  - name: s\n    type: number\n    ref: $mode\n"
                + "    guard: {when: [{field: $mode, in: [2, 4]}], else: -1}\n");
        assertEquals(4.0, ((Number) schema.decode(hex("04")).get("s")).doubleValue());
        assertEquals(-1.0, ((Number) schema.decode(hex("05")).get("s")).doubleValue());
    }

    @Test
    public void aSetMustBeNumbersAndAlone() {
        assertTrue(rejection("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n"
                + "    present_if: {field: $x, in: [1, x]}\n    fields: [{name: x, type: u8}]\n")
                .contains("`in` in the present_if of 'v' is a list of numbers (PS-543)"));
        assertTrue(rejection("name: t\nfields:\n  - {name: a, type: u8}\n  - name: b\n    type: number\n"
                + "    ref: $a\n    guard: {when: [{field: $a, not_in: [true]}], else: 0}\n")
                .contains("`not_in` in a guard condition of 'b' is a list of numbers (PS-543)"));
        assertTrue(rejection("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n"
                + "    present_if: {field: $x, not_in: [1], eq: 2}\n    fields: [{name: x, type: u8}]\n")
                .contains("the present_if of 'v' has 2 comparison operators; a condition has exactly one (PS-126)"));
    }

    /** CR-2026-124: PS-126 holds for every condition, not only a set one. */
    @Test
    public void everyConditionHasExactlyOneOperator() {
        assertTrue(rejection("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n"
                + "    present_if: {field: $x, gt: 0, lt: 3}\n    fields: [{name: x, type: u8}]\n")
                .contains("the present_if of 'v' has 2 comparison operators; a condition has exactly one (PS-126)"));
        assertTrue(rejection("name: t\nfields:\n  - {name: a, type: u8}\n  - name: b\n    type: number\n"
                + "    ref: $a\n    guard: {when: [{field: $a}], else: 0}\n")
                .contains("a guard condition of 'b' has 0 comparison operators; a condition has exactly one (PS-126)"));
    }

    /** CR-2026-124 (PS-543): an empty in never holds, an empty not_in always does. */
    @Test
    public void emptySets() {
        String in = "name: t\nfields:\n  - name: v\n    type: repeat\n    count: 3\n"
                + "    present_if: {field: $x, in: []}\n    element: {name: x, type: u8}\n";
        assertEquals(List.of(), longs(Schema.fromYaml(in).decode(hex("010203")).get("v")));
        String notIn = in.replace("in: []", "not_in: []");
        assertEquals(List.of(1L, 2L, 3L), longs(Schema.fromYaml(notIn).decode(hex("010203")).get("v")));
    }

    /** CR-2026-124 (PS-541): a record may not report a name the element reports outside. */
    @Test
    public void aRecordSharingAnElementNameIsRejected() {
        String yaml = "name: t\nfields:\n  - name: m\n    type: repeat\n    count: 1\n    fields:\n"
                + "      - {name: channel, type: u8}\n"
                + "      - match: {length: 1, cases: {1: [{name: kind, type: u8}]}}\n"
                + "      - records:\n"
                + "          - [{name: kind, type: u8}]\n"
                + "          - [{name: channel, type: u8}]\n";
        assertTrue(rejection(yaml).contains("Repeat 'm': ['channel', 'kind'] is reported both by a record "
                + "and by the element outside `records` (PS-541)"), rejection(yaml));
        // An object member is not reported at the element's level, and a `_` field is reported nowhere.
        String fine = "name: t\nfields:\n  - name: m\n    type: repeat\n    count: 1\n    fields:\n"
                + "      - {name: _x, type: u8}\n"
                + "      - {name: o, type: object, fields: [{name: b, type: u8}]}\n"
                + "      - records:\n          - [{name: b, type: u8}, {name: _x, type: u8}]\n";
        assertNotNull(Schema.fromYaml(fine));
    }

    private static List<Long> longs(Object list) {
        return ((List<?>) list).stream().map(v -> ((Number) v).longValue()).toList();
    }
}
