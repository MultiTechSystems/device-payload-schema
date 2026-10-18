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
 * CR-2026-200 (PS-422, PS-643, PS-644): {@code encoding: bcd_signed}, on decode,
 * encode and load. The cases are shared with the reference, the generated codec, Go and C#
 * in tests/cr-2026-200-cases.yaml.
 */
public class CR2026200Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-200-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
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
                    Map<String, Object> got = new java.util.LinkedHashMap<>(schema.decode(payload));
                    Object warned = got.remove("_warnings");
                    if (c.containsKey("warnings")) {
                        assertEquals(String.valueOf(c.get("warnings")),
                                String.valueOf(warned == null ? List.of() : warned));
                    }
                    Map<?, ?> want = (Map<?, ?>) c.get("expected");
                    assertEquals(want.size(), got.size(), "got " + got);
                    for (Map.Entry<?, ?> e : want.entrySet()) {
                        Object value = got.get(e.getKey());
                        if (e.getValue() instanceof Number w && value instanceof Number g) {
                            // 0 and 0.0 alike; a negative zero must read as positive (PS-643).
                            assertEquals(w.doubleValue(), g.doubleValue(), 1e-9);
                            assertFalse(Double.doubleToRawLongBits(g.doubleValue()) == Long.MIN_VALUE,
                                    "negative zero reported");
                        } else {
                            assertEquals(String.valueOf(e.getValue()), String.valueOf(value));
                        }
                    }
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
                    if (c.containsKey("warnings")) {
                        assertEquals(String.valueOf(c.get("warnings")), String.valueOf(result.getWarnings()));
                    }
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
