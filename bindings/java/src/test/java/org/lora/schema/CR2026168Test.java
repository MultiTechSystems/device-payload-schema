package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-168: a definition holds a field group, one field or a lookup table (PS-345); a
 * reference to a field may rename it and replace keys whole (PS-578); a field's
 * {@code lookup} may point to a table (PS-579); references resolve transitively (PS-580).
 * The cases are tests/cr-2026-168-cases.yaml, shared with the reference, Go and C#.
 */
public class CR2026168Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-168-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    /** Numbers compared by value, so a decoded Long equals a parsed Integer. */
    private static Object normalize(Object value) {
        if (value instanceof Number n) return n.doubleValue();
        if (value instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, v) -> out.put(String.valueOf(k), normalize(v)));
            return out;
        }
        if (value instanceof List<?> l) {
            List<Object> out = new ArrayList<>();
            for (Object v : l) out.add(normalize(v));
            return out;
        }
        return value;
    }

    @TestFactory
    @SuppressWarnings("unchecked")
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                String text = String.valueOf(c.get("schema"));
                if (c.containsKey("error")) {
                    SchemaException e = assertThrows(SchemaException.class,
                            () -> Schema.fromYaml(text));
                    assertEquals(String.valueOf(c.get("error")), e.getMessage());
                    return;
                }
                Schema schema = Schema.fromYaml(text);
                byte[] payload = HexFormat.of().parseHex(String.valueOf(c.get("payload")));
                Integer port = (Integer) c.get("fport");
                Map<String, Object> decoded = port == null ? schema.decode(payload)
                        : schema.decodeWithPort(payload, port);
                Map<String, Object> expected = new Yaml().load(String.valueOf(c.get("expected")));
                Map<String, Object> values = new LinkedHashMap<>(decoded);
                values.remove("_quality");      // `valid_range` reports one beside the values
                assertEquals(normalize(expected), normalize(values));
                if (!Boolean.FALSE.equals(c.get("round_trip"))) {
                    EncodeResult encoded = port == null ? schema.encode(decoded)
                            : schema.encodeWithPort(decoded, port);
                    assertTrue(encoded.isSuccess(), encoded.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(encoded.getPayload()));
                }
                if (c.containsKey("encode")) {
                    Map<String, Object> given = new Yaml().load(String.valueOf(c.get("encode")));
                    EncodeResult again = port == null ? schema.encode(given)
                            : schema.encodeWithPort(given, port);
                    assertTrue(again.isSuccess(), again.getErrors().toString());
                    assertEquals(String.valueOf(c.get("encoded")),
                            HexFormat.of().formatHex(again.getPayload()));
                }
            }));
        }
        return tests;
    }

    /** A resolved template keeps its key order: two cases equally given, the first is written (PS-563). */
    @org.junit.jupiter.api.Test
    void aTemplateKeepsItsCaseOrder() {
        Schema schema = Schema.fromYaml("name: t\ndefinitions:\n  f:\n    field:\n      type: object\n"
                + "      fields:\n        - match:\n            length: 1\n            cases:\n"
                + "              2: [{name: a, type: u8}]\n              1: [{name: a, type: u8}]\n"
                + "fields:\n  - {$ref: '#/definitions/f', name: o}\n");
        EncodeResult out = schema.encode(new LinkedHashMap<>(Map.of("o", new LinkedHashMap<>(Map.of("a", 5)))));
        assertTrue(out.isSuccess(), out.getErrors().toString());
        assertEquals("0205", HexFormat.of().formatHex(out.getPayload()));
    }
}
