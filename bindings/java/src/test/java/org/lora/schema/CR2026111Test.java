package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-111: a repeat of single values (PS-528, PS-529, PS-531) and an index that counts
 * from {@code index_base} (PS-530, PS-375). Mirrors the spec prototype's
 * TestCR2026111ElementRepeat, with the messages tools/schema_interpreter.py gives.
 */
public class CR2026111Test {
    private static final String INCREMENTS = "name: t\nendian: big\nfields:\n"
            + "  - name: increments\n    type: repeat\n    until: end\n"
            + "    element: {type: u16, div: 10}\n";

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
    public void itReportsAnArrayOfValues() {
        Map<String, Object> out = Schema.fromYaml(INCREMENTS).decode(hex("00780096005A"));
        List<?> values = (List<?>) out.get("increments");
        assertEquals(3, values.size());
        assertEquals(12.0, ((Number) values.get(0)).doubleValue());
        assertEquals(15.0, ((Number) values.get(1)).doubleValue());
        assertEquals(9.0, ((Number) values.get(2)).doubleValue());
    }

    @Test
    public void anOmittedValueIsNullInItsPosition() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 3\n"
                + "    element: {type: u8, sentinel: [255]}\n");
        List<?> values = (List<?>) schema.decode(hex("01FF03")).get("v");
        assertEquals(3, values.size());
        assertNull(values.get(1));
        assertEquals(3.0, ((Number) values.get(2)).doubleValue());
    }

    @Test
    public void aMappingMissIsNullToo() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 2\n"
                + "    element: {type: u8, lookup: {1: 'on'}}\n");
        assertEquals(Arrays.asList("on", null), schema.decode(hex("0102")).get("v"));
    }

    @Test
    public void presentIfRefersToTheValueByItsName() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 3\n"
                + "    present_if: {field: $x, ne: 0}\n    element: {name: x, type: u8}\n");
        List<?> values = (List<?>) schema.decode(hex("010002")).get("v");
        assertEquals(2, values.size());
        assertEquals(2.0, ((Number) values.get(1)).doubleValue());
    }

    @Test
    public void bothFieldsAndElementAreRejected() {
        assertEquals("Repeat 'v' declares both of `fields` and `element`; it declares exactly one "
                + "(PS-083, PS-528)", rejection("name: t\nfields:\n  - name: v\n    type: repeat\n"
                + "    count: 1\n    fields: [{name: a, type: u8}]\n    element: {type: u8}\n"));
    }

    @Test
    public void neitherFieldsNorElementIsRejected() {
        assertEquals("Repeat 'v' declares neither of `fields` and `element`; it declares exactly one "
                + "(PS-083, PS-528)", rejection("name: t\nfields:\n  - name: v\n    type: repeat\n"
                + "    count: 1\n"));
    }

    @Test
    public void anElementThatIsNotAMappingIsRejected() {
        assertEquals("Repeat 'v': `element` is a single field definition (PS-528)",
                rejection("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n    element: 3\n"));
    }

    @Test
    public void indexBaseIsAnInteger() {
        for (String base : new String[] {"'1'", "1.5", "true"}) {
            assertEquals("Repeat 'v': `index_base` is an integer (PS-530)",
                    rejection("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n"
                            + "    index: i\n    index_base: " + base + "\n    element: {type: u8}\n"), base);
        }
        Schema.fromYaml("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n"
                + "    index: i\n    index_base: -1\n    element: {type: u8}\n");
    }

    @Test
    public void theEncoderTakesAnArrayOfValues() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("increments", List.of(12, 15, 9));
        EncodeResult result = Schema.fromYaml(INCREMENTS).encode(data);
        assertTrue(result.isSuccess(), result.toString());
        assertArrayEquals(hex("00780096005A"), result.getPayload());
    }

    @Test
    public void aNullPositionIsRefusedOnEncode() {
        Map<String, Object> data = new LinkedHashMap<>();
        List<Object> values = new ArrayList<>();
        values.add(12);
        values.add(null);
        data.put("increments", values);
        EncodeResult result = Schema.fromYaml(INCREMENTS).encode(data);
        assertFalse(result.isSuccess());
        assertTrue(String.join("; ", result.getErrors()).contains(
                "Repeat 'increments' has null at position 1, which has no encoding (PS-529)"),
                result.getErrors().toString());
    }

    @Test
    public void indexBaseNumbersFrom1() {
        Schema schema = Schema.fromYaml("name: t\nendian: big\nfields:\n  - name: devices\n    type: repeat\n"
                + "    count: 2\n    index: i\n    index_base: 1\n    fields:\n"
                + "      - {name: id, type: u16, name_from: \"Device${i}ID\"}\n");
        List<?> devices = (List<?>) schema.decode(hex("000A000B")).get("devices");
        assertTrue(((Map<?, ?>) devices.get(0)).containsKey("Device1ID"), devices.toString());
        assertTrue(((Map<?, ?>) devices.get(1)).containsKey("Device2ID"), devices.toString());
    }

    @Test
    public void indexBaseEncodesAsItDecodes() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 2\n"
                + "    index: i\n    index_base: 1\n    fields:\n      - {name: n, type: u8}\n"
                + "      - {name: pos, type: number, ref: $i}\n");
        Map<String, Object> out = schema.decode(hex("0507"));
        List<?> records = (List<?>) out.get("v");
        assertEquals(1.0, ((Number) ((Map<?, ?>) records.get(0)).get("pos")).doubleValue());
        assertEquals(2.0, ((Number) ((Map<?, ?>) records.get(1)).get("pos")).doubleValue());
        assertArrayEquals(hex("0507"), schema.encode(out).getPayload());
    }

    @Test
    public void theMetaEntryNestsTheElement() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: temperatures\n    type: repeat\n"
                + "    count: 8\n    index: i\n    index_base: 1\n    identity: i\n"
                + "    element: {name: t, type: u16, unit: Cel}\n");
        Map<?, ?> meta = (Map<?, ?>) ((Map<?, ?>) schema.interpret(new byte[16],
                new InputContext(null, null, null)).get("_meta")).get("fields");
        // Key order is the reference's: type, element, identity.
        assertEquals("{type=repeat, element={type=u16, unit=Cel}, identity={index=i, count=8, index_base=1}}",
                String.valueOf(meta.get("temperatures")));
    }

    @Test
    public void aBaseOf0AddsNothingToTheListing() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 2\n"
                + "    index: i\n    identity: i\n    fields: [{name: a, type: u8}]\n");
        Map<?, ?> meta = (Map<?, ?>) ((Map<?, ?>) schema.interpret(new byte[] {1, 2},
                new InputContext(null, null, null)).get("_meta")).get("fields");
        assertEquals("{index=i, count=2}", String.valueOf(((Map<?, ?>) meta.get("v")).get("identity")));
    }
}
