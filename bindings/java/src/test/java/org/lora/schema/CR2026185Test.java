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
 * CR-2026-185 (PS-585 amended, PS-608): a checksum field within a byte_group, a tlv or
 * records entry or a repeat element is rejected at load; one in a match case, a flagged
 * group or a repeat's trailer counts from the frame; an empty covered range is an error on
 * decode and encode. The cases are shared with the reference, the generated codec, Go and
 * C# in tests/cr-2026-185-cases.yaml (its normalize list is not read here).
 */
public class CR2026185Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-185-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
    }

    /** Equal as the reference compares: numbers by value, maps and lists member by member. */
    private static boolean same(Object want, Object got) {
        if (want instanceof Number w && got instanceof Number g) {
            return w.doubleValue() == g.doubleValue();
        }
        if (want instanceof Map<?, ?> w && got instanceof Map<?, ?> g) {
            if (!w.keySet().equals(g.keySet())) return false;
            for (Object k : w.keySet()) if (!same(w.get(k), g.get(k))) return false;
            return true;
        }
        if (want instanceof List<?> w && got instanceof List<?> g) {
            if (w.size() != g.size()) return false;
            for (int i = 0; i < w.size(); i++) if (!same(w.get(i), g.get(i))) return false;
            return true;
        }
        return java.util.Objects.equals(want, got);
    }

    @TestFactory
    @SuppressWarnings("unchecked")
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
                    return;
                }
                Map<String, Object> out = schema.decode(payload);
                Map<String, Object> data = new LinkedHashMap<>();
                for (Map.Entry<String, Object> e : out.entrySet()) {
                    if (!e.getKey().startsWith("_")) data.put(e.getKey(), e.getValue());
                }
                assertTrue(same(c.get("expected"), data), "want " + c.get("expected") + ", got " + data);
                if (c.containsKey("warning")) {
                    String want = String.valueOf(c.get("warning"));
                    List<String> warnings = (List<String>) out.getOrDefault("_warnings", List.of());
                    assertTrue(warnings.stream().anyMatch(w -> w.contains(want)),
                            "want '" + want + "', got " + warnings);
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
                    assertEquals(0, result.getPayload().length, "no payload with an error");
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
