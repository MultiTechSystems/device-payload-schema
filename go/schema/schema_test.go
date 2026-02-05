// Copyright (c) 2024-2026 Multitech Systems, Inc.
// Author: Jason Reiss
// SPDX-License-Identifier: MIT

package schema

import (
	"bytes"
	"math"
	"testing"
)

func TestDecodeUint(t *testing.T) {
	tests := []struct {
		name   string
		data   []byte
		endian string
		want   uint64
	}{
		{"uint8", []byte{0xff}, "big", 255},
		{"uint16 big", []byte{0x01, 0x00}, "big", 256},
		{"uint16 little", []byte{0x00, 0x01}, "little", 256},
		{"uint32 big", []byte{0x00, 0x01, 0x00, 0x00}, "big", 65536},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := decodeUint(tt.data, tt.endian)
			if got != tt.want {
				t.Errorf("decodeUint() = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestDecodeSint(t *testing.T) {
	tests := []struct {
		name   string
		data   []byte
		endian string
		want   int64
	}{
		{"positive", []byte{0x7f}, "big", 127},
		{"negative byte", []byte{0xff}, "big", -1},
		{"negative short", []byte{0xff, 0xfe}, "big", -2},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := decodeSint(tt.data, tt.endian)
			if got != tt.want {
				t.Errorf("decodeSint() = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestDecodeBits(t *testing.T) {
	// 0xB4 = 0b10110100
	byteVal := byte(0xB4)

	tests := []struct {
		name      string
		bitOffset int
		bits      int
		want      int
	}{
		{"low 2 bits", 0, 2, 0},
		{"mid 4 bits", 2, 4, 13},
		{"high 2 bits", 6, 2, 2},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := decodeBits(byteVal, tt.bitOffset, tt.bits)
			if got != tt.want {
				t.Errorf("decodeBits() = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestCompactFormat(t *testing.T) {
	tests := []struct {
		name    string
		format  string
		data    []byte
		want    map[string]any
		wantErr bool
	}{
		{
			name:   "simple uint8",
			format: ">B:value",
			data:   []byte{0xff},
			want:   map[string]any{"value": float64(255)},
		},
		{
			name:   "multiple fields",
			format: ">B:a H:b B:c",
			data:   []byte{0x01, 0x00, 0x02, 0x03},
			want:   map[string]any{"a": float64(1), "b": float64(2), "c": float64(3)},
		},
		{
			name:   "little endian",
			format: "<H:value",
			data:   []byte{0x00, 0x01},
			want:   map[string]any{"value": float64(256)},
		},
		{
			name:   "signed negative",
			format: ">h:value",
			data:   []byte{0xff, 0xfe},
			want:   map[string]any{"value": float64(-2)},
		},
		{
			name:   "with skip",
			format: ">B:first 2x B:last",
			data:   []byte{0x01, 0x00, 0x00, 0xff},
			want:   map[string]any{"first": float64(1), "last": float64(255)},
		},
		{
			name:   "string",
			format: ">5s:text",
			data:   []byte("Hello"),
			want:   map[string]any{"text": "Hello"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := DecodeCompact(tt.format, tt.data)
			if (err != nil) != tt.wantErr {
				t.Errorf("DecodeCompact() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr {
				for k, v := range tt.want {
					if got[k] != v {
						t.Errorf("DecodeCompact()[%s] = %v, want %v", k, got[k], v)
					}
				}
			}
		})
	}
}

func TestSchemaBasic(t *testing.T) {
	schemaYAML := `
name: test_sensor
fields:
  - name: version
    type: UInt
    length: 1
  - name: temperature
    type: SInt
    length: 2
  - name: humidity
    type: UInt
    length: 1
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	if schema.Name != "test_sensor" {
		t.Errorf("schema.Name = %v, want test_sensor", schema.Name)
	}

	// version=1, temp=26 (big endian 0x001A), humidity=100
	data := []byte{0x01, 0x00, 0x1A, 0x64}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["version"] != float64(1) {
		t.Errorf("version = %v, want 1", result["version"])
	}
	if result["temperature"] != float64(26) {
		t.Errorf("temperature = %v, want 26", result["temperature"])
	}
	if result["humidity"] != float64(100) {
		t.Errorf("humidity = %v, want 100", result["humidity"])
	}
}

func TestSchemaWithModifiers(t *testing.T) {
	schemaYAML := `
name: scaled_sensor
fields:
  - name: temperature
    type: SInt
    length: 2
    mult: 0.1
  - name: offset_value
    type: UInt
    length: 1
    add: -40
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// temp=250 (25.0°C after *0.1), offset=100 (60 after -40)
	data := []byte{0x00, 0xFA, 0x64}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if math.Abs(result["temperature"].(float64)-25.0) > 0.001 {
		t.Errorf("temperature = %v, want 25.0", result["temperature"])
	}
	if result["offset_value"] != float64(60) {
		t.Errorf("offset_value = %v, want 60", result["offset_value"])
	}
}

func TestSchemaWithLookup(t *testing.T) {
	schemaYAML := `
name: status_sensor
fields:
  - name: status
    type: UInt
    length: 1
    lookup:
      0: "Off"
      1: "On"
      2: "Error"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x01}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["status"] != "On" {
		t.Errorf("status = %v, want 'On'", result["status"])
	}
}

func TestSchemaWithNestedObject(t *testing.T) {
	schemaYAML := `
name: nested_sensor
fields:
  - name: sensor
    type: Object
    fields:
      - name: temp
        type: SInt
        length: 2
      - name: humid
        type: UInt
        length: 1
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x00, 0x19, 0x32} // temp=25, humid=50
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	sensor, ok := result["sensor"].(map[string]any)
	if !ok {
		t.Fatalf("sensor is not a map")
	}
	if sensor["temp"] != float64(25) {
		t.Errorf("sensor.temp = %v, want 25", sensor["temp"])
	}
	if sensor["humid"] != float64(50) {
		t.Errorf("sensor.humid = %v, want 50", sensor["humid"])
	}
}

func TestSchemaWithMatch(t *testing.T) {
	schemaYAML := `
name: conditional
fields:
  - name: msg
    type: Match
    length: 1
    cases:
      - case: 1
        fields:
          - name: temp
            type: SInt
            length: 2
      - case: 2
        fields:
          - name: count
            type: UInt
            length: 4
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// Case 1: temp=25
	data1 := []byte{0x01, 0x00, 0x19}
	result1, err := schema.Decode(data1)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	msg1, ok := result1["msg"].(map[string]any)
	if !ok {
		t.Fatalf("msg is not a map")
	}
	if msg1["temp"] != float64(25) {
		t.Errorf("msg.temp = %v, want 25", msg1["temp"])
	}

	// Case 2: count=1000
	data2 := []byte{0x02, 0x00, 0x00, 0x03, 0xE8}
	result2, err := schema.Decode(data2)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	msg2, ok := result2["msg"].(map[string]any)
	if !ok {
		t.Fatalf("msg is not a map")
	}
	if msg2["count"] != float64(1000) {
		t.Errorf("msg.count = %v, want 1000", msg2["count"])
	}
}

func TestSchemaWithVariable(t *testing.T) {
	schemaYAML := `
name: variable_match
fields:
  - name: type
    type: UInt
    length: 1
    var: msg_type
  - name: len
    type: UInt
    length: 1
  - name: data
    type: Match
    on: $msg_type
    cases:
      - case: 1
        fields:
          - name: temp
            type: SInt
            length: 2
      - case: 2
        fields:
          - name: count
            type: UInt
            length: 4
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// type=1, len=2, temp=25
	data := []byte{0x01, 0x02, 0x00, 0x19}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["type"] != float64(1) {
		t.Errorf("type = %v, want 1", result["type"])
	}

	dataField, ok := result["data"].(map[string]any)
	if !ok {
		t.Fatalf("data is not a map")
	}
	if dataField["temp"] != float64(25) {
		t.Errorf("data.temp = %v, want 25", dataField["temp"])
	}
}

func TestFloat16(t *testing.T) {
	// 1.0 in float16 = 0x3C00
	data := []byte{0x3C, 0x00}
	val, err := decodeFloat(data, 2, "big")
	if err != nil {
		t.Fatalf("decodeFloat() error = %v", err)
	}
	if val != 1.0 {
		t.Errorf("float16(1.0) = %v, want 1.0", val)
	}
}

func TestBufferUnderflow(t *testing.T) {
	schemaYAML := `
name: test
fields:
  - name: value
    type: UInt
    length: 4
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x01, 0x02} // Only 2 bytes, need 4
	_, err = schema.Decode(data)
	if err == nil {
		t.Error("expected buffer underflow error")
	}
}

func TestTLVSimple(t *testing.T) {
	// Simple TLV with single-byte tags (Elsys style)
	schemaYAML := `
name: elsys_sensor
endian: big
fields:
  - type: TLV
    tag_size: 1
    cases:
      "1":
        - name: temperature
          type: SInt
          length: 2
          mult: 0.1
      "2":
        - name: humidity
          type: UInt
          length: 1
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// Tag 1 (temp) = 231 -> 23.1, Tag 2 (humid) = 30
	data := []byte{0x01, 0x00, 0xE7, 0x02, 0x1E}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	temp, ok := result["temperature"].(float64)
	if !ok || math.Abs(temp-23.1) > 0.01 {
		t.Errorf("temperature = %v, want 23.1", result["temperature"])
	}

	humid, ok := result["humidity"].(float64)
	if !ok || humid != 30 {
		t.Errorf("humidity = %v, want 30", result["humidity"])
	}
}

func TestTLVCompositeTag(t *testing.T) {
	// Milesight-style composite tag (channel_id + channel_type)
	schemaYAML := `
name: milesight_am307
endian: little
fields:
  - type: TLV
    tag_fields:
      - name: channel_id
        type: UInt
        length: 1
      - name: channel_type
        type: UInt
        length: 1
    tag_key:
      - channel_id
      - channel_type
    cases:
      "[1,117]":
        - name: battery
          type: UInt
          length: 1
      "[3,103]":
        - name: temperature
          type: SInt
          length: 2
          mult: 0.1
      "[4,104]":
        - name: humidity
          type: UInt
          length: 1
          mult: 0.5
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// Battery=100, Temp=272->27.2, Humid=90->45
	data := []byte{
		0x01, 0x75, 0x64,       // Battery: 100
		0x03, 0x67, 0x10, 0x01, // Temperature: 272 (little-endian) -> 27.2
		0x04, 0x68, 0x5A,       // Humidity: 90 -> 45
	}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	battery, ok := result["battery"].(float64)
	if !ok || battery != 100 {
		t.Errorf("battery = %v, want 100", result["battery"])
	}

	temp, ok := result["temperature"].(float64)
	if !ok || math.Abs(temp-27.2) > 0.01 {
		t.Errorf("temperature = %v, want 27.2", result["temperature"])
	}

	humid, ok := result["humidity"].(float64)
	if !ok || math.Abs(humid-45.0) > 0.01 {
		t.Errorf("humidity = %v, want 45.0", result["humidity"])
	}
}

// =============================================================================
// Bytes Type Tests
// =============================================================================

func TestBytesHex(t *testing.T) {
	schemaYAML := `
name: bytes_test
fields:
  - name: device_eui
    type: Bytes
    length: 8
    format: hex
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["device_eui"] != "0102030405060708" {
		t.Errorf("device_eui = %v, want 0102030405060708", result["device_eui"])
	}
}

func TestBytesHexUpper(t *testing.T) {
	schemaYAML := `
name: bytes_test
fields:
  - name: device_eui
    type: bytes
    length: 4
    format: "hex:upper"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0xAB, 0xCD, 0xEF, 0x12}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["device_eui"] != "ABCDEF12" {
		t.Errorf("device_eui = %v, want ABCDEF12", result["device_eui"])
	}
}

func TestBytesWithSeparator(t *testing.T) {
	schemaYAML := `
name: bytes_test
fields:
  - name: mac_addr
    type: bytes
    length: 6
    format: hex
    separator: ":"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["mac_addr"] != "aa:bb:cc:dd:ee:ff" {
		t.Errorf("mac_addr = %v, want aa:bb:cc:dd:ee:ff", result["mac_addr"])
	}
}

func TestBytesBase64(t *testing.T) {
	schemaYAML := `
name: bytes_test
fields:
  - name: payload
    type: bytes
    length: 6
    format: base64
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x21} // "Hello!"
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["payload"] != "SGVsbG8h" {
		t.Errorf("payload = %v, want SGVsbG8h", result["payload"])
	}
}

func TestBytesArray(t *testing.T) {
	schemaYAML := `
name: bytes_test
fields:
  - name: raw
    type: bytes
    length: 4
    format: array
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x01, 0x02, 0x03, 0x04}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	arr, ok := result["raw"].([]any)
	if !ok {
		t.Fatalf("raw is not an array")
	}
	if len(arr) != 4 {
		t.Errorf("len(raw) = %d, want 4", len(arr))
	}
	if arr[0] != float64(1) || arr[3] != float64(4) {
		t.Errorf("raw = %v, want [1,2,3,4]", arr)
	}
}

// =============================================================================
// Repeat Type Tests
// =============================================================================

func TestRepeatCount(t *testing.T) {
	schemaYAML := `
name: repeat_test
fields:
  - name: readings
    type: repeat
    count: 3
    fields:
      - name: temp
        type: s16
      - name: humid
        type: u8
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// 3 readings: temp=-10, humid=50; temp=25, humid=60; temp=100, humid=70
	data := []byte{
		0xFF, 0xF6, 0x32, // -10, 50
		0x00, 0x19, 0x3C, // 25, 60
		0x00, 0x64, 0x46, // 100, 70
	}

	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	readings, ok := result["readings"].([]any)
	if !ok {
		t.Fatalf("readings is not an array")
	}
	if len(readings) != 3 {
		t.Errorf("len(readings) = %d, want 3", len(readings))
	}

	r0, ok := readings[0].(map[string]any)
	if !ok {
		t.Fatalf("readings[0] is not a map")
	}
	if r0["temp"] != float64(-10) {
		t.Errorf("readings[0].temp = %v, want -10", r0["temp"])
	}
	if r0["humid"] != float64(50) {
		t.Errorf("readings[0].humid = %v, want 50", r0["humid"])
	}
}

func TestRepeatUntilEnd(t *testing.T) {
	schemaYAML := `
name: repeat_test
fields:
  - name: values
    type: repeat
    until: end
    fields:
      - name: value
        type: u16
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x00, 0x01, 0x00, 0x02, 0x00, 0x03, 0x00, 0x04}

	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	values, ok := result["values"].([]any)
	if !ok {
		t.Fatalf("values is not an array")
	}
	if len(values) != 4 {
		t.Errorf("len(values) = %d, want 4", len(values))
	}

	v0, ok := values[0].(map[string]any)
	if !ok {
		t.Fatalf("values[0] is not a map")
	}
	if v0["value"] != float64(1) {
		t.Errorf("values[0].value = %v, want 1", v0["value"])
	}
}

func TestRepeatWithVariable(t *testing.T) {
	schemaYAML := `
name: repeat_test
fields:
  - name: count
    type: u8
    var: item_count
  - name: items
    type: repeat
    count: $item_count
    fields:
      - name: id
        type: u8
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := []byte{0x03, 0x0A, 0x0B, 0x0C} // count=3, items=[10,11,12]

	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["count"] != float64(3) {
		t.Errorf("count = %v, want 3", result["count"])
	}

	items, ok := result["items"].([]any)
	if !ok {
		t.Fatalf("items is not an array")
	}
	if len(items) != 3 {
		t.Errorf("len(items) = %d, want 3", len(items))
	}
}

// =============================================================================
// Encoding Tests
// =============================================================================

func TestEncodeBasic(t *testing.T) {
	schemaYAML := `
name: test_sensor
fields:
  - name: version
    type: u8
  - name: temperature
    type: s16
  - name: humidity
    type: u8
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"version":     float64(1),
		"temperature": float64(26),
		"humidity":    float64(100),
	}

	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	expected := []byte{0x01, 0x00, 0x1A, 0x64}
	if len(encoded) != len(expected) {
		t.Errorf("len(encoded) = %d, want %d", len(encoded), len(expected))
	}
	for i := range expected {
		if encoded[i] != expected[i] {
			t.Errorf("encoded[%d] = 0x%02X, want 0x%02X", i, encoded[i], expected[i])
		}
	}
}

func TestEncodeWithModifiers(t *testing.T) {
	schemaYAML := `
name: scaled_sensor
fields:
  - name: temperature
    type: s16
    mult: 0.1
  - name: offset_value
    type: u8
    add: -40
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"temperature":  float64(25.0), // Should encode to 250
		"offset_value": float64(60),   // Should encode to 100 (60 + 40)
	}

	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	expected := []byte{0x00, 0xFA, 0x64} // 250 (big-endian), 100
	if len(encoded) != len(expected) {
		t.Errorf("len(encoded) = %d, want %d", len(encoded), len(expected))
	}
	for i := range expected {
		if encoded[i] != expected[i] {
			t.Errorf("encoded[%d] = 0x%02X, want 0x%02X", i, encoded[i], expected[i])
		}
	}
}

func TestEncodeWithDiv(t *testing.T) {
	schemaYAML := `
name: div_test
fields:
  - name: temperature
    type: s16
    div: 10
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"temperature": float64(25.0), // Should encode to 250
	}

	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	expected := []byte{0x00, 0xFA} // 250 (big-endian)
	if len(encoded) != len(expected) {
		t.Errorf("len(encoded) = %d, want %d", len(encoded), len(expected))
	}
	for i := range expected {
		if encoded[i] != expected[i] {
			t.Errorf("encoded[%d] = 0x%02X, want 0x%02X", i, encoded[i], expected[i])
		}
	}
}

func TestEncodeWithLookup(t *testing.T) {
	schemaYAML := `
name: status_sensor
fields:
  - name: status
    type: u8
    lookup:
      0: "Off"
      1: "On"
      2: "Error"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"status": "On", // Should encode to 1
	}

	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	if len(encoded) != 1 || encoded[0] != 0x01 {
		t.Errorf("encoded = %v, want [0x01]", encoded)
	}
}

func TestEncodeLittleEndian(t *testing.T) {
	schemaYAML := `
name: little_endian_test
endian: little
fields:
  - name: value
    type: u16
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"value": float64(0x1234),
	}

	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	expected := []byte{0x34, 0x12} // Little-endian
	if len(encoded) != len(expected) {
		t.Errorf("len(encoded) = %d, want %d", len(encoded), len(expected))
	}
	for i := range expected {
		if encoded[i] != expected[i] {
			t.Errorf("encoded[%d] = 0x%02X, want 0x%02X", i, encoded[i], expected[i])
		}
	}
}

func TestRoundTrip(t *testing.T) {
	schemaYAML := `
name: roundtrip_test
fields:
  - name: version
    type: u8
  - name: temperature
    type: s16
    div: 10
  - name: humidity
    type: u8
    mult: 0.5
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	original := map[string]any{
		"version":     float64(2),
		"temperature": float64(25.5),
		"humidity":    float64(45.0),
	}

	encoded, err := schema.Encode(original)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	decoded, err := schema.Decode(encoded)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	// Check values match (with floating point tolerance)
	if decoded["version"] != float64(2) {
		t.Errorf("version = %v, want 2", decoded["version"])
	}
	if math.Abs(decoded["temperature"].(float64)-25.5) > 0.01 {
		t.Errorf("temperature = %v, want 25.5", decoded["temperature"])
	}
	if math.Abs(decoded["humidity"].(float64)-45.0) > 0.01 {
		t.Errorf("humidity = %v, want 45.0", decoded["humidity"])
	}
}

// =============================================================================
// Phase 2 Tests
// =============================================================================

func TestPortBasedSelection(t *testing.T) {
	schemaYAML := `
name: port_test
endian: big
ports:
  1:
    direction: uplink
    fields:
      - name: temperature
        type: s16
        div: 10
      - name: humidity
        type: u8
  100:
    direction: downlink
    fields:
      - name: report_interval
        type: u16
  default:
    fields:
      - name: raw_byte
        type: u8
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// Port 1 uplink: temp=23.5, humid=50
	result, err := schema.DecodeWithPort([]byte{0x00, 0xEB, 0x32}, 1)
	if err != nil {
		t.Fatalf("DecodeWithPort(1) error = %v", err)
	}
	if math.Abs(result["temperature"].(float64)-23.5) > 0.01 {
		t.Errorf("temperature = %v, want 23.5", result["temperature"])
	}
	if result["humidity"] != float64(50) {
		t.Errorf("humidity = %v, want 50", result["humidity"])
	}

	// Port 100 downlink: interval=60
	result, err = schema.DecodeWithPort([]byte{0x00, 0x3C}, 100)
	if err != nil {
		t.Fatalf("DecodeWithPort(100) error = %v", err)
	}
	if result["report_interval"] != float64(60) {
		t.Errorf("report_interval = %v, want 60", result["report_interval"])
	}

	// Default port fallback
	result, err = schema.DecodeWithPort([]byte{0xAB}, 42)
	if err != nil {
		t.Fatalf("DecodeWithPort(42) error = %v", err)
	}
	if result["raw_byte"] != float64(0xAB) {
		t.Errorf("raw_byte = %v, want 171", result["raw_byte"])
	}

	// Unknown port without default should error
	noDefault, _ := ParseSchema(`
name: no_default
ports:
  1:
    fields:
      - name: x
        type: u8
`)
	_, err = noDefault.DecodeWithPort([]byte{0x01}, 99)
	if err == nil {
		t.Error("expected error for unknown port without default")
	}
}

func TestBitfieldStringHex(t *testing.T) {
	schemaYAML := `
name: version_test
endian: big
fields:
  - name: firmware_version
    type: bitfield_string
    length: 2
    delimiter: "."
    prefix: "v"
    parts:
      - [8, 8, hex]
      - [0, 8, hex]
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	result, err := schema.Decode([]byte{0x01, 0x03})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if result["firmware_version"] != "v1.3" {
		t.Errorf("firmware_version = %v, want v1.3", result["firmware_version"])
	}

	// Test hex uppercase for A
	result, err = schema.Decode([]byte{0x02, 0x0A})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if result["firmware_version"] != "v2.A" {
		t.Errorf("firmware_version = %v, want v2.A", result["firmware_version"])
	}
}

func TestBitfieldStringDecimal(t *testing.T) {
	schemaYAML := `
name: dec_version
endian: big
fields:
  - name: version
    type: bitfield_string
    length: 2
    delimiter: "."
    parts:
      - [8, 8]
      - [0, 8]
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	result, err := schema.Decode([]byte{0x03, 0x0A})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if result["version"] != "3.10" {
		t.Errorf("version = %v, want 3.10", result["version"])
	}
}

func TestFlaggedAllGroups(t *testing.T) {
	schemaYAML := `
name: flagged_test
endian: big
fields:
  - name: protocol_version
    type: u8
  - name: device_id
    type: u16
  - name: flags
    type: u16
  - flagged:
      field: flags
      groups:
        - bit: 0
          fields:
            - name: dielectric_permittivity
              type: u16
              div: 50
            - name: soil_temperature
              type: u16
              formula: "(x - 400) / 10"
        - bit: 1
          fields:
            - name: battery_voltage
              type: u16
              div: 1000
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// All groups present (flags=0x0003)
	data := []byte{0x02, 0x0C, 0x43, 0x00, 0x03, 0x01, 0x55, 0x01, 0x90, 0x0C, 0x5E}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["protocol_version"] != float64(2) {
		t.Errorf("protocol_version = %v, want 2", result["protocol_version"])
	}
	if math.Abs(result["dielectric_permittivity"].(float64)-6.82) > 0.01 {
		t.Errorf("dielectric = %v, want 6.82", result["dielectric_permittivity"])
	}
	if math.Abs(result["soil_temperature"].(float64)-0.0) > 0.1 {
		t.Errorf("soil_temperature = %v, want 0.0", result["soil_temperature"])
	}
	if math.Abs(result["battery_voltage"].(float64)-3.166) > 0.001 {
		t.Errorf("battery = %v, want 3.166", result["battery_voltage"])
	}
}

func TestFlaggedPartialGroups(t *testing.T) {
	schemaYAML := `
name: flagged_partial
endian: big
fields:
  - name: protocol_version
    type: u8
  - name: device_id
    type: u16
  - name: flags
    type: u16
  - flagged:
      field: flags
      groups:
        - bit: 0
          fields:
            - name: dielectric_permittivity
              type: u16
              div: 50
        - bit: 1
          fields:
            - name: battery_voltage
              type: u16
              div: 1000
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// Battery only (flags=0x0002)
	data := []byte{0x02, 0x0C, 0x43, 0x00, 0x02, 0x0C, 0x5E}
	result, err := schema.Decode(data)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if result["flags"] != float64(2) {
		t.Errorf("flags = %v, want 2", result["flags"])
	}
	if _, ok := result["dielectric_permittivity"]; ok {
		t.Error("dielectric_permittivity should not be present when bit 0 is clear")
	}
	if math.Abs(result["battery_voltage"].(float64)-3.166) > 0.001 {
		t.Errorf("battery = %v, want 3.166", result["battery_voltage"])
	}
}

func TestCrossFieldFormula(t *testing.T) {
	schemaYAML := `
name: computed_test
endian: big
fields:
  - name: dielectric
    type: u16
    div: 50
  - name: vwc
    type: number
    formula: "0.0000043 * pow($dielectric, 3) - 0.00055 * pow($dielectric, 2) + 0.0292 * $dielectric - 0.053"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// dielectric = 341/50 = 6.82
	result, err := schema.Decode([]byte{0x01, 0x55})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if math.Abs(result["dielectric"].(float64)-6.82) > 0.01 {
		t.Errorf("dielectric = %v, want 6.82", result["dielectric"])
	}

	expectedVWC := 0.0000043*math.Pow(6.82, 3) - 0.00055*math.Pow(6.82, 2) + 0.0292*6.82 - 0.053
	if math.Abs(result["vwc"].(float64)-expectedVWC) > 0.001 {
		t.Errorf("vwc = %v, want %v", result["vwc"], expectedVWC)
	}
}

func TestFormulaWithX(t *testing.T) {
	schemaYAML := `
name: formula_x
endian: big
fields:
  - name: soil_temperature
    type: u16
    formula: "(x - 400) / 10"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// raw=450, (450-400)/10 = 5.0
	result, err := schema.Decode([]byte{0x01, 0xC2})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if math.Abs(result["soil_temperature"].(float64)-5.0) > 0.01 {
		t.Errorf("soil_temperature = %v, want 5.0", result["soil_temperature"])
	}
}

func TestModifierYAMLKeyOrder(t *testing.T) {
	// YAML key order: add first, then div → (raw - 400) / 10
	// This is the Decentlab DL-5TM soil_temperature pattern
	schemaYAML := `
name: yaml_order_test
endian: big
fields:
  - name: soil_temperature
    type: u16
    add: -400
    div: 10
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// raw=625 (0x0271), (625 - 400) / 10 = 22.5
	result, err := schema.Decode([]byte{0x02, 0x71})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if math.Abs(result["soil_temperature"].(float64)-22.5) > 0.01 {
		t.Errorf("soil_temperature = %v, want 22.5 (add-then-div order)", result["soil_temperature"])
	}

	// Verify different order gives different result: div first, then add → (raw / 10) - 400
	schemaYAML2 := `
name: yaml_order_test2
endian: big
fields:
  - name: soil_temperature
    type: u16
    div: 10
    add: -400
`
	schema2, err := ParseSchema(schemaYAML2)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// raw=625, (625 / 10) - 400 = -337.5
	result2, err := schema2.Decode([]byte{0x02, 0x71})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if math.Abs(result2["soil_temperature"].(float64)-(-337.5)) > 0.01 {
		t.Errorf("soil_temperature = %v, want -337.5 (div-then-add order)", result2["soil_temperature"])
	}
}

func TestModifierYAMLKeyOrderRoundtrip(t *testing.T) {
	// add-then-div pattern should roundtrip correctly
	schemaYAML := `
name: roundtrip_order
endian: big
fields:
  - name: soil_temperature
    type: u16
    add: -400
    div: 10
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// Encode 22.5 → raw 625 → bytes 0x02 0x71
	encoded, err := schema.Encode(map[string]any{"soil_temperature": 22.5})
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}
	if len(encoded) != 2 || encoded[0] != 0x02 || encoded[1] != 0x71 {
		t.Errorf("Encode = %x, want 0271", encoded)
	}

	// Decode back to 22.5
	result, err := schema.Decode(encoded)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if math.Abs(result["soil_temperature"].(float64)-22.5) > 0.01 {
		t.Errorf("roundtrip soil_temperature = %v, want 22.5", result["soil_temperature"])
	}
}

func TestAlbedoFormula(t *testing.T) {
	schemaYAML := `
name: albedo_test
endian: big
fields:
  - name: incoming
    type: u16
    div: 10
  - name: reflected
    type: u16
    div: 10
  - name: albedo
    type: number
    formula: "$incoming > 0 && $reflected > 0 ? $reflected / $incoming : 0"
`

	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// incoming=100.0, reflected=50.0, albedo=0.5
	result, err := schema.Decode([]byte{0x03, 0xE8, 0x01, 0xF4})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}
	if math.Abs(result["albedo"].(float64)-0.5) > 0.01 {
		t.Errorf("albedo = %v, want 0.5", result["albedo"])
	}

	// Zero guard: incoming=0
	result, err = schema.Decode([]byte{0x00, 0x00, 0x01, 0xF4})
	if err != nil {
		t.Fatalf("Decode() zero guard error = %v", err)
	}
	if result["albedo"].(float64) != 0 {
		t.Errorf("albedo = %v, want 0 (zero guard)", result["albedo"])
	}
}

// =============================================================================
// ENCODE TESTS
// =============================================================================

func TestEncodeFlaggedAllGroups(t *testing.T) {
	schemaYAML := `
name: enc_flagged
endian: big
fields:
  - name: version
    type: u8
  - name: flags
    type: u16
  - flagged:
      field: flags
      groups:
        - bit: 0
          fields:
            - name: temperature
              type: u16
              mult: 0.1
            - name: humidity
              type: u16
              mult: 0.5
        - bit: 1
          fields:
            - name: battery
              type: u16
              div: 1000
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"version":     float64(2),
		"temperature": float64(25.0),
		"humidity":    float64(65.0),
		"battery":     float64(3.166),
	}
	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}
	// version=2, flags=0x0003, temp=250(0x00FA), humidity=130(0x0082), battery=3166(0x0C5E)
	expected := []byte{0x02, 0x00, 0x03, 0x00, 0xFA, 0x00, 0x82, 0x0C, 0x5E}
	if !bytes.Equal(encoded, expected) {
		t.Errorf("Encode() = %X, want %X", encoded, expected)
	}
}

func TestEncodeFlaggedBatteryOnly(t *testing.T) {
	schemaYAML := `
name: enc_flagged_bat
endian: big
fields:
  - name: version
    type: u8
  - name: flags
    type: u16
  - flagged:
      field: flags
      groups:
        - bit: 0
          fields:
            - name: temperature
              type: u16
              mult: 0.1
        - bit: 1
          fields:
            - name: battery
              type: u16
              div: 1000
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"version": float64(2),
		"battery": float64(3.166),
	}
	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}
	// flags=0x0002 (only bit 1), battery=3166(0x0C5E)
	if encoded[1] != 0x00 || encoded[2] != 0x02 {
		t.Errorf("flags = %X %X, want 00 02", encoded[1], encoded[2])
	}
	if len(encoded) != 5 {
		t.Errorf("len = %d, want 5", len(encoded))
	}
}

