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
 * CR-2026-182 (PS-603, PS-604): a whole numeric field declaring {@code consume: 0}
 * reads in place and leaves the read position; an encoder writes nothing for it and checks
 * a value given for it. The cases are shared with the reference, the generated codec, Go and
 * C# in tests/cr-2026-182-cases.yaml.
 */
public class CR2026182Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-182-cases.yaml");
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
                    Object want = new Yaml().load(String.valueOf(c.get("expected")));
                    Map<String, Object> got = schema.decode(payload);
                    assertTrue(same(want, got), "want " + want + ", got " + got);
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

    private static boolean same(Object want, Object got) {
        if (want instanceof Number w && got instanceof Number g) {
            return w.doubleValue() == g.doubleValue();
        }
        if (want instanceof Map<?, ?> w && got instanceof Map<?, ?> g) {
            if (w.size() != g.size()) return false;
            for (Map.Entry<?, ?> e : w.entrySet()) {
                if (!g.containsKey(e.getKey()) || !same(e.getValue(), g.get(e.getKey()))) return false;
            }
            return true;
        }
        if (want instanceof List<?> w && got instanceof List<?> g) {
            if (w.size() != g.size()) return false;
            for (int i = 0; i < w.size(); i++) if (!same(w.get(i), g.get(i))) return false;
            return true;
        }
        return java.util.Objects.equals(want, got);
    }
}
