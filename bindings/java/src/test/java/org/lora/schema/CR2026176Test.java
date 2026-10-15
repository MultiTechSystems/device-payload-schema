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
 * CR-2026-176 (PS-361 amended, PS-590 to PS-592): template string fields. The cases are
 * shared with the reference, the generated codec, Go and C# in tests/cr-2026-176-cases.yaml.
 */
public class CR2026176Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-176-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
    }

    /** Numbers compared by value, whatever boxed type each side holds. */
    @SuppressWarnings("unchecked")
    private static Object norm(Object v) {
        if (v instanceof Number n) {
            double d = n.doubleValue();
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
    @SuppressWarnings("unchecked")
    List<DynamicTest> decodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("decode")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> got = new LinkedHashMap<>(
                        schema.decode(HexFormat.of().parseHex(String.valueOf(c.get("payload")))));
                List<String> warnings = (List<String>) got.remove("_warnings");
                if (warnings == null) warnings = List.of();
                got.remove("_quality");
                assertEquals(norm(c.get("expected")), norm(got));
                if (c.containsKey("warning")) {
                    String want = String.valueOf(c.get("warning"));
                    assertTrue(warnings.stream().anyMatch(w -> w.contains(want)),
                            "want '" + want + "', got " + warnings);
                } else {
                    assertTrue(warnings.stream().noneMatch(w -> w.contains("PS-591")), warnings.toString());
                }
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
}