func TestEncodeBitfieldString(t *testing.T) {
	schema := &Schema{
		Name:   "enc_bfs",
		Endian: "big",
		Fields: []Field{
			{
				Name:      "fw_version",
				Type:      TypeBitfieldString,
				Length:    2,
				Prefix:    "v",
				Delimiter: ".",
				Parts:     [][]any{{float64(8), float64(8), "hex"}, {float64(0), float64(8), "hex"}},
			},
			{
				Name:      "hw_version",
				Type:      TypeBitfieldString,
				Length:    2,
				Delimiter: ".",
				Parts:     [][]any{{float64(8), float64(8)}, {float64(0), float64(8)}},
			},
		},
	}

	data := map[string]any{
		"fw_version": "v1.3",
		"hw_version": "3.10",
	}
	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}
	// fw: v1.3 → 0x0103, hw: 3.10 → 0x030A
	expected := []byte{0x01, 0x03, 0x03, 0x0A}
	if !bytes.Equal(encoded, expected) {
		t.Errorf("Encode() = %X, want %X", encoded, expected)
	}
}

func TestEncodePortBased(t *testing.T) {
	schema := &Schema{
		Name:   "enc_port",
		Endian: "big",
		Ports: map[string]*PortDef{
			"1": {Fields: []Field{{Name: "temperature", Type: TypeU16, Mult: floatPtr(0.1)}}},
			"2": {Fields: []Field{{Name: "config_interval", Type: TypeU16}}},
		},
	}

	enc1, err := schema.EncodeWithPort(map[string]any{"temperature": float64(25.0)}, 1)
	if err != nil {
		t.Fatalf("EncodeWithPort(1) error = %v", err)
	}
	if !bytes.Equal(enc1, []byte{0x00, 0xFA}) {
		t.Errorf("Port 1 = %X, want 00FA", enc1)
	}

	enc2, err := schema.EncodeWithPort(map[string]any{"config_interval": float64(300)}, 2)
	if err != nil {
		t.Fatalf("EncodeWithPort(2) error = %v", err)
	}
	if !bytes.Equal(enc2, []byte{0x01, 0x2C}) {
		t.Errorf("Port 2 = %X, want 012C", enc2)
	}
}

