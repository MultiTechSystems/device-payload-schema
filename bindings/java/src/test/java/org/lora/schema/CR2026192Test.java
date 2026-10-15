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
 * CR-2026-192 (PS-567, PS-569, PS-615, PS-629): a sampled repeat anchored at a time field, the
 * range of a time field on encode, and {@code _meta.time}. The cases are shared with the
 * reference, the generated codec, Go and C# in tests/cr-2026-192-cases.yaml.
 */
public class CR2026192Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-192-cases.yaml");
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
                for (Map.Entry<?, ?> f : ((Map<?, ?>) c.get("expected")).entrySet()) {
                    Map<?, ?> entry = (Map<?, ?>) fields.get(f.getKey());
                    for (Map.Entry<?, ?> k : ((Map<?, ?>) f.getValue()).entrySet()) {
                        if (k.getValue() == null) {
                            assertFalse(entry.containsKey(k.getKey()), f.getKey() + "." + k.getKey() + " is absent");
                        } else {
                            assertEquals(norm(k.getValue()), norm(entry.get(k.getKey())), f.getKey() + "." + k.getKey());
                        }
                    }
                }
            }));
        }
        return tests;
    }

    /** PS-629 in the prototype's key order: time right after reports, its keys as declared. */
    @org.junit.jupiter.api.Test
    void timeFollowsReports() throws IOException {
        Schema schema = Schema.fromYaml(String.valueOf(section("meta").get(0).get("schema")));
        Map<String, Object> out = schema.interpret(new byte[8], new InputContext(null, null, null));
        Map<?, ?> entry = (Map<?, ?>) ((Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields")).get("ts");
        assertEquals(List.of("type", "reports", "time"), new ArrayList<>(entry.keySet()).subList(0, 3));
        assertEquals(List.of("format", "epoch", "fraction"), new ArrayList<>(((Map<?, ?>) entry.get("time")).keySet()));
    }
}
