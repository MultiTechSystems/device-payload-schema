package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-150 (PS-560, PS-561): an ascii field with {@code numeral} reports its digits as an
 * integer, strictly, and writes them back canonically. The cases are shared with the
 * reference, the generated codec, Go and C# in tests/cr-2026-173-cases.yaml.
 */
public class CR2026173Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-173-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
    }

    /** Numbers as doubles, at any depth, so 1 and 1.0 compare equal. */
    private static Object normalize(Object v) {
        if (v instanceof Number n) return n.doubleValue();
        if (v instanceof Map<?, ?> m) {
            Map<String, Object> out = new java.util.TreeMap<>();
            m.forEach((k, x) -> out.put(String.valueOf(k), normalize(x)));
            return out;
        }
        if (v instanceof List<?> l) return l.stream().map(CR2026173Test::normalize).toList();
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
                    SchemaException e = assertThrows(SchemaException.class, () -> schema.decode(payload));
                    assertTrue(e.getMessage().contains(String.valueOf(c.get("error"))),
                            "want '" + c.get("error") + "', got " + e.getMessage());
                } else {
                    Object want = new Yaml().load(String.valueOf(c.get("expected")));
                    assertEquals(normalize(want), normalize(schema.decode(payload)));
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
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                }
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
