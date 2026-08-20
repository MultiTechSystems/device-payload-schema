package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-010: a message handled against an entry declared for the other direction is an
 * error, and no field is returned.
 *
 * <p>Before this, {@code PortDef.getDirection()} had no call site: {@code decodeWithPort}
 * matched the port number, then the default entry, and decoded whatever it found. An
 * uplink on a port declared {@code direction: downlink} came back as
 * {@code {command=0, reporting_interval=60225}} - three bytes that were a temperature and
 * a humidity. The same message text is asserted in the Python, Go and C# suites.
 */
public class CR2026010Test {

    private static final String PORTED = String.join("\n",
        "name: demo_sensor",
        "endian: big",
        "ports:",
        "  1:",
        "    direction: uplink",
        "    fields:",
        "      - {name: temperature, type: s16, div: 10}",
        "      - {name: humidity, type: u8}",
        "  2:",
        "    direction: downlink",
        "    fields:",
        "      - {name: command, type: u8}",
        "      - {name: reporting_interval, type: u16}",
        "  3:",
        "    direction: both",
        "    fields:",
        "      - {name: x, type: u8}",
        "  4:",
        "    fields:",
        "      - {name: y, type: u8}",
        "");

    private static final byte[] UPLINK_PAYLOAD = {0x00, (byte) 0xEB, 0x41};

    @Test
    public void anUplinkOnADownlinkPortIsAnError() {
        SchemaException.DecodeException caught = assertThrows(
            SchemaException.DecodeException.class,
            () -> Schema.fromYaml(PORTED).decodeWithPort(UPLINK_PAYLOAD, 2, Schema.DIRECTION_UPLINK));
        assertEquals(
            "fPort 2 is declared direction:downlink; message direction is uplink",
            caught.getMessage());
    }

    @Test
    public void aDownlinkOnAnUplinkPortIsAnError() {
        SchemaException.DecodeException caught = assertThrows(
            SchemaException.DecodeException.class,
            () -> Schema.fromYaml(PORTED).decodeWithPort(UPLINK_PAYLOAD, 1, Schema.DIRECTION_DOWNLINK));
        assertEquals(
            "fPort 1 is declared direction:uplink; message direction is downlink",
            caught.getMessage());
    }

    @Test
    public void theDeclaredDirectionStillDecodes() {
        Schema schema = Schema.fromYaml(PORTED);

        Map<String, Object> up = schema.decodeWithPort(UPLINK_PAYLOAD, 1, Schema.DIRECTION_UPLINK);
        assertEquals(23.5, ((Number) up.get("temperature")).doubleValue(), 0.001);
        assertEquals(65, ((Number) up.get("humidity")).intValue());

        Map<String, Object> down = schema.decodeWithPort(UPLINK_PAYLOAD, 2, Schema.DIRECTION_DOWNLINK);
        assertEquals(60225, ((Number) down.get("reporting_interval")).intValue());
    }

    @Test
    public void anUnstatedDirectionDecodesAsBefore() {
        // PS-290: no existing caller changes behaviour.
        Map<String, Object> result = Schema.fromYaml(PORTED).decodeWithPort(UPLINK_PAYLOAD, 2);
        assertEquals(60225, ((Number) result.get("reporting_interval")).intValue());
    }

    @Test
    public void anEntryAcceptingEitherDirectionIsNotChecked() {
        Schema schema = Schema.fromYaml(PORTED);
        // Port 3 declares `both`; port 4 declares nothing, which PS-287 reads as `both`.
        for (int fPort : new int[] {3, 4}) {
            for (String direction : new String[] {Schema.DIRECTION_UPLINK, Schema.DIRECTION_DOWNLINK}) {
                Map<String, Object> result = schema.decodeWithPort(new byte[] {0x2A}, fPort, direction);
                assertFalse(result.isEmpty(), "fPort " + fPort + " as " + direction);
            }
        }
    }

    @Test
    public void theDefaultEntryIsCheckedAndNamedAsItself() {
        // PS-289. Naming fPort 42 would describe a port the schema never defined.
        String body = String.join("\n",
            "name: t",
            "endian: big",
            "ports:",
            "  1:",
            "    direction: uplink",
            "    fields: [{name: x, type: u8}]",
            "  default:",
            "    direction: downlink",
            "    fields: [{name: raw, type: u8}]",
            "");
        SchemaException.DecodeException caught = assertThrows(
            SchemaException.DecodeException.class,
            () -> Schema.fromYaml(body).decodeWithPort(new byte[] {(byte) 0xAB}, 42, Schema.DIRECTION_UPLINK));
        assertEquals(
            "the default port entry is declared direction:downlink; message direction is uplink",
            caught.getMessage());
    }