func TestEncodeFlaggedRoundtrip(t *testing.T) {
	schemaYAML := `
name: roundtrip_flagged
endian: big
fields:
  - name: version
    type: u8
  - name: flags
    type: u16
  - flagged:
      field: flags
      groups:
        - bit: 0
          fields:
            - name: temperature
              type: u16
              mult: 0.1
        - bit: 1
          fields:
            - name: battery
              type: u16
              div: 1000
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	data := map[string]any{
		"version":     float64(2),
		"temperature": float64(25.0),
		"battery":     float64(3.166),
	}
	encoded, err := schema.Encode(data)
	if err != nil {
		t.Fatalf("Encode() error = %v", err)
	}

	decoded, err := schema.Decode(encoded)
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if math.Abs(decoded["temperature"].(float64)-25.0) > 0.1 {
		t.Errorf("roundtrip temperature = %v, want ~25.0", decoded["temperature"])
	}
	if math.Abs(decoded["battery"].(float64)-3.166) > 0.01 {
		t.Errorf("roundtrip battery = %v, want ~3.166", decoded["battery"])
	}
}

func floatPtr(f float64) *float64 { return &f }

// Phase 2 Tests: polynomial, compute, guard, ref

func TestPolynomialEvaluation(t *testing.T) {
	// Test: y = 0.1*x^2 - 4*x + 30
	// coefficients: [0.1, -4, 30]
	schemaYAML := `
name: polynomial_test
endian: big
fields:
  - name: raw_value
    type: u16
  - name: calibrated
    type: number
    ref: $raw_value
    polynomial: [0.1, -4, 30]
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// raw_value = 100 → 0.1*100^2 - 4*100 + 30 = 1000 - 400 + 30 = 630
	decoded, err := schema.Decode([]byte{0x00, 0x64}) // 100
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if calibrated, ok := decoded["calibrated"].(float64); !ok || math.Abs(calibrated-630) > 0.01 {
		t.Errorf("polynomial result = %v, want 630", decoded["calibrated"])
	}
}

