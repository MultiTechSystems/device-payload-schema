package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-121 (PS-544, PS-545), CR-2026-123 (PS-546, PS-547) and CR-2026-126 (PS-548):
 * element repeats completed. Mirrors tests/test_cr_2026_121_123_126.py, with the messages
 * tools/schema_interpreter.py gives.
 */
public class CR2026121123126Test {
    private static byte[] hex(String text) {
        String h = text.replace(" ", "");
        byte[] out = new byte[h.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(h.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    private static String rejection(String yaml) {
        return assertThrows(SchemaException.class, () -> Schema.fromYaml(yaml)).getMessage();
    }

    private static String repeat(String element, int count, String extra) {
        return "name: t\nendian: big\nfields:\n  - name: v\n    type: repeat\n    count: " + count + "\n"
                + extra + "    element: " + element + "\n";
    }

    private static List<Object> listOf(Object... items) {
        return new ArrayList<>(Arrays.asList(items));
    }

    private static Map<String, Object> data(String key, Object value) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put(key, value);
        return out;
    }

    private static List<Double> numbers(Object list) {
        List<Double> out = new ArrayList<>();
        for (Object o : (List<?>) list) out.add(o == null ? null : ((Number) o).doubleValue());
        return out;
    }

    // PS-544

    @Test
    public void aNullRoundTripsThroughTheFirstSentinel() {
        Schema schema = Schema.fromYaml(repeat("{type: u16, sentinel: [0xFFFF, 0xFFFE]}", 3, ""));
        Map<String, Object> out = schema.decode(hex("0001FFFF0003"));
        assertEquals(Arrays.asList(1.0, null, 3.0), numbers(out.get("v")));
        EncodeResult encoded = schema.encode(out);
        assertTrue(encoded.isSuccess(), encoded.toString());
        assertArrayEquals(hex("0001FFFF0003"), encoded.getPayload());
        // The second sentinel decodes to null too, and re-encodes as the first.
        assertArrayEquals(hex("0001FFFF0003"), schema.encode(schema.decode(hex("0001FFFE0003"))).getPayload());
    }

    @Test
    public void theSentinelIsWrittenAsRawBitsNotThroughTheModifiers() {
        Schema schema = Schema.fromYaml(repeat("{type: u16, div: 10, sentinel: [0xFFFF]}", 2, ""));
        EncodeResult encoded = schema.encode(data("v", listOf(0.5, null)));
        assertTrue(encoded.isSuccess(), encoded.toString());
        assertArrayEquals(hex("0005FFFF"), encoded.getPayload());
    }

    @Test
    public void aNullWithoutASentinelIsRefusedNamingThePosition() {
        EncodeResult encoded = Schema.fromYaml(repeat("{type: u8}", 2, "")).encode(data("v", listOf(1, null)));
        assertFalse(encoded.isSuccess());
        assertTrue(String.join("; ", encoded.getErrors()).contains("Repeat 'v' has null at position 1, and its "
                + "element declares no `sentinel` to write for it (PS-544)"), encoded.getErrors().toString());
    }

    // PS-545

    @Test
    public void anInternalNameIsReported() {
        Schema schema = Schema.fromYaml(repeat("{name: _x, type: u8}", 2, ""));
        assertEquals(Arrays.asList(1.0, 2.0), numbers(schema.decode(hex("0102")).get("v")));
        EncodeResult encoded = schema.encode(data("v", listOf(1, 2)));
        assertTrue(encoded.isSuccess(), encoded.toString());
        assertArrayEquals(hex("0102"), encoded.getPayload());
    }

    @Test
    public void anInternalNameStillBindsForReferences() {
        Schema schema = Schema.fromYaml(repeat("{name: _x, type: u8}", 3, "    present_if: {field: $_x, ne: 0}\n"));
        assertEquals(Arrays.asList(1.0, 2.0), numbers(schema.decode(hex("010002")).get("v")));
    }

    @Test
    public void aConstructOrTypelessElementIsRejected() {
        for (String element : new String[] {
                "{byte_group: [{name: a, type: 'u8[0:3]'}]}",
                "{name: m, match: {length: 1, cases: {}}}",
                "{name: f, type: u8, flagged: {field: $x, groups: []}}",
                "{name: n}"}) {
            assertTrue(rejection(repeat(element, 3, "")).contains(
                    "Repeat 'v': `element` is a data field that declares `type`, not a construct (PS-545)"),
                    element);
        }
    }

    @Test
    public void anObjectElementReportsOneObject() {
        Schema schema = Schema.fromYaml(repeat("{name: o, type: object, fields: [{name: a, type: u8}, "
                + "{name: b, type: u8}]}", 2, ""));
        List<?> values = (List<?>) schema.decode(hex("01020304")).get("v");
        assertEquals(2, values.size());
        assertEquals(3.0, ((Number) ((Map<?, ?>) values.get(1)).get("a")).doubleValue());
    }

    // PS-546, PS-547

    private static final String CODES = "  - name: codes\n    type: repeat\n    count: 2\n"
            + "    element: {type: u8, lookup: {1: '1.8.1', 2: '2.8.1', default: '${value}'}}\n";

    private static String values(int count, String index, String element) {
        return "  - name: values\n    type: repeat\n    count: " + count + "\n    index: " + index + "\n"
                + "    identity: $codes[i]\n    element: " + element + "\n";
    }

    private static String schema(String... fields) {
        return "name: t\nendian: big\nfields:\n" + String.join("", fields);
    }

    @Test
    public void twoArraysAndTheMetaIdentity() {
        Schema s = Schema.fromYaml(schema(CODES, values(2, "i", "{type: u32}")));
        Map<String, Object> out = s.interpret(hex("01020000007B000001C8"), new InputContext(null, null, null));
        assertEquals(List.of("1.8.1", "2.8.1"), out.get("codes"));
        assertEquals(Arrays.asList(123.0, 456.0), numbers(out.get("values")));
        Map<?, ?> entry = (Map<?, ?>) ((Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields")).get("values");
        Map<?, ?> identity = (Map<?, ?>) entry.get("identity");
        assertEquals(List.of("repeat", "values", "open"), new ArrayList<>(identity.keySet()));
        assertEquals("codes", identity.get("repeat"));
        assertEquals(List.of("1.8.1", "2.8.1", "${value}"), identity.get("values"));
        assertEquals(Boolean.TRUE, identity.get("open"));
        assertEquals(List.of("type", "element", "identity"), new ArrayList<>(entry.keySet()));
    }

    @Test
    public void aSiblingUnitIsListedByTheRepeat() {
        Schema s = Schema.fromYaml(schema(
                "  - name: units\n    type: repeat\n    count: 2\n    element: {type: u8, lookup: {0: Wh, 1: W}}\n",
                "  - name: values\n    type: repeat\n    count: 2\n    index: i\n"
                        + "    element: {type: u32, unit: '$units[i]'}\n"));
        Map<String, Object> out = s.interpret(hex("00010000007B000001C8"), new InputContext(null, null, null));
        Map<?, ?> entry = (Map<?, ?>) ((Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields")).get("values");
        Map<?, ?> unit = (Map<?, ?>) ((Map<?, ?>) entry.get("element")).get("unit");
        assertEquals("units", unit.get("repeat"));
        assertEquals(List.of("Wh", "W"), unit.get("values"));
        assertFalse(unit.containsKey("open"));
    }

    @Test
    public void differentLengthsAreAnErrorNamingBoth() {
        Schema s = Schema.fromYaml(schema(CODES, values(1, "i", "{type: u32}")));
        Exception e = assertThrows(SchemaException.class, () -> s.decode(hex("01020000007B")));
        assertTrue(e.getMessage().contains(
                "Repeat 'values' has 1 values and 'codes', which identifies them, has 2 (PS-546)"), e.getMessage());
    }

    @Test
    public void aReferenceThatIsNotAnEarlierLabelledSiblingIsRejected() {
        String plainCodes = "  - name: codes\n    type: repeat\n    count: 2\n    element: {type: u8}\n";
        for (String yaml : new String[] {
                schema(values(2, "i", "{type: u32}"), CODES),                // not earlier
                schema(CODES, values(2, "j", "{type: u32}")),                // wrong index
                schema(plainCodes, values(2, "i", "{type: u32}"))}) {        // no lookup
            assertTrue(rejection(yaml).contains("PS-546"), yaml);
        }
    }

    @Test
    public void aCountMayNameAComputedField() {
        for (String kind : new String[] {"integer", "number"}) {
            Schema s = Schema.fromYaml("name: t\nfields:\n  - {name: total, type: u8}\n"
                    + "  - {name: _n, type: " + kind + ", compute: {op: sub, a: $total, b: 2}}\n"
                    + "  - name: v\n    type: repeat\n    count: $_n\n    element: {type: u8}\n");
            assertEquals(Arrays.asList(10.0, 11.0), numbers(s.decode(hex("040A0B")).get("v")), kind);
        }
    }

    // PS-548

    private static final String PS_548 = "its ipso.instance can be other than an integer from 0 to 65534 (PS-548)";

    private static String soil(String lookup) {
        return "name: t\nfields:\n  - name: s\n    type: repeat\n    count: 2\n    index: i\n    fields:\n"
                + "      - {name: k, type: u8, lookup: " + lookup + "}\n"
                + "      - {name: t, type: s16, ipso: {object: 3303, instance: $k}}\n";
    }

    @Test
    public void integerLabelsAreAccepted() {
        Schema.fromYaml(soil("{0: 1, 1: 2}"));
    }

    @Test
    public void labelsThatAreNotInstanceIdsAreRejected() {
        for (String lookup : new String[] {"{0: soil, 1: air}", "{0: 70000}", "{0: '1', default: '${value}'}"}) {
            assertTrue(rejection(soil(lookup)).contains("Field 't': " + PS_548), lookup);
        }
    }

    @Test
    public void aLiteralMustBeAnInstanceId() {
        for (String instance : new String[] {"two", "-1", "65535", "true", "1.5"}) {
            assertTrue(rejection("name: t\nfields:\n  - {name: t, type: u8, ipso: {object: 3303, instance: "
                    + instance + "}}\n").contains("Field 't': " + PS_548), instance);
        }
        for (String instance : new String[] {"0", "65534"}) {
            Schema.fromYaml("name: t\nfields:\n  - {name: t, type: u8, ipso: {object: 3303, instance: "
                    + instance + "}}\n");
        }
    }

    @Test
    public void anIndexMustFitFromItsBase() {
        String template = "name: t\nfields:\n  - name: s\n    type: repeat\n    count: 8\n    index: i\n"
                + "    index_base: %d\n    element: {type: u8, ipso: {object: 3303, instance: $i}}\n";
        Schema.fromYaml(String.format(template, 1));
        Schema.fromYaml(String.format(template, 65527));
        assertTrue(rejection(String.format(template, 65528)).contains("Field '<unnamed>': " + PS_548));
    }

    @Test
    public void aSiblingReferenceIsHeldToIt() {
        String template = "name: t\nfields:\n  - name: units\n    type: repeat\n    count: 2\n"
                + "    element: {type: u8, lookup: %s}\n"
                + "  - name: v\n    type: repeat\n    count: 2\n    index: i\n"
                + "    element: {type: u32, ipso: {object: 3305, instance: '$units[i]'}}\n";
        Schema.fromYaml(String.format(template, "{0: 3, 1: 4}"));
        assertTrue(rejection(String.format(template, "{0: Wh, 1: W}")).contains(PS_548));
    }
}
