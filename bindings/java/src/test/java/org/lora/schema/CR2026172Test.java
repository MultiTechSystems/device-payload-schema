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
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-172 (PS-119, PS-584): compute {@code pow}; a result that is not a finite real number
 * omits the field. The cases are shared with the reference, the generated codec, Go and C# in
 * tests/cr-2026-172-cases.yaml.
 */
public class CR2026172Test {

    @SuppressWarnings("unchecked")
    @TestFactory
    List<DynamicTest> decodeCases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-172-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : (List<Map<String, Object>>) doc.get("decode_cases")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> got = schema.decode(
                        HexFormat.of().parseHex(String.valueOf(c.get("payload"))));
                Map<String, Object> want = new Yaml().load(String.valueOf(c.get("expected")));
                assertEquals(new TreeSet<>(want.keySet()), new TreeSet<>(got.keySet()));
                for (Map.Entry<String, Object> e : want.entrySet()) {
                    double w = ((Number) e.getValue()).doubleValue();
                    double g = ((Number) got.get(e.getKey())).doubleValue();
                    assertEquals(w, g, 1e-9 * Math.max(1, Math.abs(w)), e.getKey());
                }
            }));
        }
        return tests;
    }
}