func TestComputeDiv(t *testing.T) {
	schemaYAML := `
name: compute_div_test
endian: big
fields:
  - name: incoming
    type: u16
  - name: reflected
    type: u16
  - name: ratio
    type: number
    compute:
      op: div
      a: $reflected
      b: $incoming
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// incoming = 1000, reflected = 300 → ratio = 0.3
	decoded, err := schema.Decode([]byte{0x03, 0xE8, 0x01, 0x2C})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if ratio, ok := decoded["ratio"].(float64); !ok || math.Abs(ratio-0.3) > 0.001 {
		t.Errorf("compute div result = %v, want 0.3", decoded["ratio"])
	}
}

func TestComputeMul(t *testing.T) {
	schemaYAML := `
name: compute_mul_test
endian: big
fields:
  - name: base
    type: u8
  - name: factor
    type: u8
  - name: product
    type: number
    compute:
      op: mul
      a: $base
      b: $factor
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// base = 12, factor = 5 → product = 60
	decoded, err := schema.Decode([]byte{0x0C, 0x05})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if product, ok := decoded["product"].(float64); !ok || product != 60 {
		t.Errorf("compute mul result = %v, want 60", decoded["product"])
	}
}

func TestGuardWithConditions(t *testing.T) {
	schemaYAML := `
name: guard_test
endian: big
fields:
  - name: raw_temp
    type: u16
  - name: temperature
    type: number
    ref: $raw_temp
    transform:
      - sub: 400
      - div: 10
    guard:
      when:
        - field: $raw_temp
          gt: 0
          lt: 2000
      else: -999
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// raw_temp = 650 (valid) → (650-400)/10 = 25.0
	decoded, err := schema.Decode([]byte{0x02, 0x8A})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if temp, ok := decoded["temperature"].(float64); !ok || math.Abs(temp-25.0) > 0.01 {
		t.Errorf("guard pass result = %v, want 25.0", decoded["temperature"])
	}

	// raw_temp = 0 (invalid, not > 0) → else = -999
	decoded2, err := schema.Decode([]byte{0x00, 0x00})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if temp, ok := decoded2["temperature"].(float64); !ok || temp != -999 {
		t.Errorf("guard fail result = %v, want -999", decoded2["temperature"])
	}
}

func TestRefWithTransform(t *testing.T) {
	schemaYAML := `
name: ref_transform_test
endian: big
fields:
  - name: raw_reading
    type: u16
  - name: celsius
    type: number
    ref: $raw_reading
    transform:
      - sub: 500
      - div: 10
`
	schema, err := ParseSchema(schemaYAML)
	if err != nil {
		t.Fatalf("ParseSchema() error = %v", err)
	}

	// raw_reading = 750 → (750-500)/10 = 25.0
	decoded, err := schema.Decode([]byte{0x02, 0xEE})
	if err != nil {
		t.Fatalf("Decode() error = %v", err)
	}

	if celsius, ok := decoded["celsius"].(float64); !ok || math.Abs(celsius-25.0) > 0.01 {
		t.Errorf("ref with transform result = %v, want 25.0", decoded["celsius"])
	}
}
