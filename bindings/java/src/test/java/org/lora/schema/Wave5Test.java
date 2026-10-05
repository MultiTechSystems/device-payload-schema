package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 0.5.2 wave 5, CR-2026-060: a lookup default may carry the value it could not map. The
 * fixture lookup-default-template.yaml holds the decode and round trip; this holds the
 * rejections, a value with a fraction, and the value's rendering.
 */
public class Wave5Test {
    private static final String SCHEMA = "name: p\nfields:\n"
        + "  - {name: sensor, type: u8, lookup: {0: Battery Voltage, 1: AIN1, default: \"AnalogSensor${value}\"}}\n"
        + "  - {name: half, type: u8, div: 2, lookup: {1: one, default: \"v${value}/${value}\"}}\n";

    @Test
    public void decodesAndRecovers() {
        Schema schema = Schema.fromYaml(SCHEMA);
        byte[][] payloads = {{1, 2}, {7, 5}, {(byte) 0xFF, 2}};
        String[][] want = {{"AIN1", "one"}, {"AnalogSensor7", "v2.5/2.5"}, {"AnalogSensor255", "one"}};
        for (int i = 0; i < payloads.length; i++) {
            Map<String, Object> out = schema.decode(payloads[i]);
            assertEquals(want[i][0], out.get("sensor"));
            assertEquals(want[i][1], out.get("half"), "2.5 is no key, not key 2");
            EncodeResult back = schema.encode(out);
            assertArrayEquals(payloads[i], back.getPayload(), String.valueOf(back.getErrors()));
        }
    }

    @Test
    public void rejections() {
        Schema schema = Schema.fromYaml(SCHEMA);
        assertFalse(schema.encode(Map.of("sensor", "Sensor7", "half", "one")).isSuccess(),
            "neither a label nor a template match (PS-409)");
        assertFalse(schema.encode(Map.of("sensor", "AIN1", "half", "v2.5/3")).isSuccess(),
            "two ${value} holding different numbers (PS-409)");
        assertThrows(SchemaException.class, () -> Schema.fromYaml(
            "name: p\nfields:\n  - {name: x, type: u8, lookup: {1: 10, default: \"x${value}\"}}\n"),
            "numeric labels beside a ${value} default (PS-407)");
    }

    @Test
    public void labelIsNotATemplate() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: x, type: u8, lookup: {1: \"a${value}\"}}\n");
        assertEquals("a${value}", schema.decode(new byte[]{1}).get("x"), "PS-408");
    }

    @Test
    public void valueRenderingMatchesJavaScript() {
        Map<Double, String> cases = Map.of(7.0, "7", -3.0, "-3", 2.5, "2.5", 1e-5, "0.00001",
            1e-6, "0.000001", 1.5e-7, "1.5e-7", 1e-8, "1e-8", 1e20, "100000000000000000000",
            1e21, "1e+21", 1.5e22, "1.5e+22");
        cases.forEach((v, want) -> assertEquals(want, Wave5.formatLookupValue(v), String.valueOf(v)));
    }
}
