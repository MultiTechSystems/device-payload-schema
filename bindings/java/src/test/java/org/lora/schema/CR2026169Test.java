package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
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
 * CR-2026-169 (0.5.7, PS-581): which port entry an encoder uses. A caller's FPort chooses
 * its entry (or {@code default}), an uplink entry is refused only for a downlink; without
 * an FPort the single declared downlink entry, else the single downlink-capable one, else a
 * rejection. The cases are shared with the reference, the generated codec, Go and C# in
 * tests/cr-2026-169-cases.yaml. A zero fPort is none given: ports are 1 to 255.
 */
public class CR2026169Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-169-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                int fPort = c.containsKey("fport") ? ((Number) c.get("fport")).intValue() : 0;
                String direction = "none".equals(c.get("direction")) ? null : Schema.DIRECTION_DOWNLINK;
                EncodeResult result = schema.encodeWithPort(input, fPort, direction);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    assertEquals(0, result.getPayload().length);
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                    assertEquals(((Number) c.get("used")).intValue(), result.getFPort());
                }
            }));
        }
        return tests;
    }

    /** PS-459 is a decoder's rule: an encoder given no FPort chooses an entry. */
    @Test
    void encodeWithoutAPortChoosesTheDownlinkEntry() {
        Schema schema = Schema.fromYaml(
                "name: t\nports:\n  2: {direction: downlink, fields: [{name: a, type: u8}]}\n");
        EncodeResult result = schema.encode(Map.of("a", 3));
        assertEquals("03", HexFormat.of().formatHex(result.getPayload()));
        assertEquals(2, result.getFPort());
    }
}
