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
 * CR-2026-186 (PS-610 to PS-615): a numeric field declaring {@code time} is reported as an
 * instant in UTC. The cases are shared with the reference, the generated codec, Go and C# in
 * tests/cr-2026-186-cases.yaml.
 */
public class CR2026186Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-186-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
    }

    /** Numbers compared by value, whatever boxed type each side holds. */
    private static Object norm(Object v) {
        if (v instanceof Number n) {
            double d = n.doubleValue();
            if (n instanceof Long || n instanceof Integer || n instanceof java.math.BigInteger) {
                return n.longValue();
            }
            return d == Math.rint(d) && !Double.isInfinite(d) ? (Object) (long) d : (Object) d;
        }
        if (v instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : m.entrySet()) out.put(String.valueOf(e.getKey()), norm(e.getValue()));
            return out;
        }
        if (v instanceof List<?> l) {
            List<Object> out = new ArrayList<>();
            for (Object o : l) out.add(norm(o));
            return out;
        }
        return v;
    }

    @TestFactory
    List<DynamicTest> decodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("decode")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                byte[] payload = HexFormat.of().parseHex(String.valueOf(c.get("payload")));
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    SchemaException e = assertThrows(SchemaException.class, () -> schema.decode(payload));
                    assertTrue(e.getMessage().contains(want), "want '" + want + "', got " + e.getMessage());
                    return;
                }
                Map<String, Object> got = new LinkedHashMap<>(schema.decode(payload));
                if (c.containsKey("warning")) {     // PS-610, PS-614 (amended): omitted, warned
                    String want = String.valueOf(c.get("warning"));
                    Object warnings = got.get("_warnings");
                    assertTrue(warnings instanceof List<?> l && l.stream().anyMatch(w -> String.valueOf(w).contains(want)),
                            "want a warning '" + want + "', got " + warnings);
                }
                got.remove("_warnings");
                got.remove("_quality");
                assertEquals(norm(c.get("expected")), norm(got));
            }));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> encodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("cases")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = schema.encode(input);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertFalse(result.isSuccess(), "want '" + want + "', got a payload");
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    return;
                }
                assertTrue(result.isSuccess(), result.getErrors().toString());
                assertEquals(String.valueOf(c.get("payload")),
                        HexFormat.of().formatHex(result.getPayload()));
            }));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> loadCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("load")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                SchemaException e = assertThrows(SchemaException.class,
                        () -> Schema.fromYaml(String.valueOf(c.get("schema"))));
                assertTrue(e.getMessage().contains(String.valueOf(c.get("error"))),
                        "want '" + c.get("error") + "', got " + e.getMessage());
            }));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> metaCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("meta")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> out = schema.interpret(
                        HexFormat.of().parseHex(String.valueOf(c.get("payload"))),
                        new InputContext(null, null, null));
                Map<?, ?> fields = (Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields");
                assertEquals(String.valueOf(c.get("reports")), ((Map<?, ?>) fields.get("f")).get("reports"));
            }));
        }
        return tests;
    }

    /** The fixture's port 2 frame, decoded and encoded back. */
    @org.junit.jupiter.api.Test
    void fixturePortTwo() throws IOException {
        Schema schema = Schema.fromYamlFile(Path.of("..", "..", "schemas", "devices",
                "_language-conformance", "time-field.yaml"));
        byte[] payload = HexFormat.of().parseHex("54A48E12");
        Map<String, Object> got = schema.decodeWithPort(payload, 2);
        assertEquals("2025-01-05T00:00:01.000Z", got.get("timestamp"));
        EncodeResult back = schema.encodeWithPort(Map.of("timestamp", got.get("timestamp")), 2);
        assertTrue(back.isSuccess(), back.getErrors().toString());
        assertArrayEquals(payload, back.getPayload());
    }
}
