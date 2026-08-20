package schema

import (
	"strings"
	"testing"
)

// CR-2026-010: a message handled against an entry declared for the other direction is
// an error, and no field is returned.
//
// Before this, Direction was parsed into PortDef and never read: DecodeWithPort matched
// the port number, then the default entry, and decoded whatever it found. An uplink on a
// port declared `direction: downlink` came back as command=0, reporting_interval=60225 -
// three bytes that were a temperature and a humidity, reported as a configuration value
// with no error. The same message text is asserted in the Python, C# and Java suites.
const cr010Schema = `
name: demo_sensor
endian: big
ports:
  1:
    direction: uplink
    fields:
      - {name: temperature, type: s16, div: 10}
      - {name: humidity, type: u8}
  2:
    direction: downlink
    fields:
      - {name: command, type: u8}
      - {name: reporting_interval, type: u16}
  3:
    direction: both
    fields:
      - {name: x, type: u8}
  4:
    fields:
      - {name: y, type: u8}
`

var cr010Payload = []byte{0x00, 0xEB, 0x41}

func cr010Parse(t *testing.T, body string) *Schema {
	t.Helper()
	s, err := ParseSchema(body)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}
	return s
}

func TestDirectionMismatchIsAnError(t *testing.T) {
	s := cr010Parse(t, cr010Schema)

	cases := []struct {
		name      string
		fPort     int
		direction string
		want      string
	}{
		{"uplink on a downlink port", 2, DirectionUplink,
			"fPort 2 is declared direction:downlink; message direction is uplink"},
		{"downlink on an uplink port", 1, DirectionDownlink,
			"fPort 1 is declared direction:uplink; message direction is downlink"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			result, err := s.DecodeWithPortDirection(cr010Payload, tc.fPort, tc.direction)
			if err == nil {
				t.Fatalf("expected an error, got result = %v", result)
			}
			if err.Error() != tc.want {
				t.Errorf("error = %q, want %q", err.Error(), tc.want)
			}
			// The point of the requirement: no plausible-looking number reaches a
			// consumer.
			if result != nil {
				t.Errorf("result = %v, want nil", result)
			}
		})
	}
}

func TestDirectionAgreementStillDecodes(t *testing.T) {
	s := cr010Parse(t, cr010Schema)

	result, err := s.DecodeWithPortDirection(cr010Payload, 1, DirectionUplink)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mustNum(result["humidity"]) != float64(65) {
		t.Errorf("humidity = %v, want 65", result["humidity"])
	}

	result, err = s.DecodeWithPortDirection(cr010Payload, 2, DirectionDownlink)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mustNum(result["reporting_interval"]) != float64(60225) {
		t.Errorf("reporting_interval = %v, want 60225", result["reporting_interval"])
	}
}

func TestDirectionCheckIsOptInOnBothSides(t *testing.T) {
	s := cr010Parse(t, cr010Schema)

	// PS-290: an unstated direction decodes as before, so no existing caller changes.
	if _, err := s.DecodeWithPort(cr010Payload, 2); err != nil {
		t.Errorf("DecodeWithPort with no direction: unexpected error %v", err)
	}
	if _, err := s.DecodeWithPortDirection(cr010Payload, 2, ""); err != nil {
		t.Errorf("empty direction: unexpected error %v", err)
	}

	// PS-287: `both`, and an entry declaring nothing, accept either direction.
	for _, fPort := range []int{3, 4} {
		for _, direction := range []string{DirectionUplink, DirectionDownlink} {
			if _, err := s.DecodeWithPortDirection([]byte{0x2A}, fPort, direction); err != nil {
				t.Errorf("fPort %d as %s: unexpected error %v", fPort, direction, err)
			}
		}
	}
}

func TestDirectionOfTheSelectedEntry(t *testing.T) {
	// PS-289: the default entry is checked, and named as itself. Naming fPort 42 would
	// describe a port the schema never defined.
	s := cr010Parse(t, `
name: t
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: x, type: u8}]
  default:
    direction: downlink
    fields: [{name: raw, type: u8}]
`)
	_, err := s.DecodeWithPortDirection([]byte{0xAB}, 42, DirectionUplink)
	if err == nil {
		t.Fatal("expected an error for the default entry")
	}
	want := "the default port entry is declared direction:downlink; message direction is uplink"
	if err.Error() != want {
		t.Errorf("error = %q, want %q", err.Error(), want)
	}
}

