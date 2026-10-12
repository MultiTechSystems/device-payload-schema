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
 * CR-2026-141 (PS-551): a bit range or bool that does not consume, whose bits the fields
 * after it write, is derived where the input omits it and writes no bits; where any of its
 * bits is written by no other field it is missing, and a supplied one must agree with the
 * bits the frame holds. The cases are tests/cr-2026-141-cases.yaml, shared with the
 * reference, the generated codec, Go and C#.
 */
public class CR2026141Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-141-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    /** Equal as JSON values: numbers by value, whatever their Java type. */
    private static boolean same(Object want, Object got) {
        if (want instanceof Number w && got instanceof Number g) {
            return w.doubleValue() == g.doubleValue();
        }
        if (want instanceof Map<?, ?> w && got instanceof Map<?, ?> g) {
            if (w.size() != g.size()) return false;
            for (Map.Entry<?, ?> e : w.entrySet()) {
                if (!g.containsKey(String.valueOf(e.getKey()))
                        || !same(e.getValue(), g.get(String.valueOf(e.getKey())))) return false;
            }
            return true;
        }
        return want == null ? got == null : want.equals(got);
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = schema.encode(input);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    assertEquals(0, result.getPayload().length, result.toString());
                    return;
                }
                assertTrue(result.isSuccess(), result.getErrors().toString());
                assertEquals(String.valueOf(c.get("payload")),
                        HexFormat.of().formatHex(result.getPayload()));
                assertTrue(result.getWarnings().isEmpty(), result.getWarnings().toString());
                if (c.containsKey("decoded")) {
                    Map<String, Object> want = new Yaml().load(String.valueOf(c.get("decoded")));
                    Map<String, Object> got = Schema.fromYaml(String.valueOf(c.get("schema")))
                            .decode(result.getPayload());
                    assertTrue(same(want, got), "want " + want + ", got " + got);
                }
            }));
        }
        return tests;
    }
}
