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
 * CR-2026-178 (0.5.7, PS-178, PS-596): {@code _meta} entries carry {@code display_unit}, the
 * field's {@code unit} as declared, beside {@code unit}.
 * The cases are shared with the reference, Go and C# in tests/cr-2026-178-cases.yaml.
 */
public class CR2026178Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-178-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> out = schema.interpret(
                        HexFormat.of().parseHex(String.valueOf(c.get("payload"))),
                        new InputContext(null, null, null));
                Map<?, ?> entry = (Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields");
                for (String key : String.valueOf(c.get("path")).split("\\.")) {
                    entry = (Map<?, ?>) entry.get(key);
                }
                for (String key : new String[] {"unit", "display_unit"}) {
                    String want = String.valueOf(c.get(key));
                    if (want.equals("absent")) assertFalse(entry.containsKey(key), key);
                    else assertEquals(want, entry.get(key), key);
                }
            }));
        }
        return tests;
    }
}