    @Test
    public void aSchemaLevelDeclarationIsChecked() {
        // PS-291: with no ports, the declaration applies to the whole schema.
        String body = String.join("\n",
            "name: cfg",
            "endian: big",
            "direction: downlink",
            "fields: [{name: reporting_interval, type: u16}]",
            "");
        SchemaException.DecodeException caught = assertThrows(
            SchemaException.DecodeException.class,
            () -> Schema.fromYaml(body).decodeWithPort(new byte[] {0x00, 0x3C}, 0, Schema.DIRECTION_UPLINK));
        assertEquals(
            "schema 'cfg' is declared direction:downlink; message direction is uplink",
            caught.getMessage());

        Map<String, Object> result =
            Schema.fromYaml(body).decodeWithPort(new byte[] {0x00, 0x3C}, 0, Schema.DIRECTION_DOWNLINK);
        assertEquals(60, ((Number) result.get("reporting_interval")).intValue());
    }

    @Test
    public void theWithdrawnBidirectionalSpellingSurfaces() {
        String body = String.join("\n",
            "name: t",
            "endian: big",
            "ports:",
            "  7:",
            "    direction: bidirectional",
            "    fields: [{name: x, type: u8}]",
            "");
        SchemaException.DecodeException caught = assertThrows(
            SchemaException.DecodeException.class,
            () -> Schema.fromYaml(body).decodeWithPort(new byte[] {0x2A}, 7, Schema.DIRECTION_UPLINK));
        assertEquals(
            "fPort 7 declares unknown direction \"bidirectional\"; expected both, downlink, uplink",
            caught.getMessage());
    }

    @Test
    public void anUnknownMessageDirectionIsACallerError() {
        // A bad argument is a programming error, not a payload problem.
        IllegalArgumentException caught = assertThrows(
            IllegalArgumentException.class,
            () -> Schema.fromYaml(PORTED).decodeWithPort(UPLINK_PAYLOAD, 1, "sideways"));
        assertTrue(caught.getMessage().contains("unknown message direction \"sideways\""),
            caught.getMessage());
    }

    @Test
    public void anUnmatchedPortStaysAnUnmatchedPort() {
        // PS-288 keeps the two faults apart. Running the check first must not turn "the
        // schema does not define this port" into a direction complaint.
        String body = String.join("\n",
            "name: nd",
            "endian: big",
            "ports:",
            "  1:",
            "    direction: uplink",
            "    fields: [{name: x, type: u8}]",
            "");
        for (String direction : new String[] {null, Schema.DIRECTION_UPLINK, Schema.DIRECTION_DOWNLINK}) {
            SchemaException.DecodeException caught = assertThrows(
                SchemaException.DecodeException.class,
                () -> Schema.fromYaml(body).decodeWithPort(new byte[] {0x01}, 99, direction));
            assertTrue(caught.getMessage().contains("No port definition for fPort 99"),
                "direction " + direction + ": " + caught.getMessage());
        }
    }

    @Test
    public void encodingForAPortThatDisclaimsTheDirectionIsAnError() {
        // PS-292: emitting the bytes would put a malformed frame on the air.
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("temperature", 23.5);
        SchemaException.DecodeException caught = assertThrows(
            SchemaException.DecodeException.class,
            () -> Schema.fromYaml(PORTED).encodeWithPort(data, 1, Schema.DIRECTION_DOWNLINK));
        assertEquals(
            "fPort 1 is declared direction:uplink; message direction is downlink",
            caught.getMessage());
    }

    @Test
    public void encodingTheDeclaredDirectionStillWorks() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("command", 0);
        data.put("reporting_interval", 60);

        Schema schema = Schema.fromYaml(PORTED);
        assertArrayEquals(new byte[] {0x00, 0x00, 0x3C},
            schema.encodeWithPort(data, 2, Schema.DIRECTION_DOWNLINK).getPayload());
        assertArrayEquals(new byte[] {0x00, 0x00, 0x3C},
            schema.encodeWithPort(data, 2).getPayload());
    }
}