func TestSchemaLevelDirection(t *testing.T) {
	// PS-291: with no ports, the declaration applies to the whole schema.
	s := cr010Parse(t, `
name: cfg
endian: big
direction: downlink
fields: [{name: reporting_interval, type: u16}]
`)
	_, err := s.DecodeWithPortDirection([]byte{0x00, 0x3C}, 0, DirectionUplink)
	if err == nil {
		t.Fatal("expected an error for the schema-level declaration")
	}
	want := "schema 'cfg' is declared direction:downlink; message direction is uplink"
	if err.Error() != want {
		t.Errorf("error = %q, want %q", err.Error(), want)
	}

	result, err := s.DecodeWithPortDirection([]byte{0x00, 0x3C}, 0, DirectionDownlink)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mustNum(result["reporting_interval"]) != float64(60) {
		t.Errorf("reporting_interval = %v, want 60", result["reporting_interval"])
	}
}

func TestWithdrawnAndUnknownDirectionValues(t *testing.T) {
	// `bidirectional` appeared in a clause 5 example and is withdrawn, so a schema
	// carrying it surfaces rather than being read as `both`.
	s := cr010Parse(t, `
name: t
endian: big
ports:
  7:
    direction: bidirectional
    fields: [{name: x, type: u8}]
`)
	_, err := s.DecodeWithPortDirection([]byte{0x2A}, 7, DirectionUplink)
	if err == nil {
		t.Fatal("expected an error for the withdrawn spelling")
	}
	want := `fPort 7 declares unknown direction "bidirectional"; expected both, downlink, uplink`
	if err.Error() != want {
		t.Errorf("error = %q, want %q", err.Error(), want)
	}

	// A bad direction argument is a caller error, not a payload problem.
	_, err = cr010Parse(t, cr010Schema).DecodeWithPortDirection(cr010Payload, 1, "sideways")
	if err == nil || !strings.Contains(err.Error(), `unknown message direction "sideways"`) {
		t.Errorf("error = %v, want it to name the bad argument", err)
	}
}

func TestEncodeDirection(t *testing.T) {
	// PS-292: the mirror of the decode check, so a frame is not built for an entry that
	// disclaims the direction it would travel.
	s := cr010Parse(t, cr010Schema)

	payload, err := s.EncodeWithPortDirection(map[string]any{"temperature": 23.5}, 1, DirectionDownlink)
	if err == nil {
		t.Fatalf("expected an error, got payload = %v", payload)
	}
	want := "fPort 1 is declared direction:uplink; message direction is downlink"
	if err.Error() != want {
		t.Errorf("error = %q, want %q", err.Error(), want)
	}
	if payload != nil {
		t.Errorf("payload = %v, want nil: emitting it would put a malformed frame on the air", payload)
	}

	if _, err := s.EncodeWithPortDirection(map[string]any{"command": 0, "reporting_interval": 60}, 2, DirectionDownlink); err != nil {
		t.Errorf("encoding the declared direction: unexpected error %v", err)
	}
	if _, err := s.EncodeWithPort(map[string]any{"command": 0, "reporting_interval": 60}, 2); err != nil {
		t.Errorf("unstated direction: unexpected error %v", err)
	}
}

func TestUnmatchedPortStaysUnmatched(t *testing.T) {
	// PS-288 keeps the two faults apart. Running the check first must not turn "the
	// schema does not define this port" into a direction complaint.
	s := cr010Parse(t, `
name: nd
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: x, type: u8}]
`)
	for _, direction := range []string{"", DirectionUplink, DirectionDownlink} {
		_, err := s.DecodeWithPortDirection([]byte{0x01}, 99, direction)
		if err == nil || !strings.Contains(err.Error(), "no port definition for fPort 99") {
			t.Errorf("direction %q: error = %v, want the unmatched-port error", direction, err)
		}
	}
}
