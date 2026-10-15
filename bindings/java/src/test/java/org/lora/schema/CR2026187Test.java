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
 * CR-2026-187 (PS-616 to PS-618): bit_labels reports the labels of a bitmask's set bits, and
 * an encoder sets the bits of the labels it is given. The cases are shared with the
 * reference, the generated codec, Go and C# in tests/cr-2026-187-cases.yaml.
 */
public class CR2026187Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-187-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
    }

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
                Map<String, Object> out = schema.decode(payload);
                Map<String, Object> data = new LinkedHashMap<>();
                for (Map.Entry<String, Object> e : out.entrySet()) {
                    if (!e.getKey().startsWith("_")) data.put(e.getKey(), e.getValue());
                }
                assertTrue(same(c.get("expected"), data), "want " + c.get("expected") + ", got " + data);
                List<String> warnings = (List<String>) out.getOrDefault("_warnings", List.of());
                List<String> want = (List<String>) c.getOrDefault("warnings", List.of());
                for (String w : want) assertTrue(warnings.contains(w), "want '" + w + "', got " + warnings);
                if (want.isEmpty()) assertTrue(warnings.isEmpty(), "unexpected " + warnings);
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
    List<DynamicTest> metaCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("meta")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> out = schema.interpret(
                        HexFormat.of().parseHex(String.valueOf(c.get("payload"))),
                        new InputContext(null, null, null));
                Map<?, ?> fields = (Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields");
                Map<?, ?> entry = (Map<?, ?>) fields.get("f");
                assertEquals(String.valueOf(c.get("reports")), entry.get("reports"));
            }));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> loadCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("load")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                String want = String.valueOf(c.get("error"));
                SchemaException e = assertThrows(SchemaException.class,
                        () -> Schema.fromYaml(String.valueOf(c.get("schema"))));
                assertTrue(e.getMessage().contains(want), "want '" + want + "', got " + e.getMessage());
            }));
        }
        return tests;
    }
}
