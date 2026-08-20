package schema

import (
    "encoding/hex"
    "os"
    "testing"
    "math"
)

func TestDraginoLAQ4Schema(t *testing.T) {
    laq4, err := os.ReadFile("../../schemas/devices/dragino/laq4.yaml")
    if err != nil {
        t.Fatalf("Error reading LAQ4: %v", err)
    }
    
    s, err := ParseSchema(string(laq4))
    if err != nil {
        t.Fatalf("Error parsing LAQ4: %v", err)
    }
    
    // Test CO2 mode payload
    payload, _ := hex.DecodeString("0BB80400C803E800FA0258")
    result, err := s.DecodeWithPort(payload, 2)
    if err != nil {
        t.Fatalf("Error decoding LAQ4: %v", err)
    }
    
    if mustNum(result["battery"]) != 3.0 {
        t.Errorf("battery = %v, want 3.0", result["battery"])
    }
    if mustNum(result["mode"]) != 1 {
        t.Errorf("mode = %v, want 1", result["mode"])
    }
    if mustNum(result["co2"]) != 1000 {
        t.Errorf("co2 = %v, want 1000", result["co2"])
    }
}

func TestDigitalMatterOysterS24(t *testing.T) {
    oyster, err := os.ReadFile("../../schemas/devices/digital-matter/oyster.yaml")
    if err != nil {
        t.Fatalf("Error reading Oyster: %v", err)
    }
    
    s, err := ParseSchema(string(oyster))
    if err != nil {
        t.Fatalf("Error parsing Oyster: %v", err)
    }
    
    // Port 1: Full precision GPS (s32)
    payload1, _ := hex.DecodeString("0008D0EB48B5205A003278")
    result1, err := s.DecodeWithPort(payload1, 1)
    if err != nil {
        t.Fatalf("Error decoding Oyster port 1: %v", err)
    }
    
    lat1 := mustNum(result1["latitude"])
    lon1 := mustNum(result1["longitude"])
    if math.Abs(lat1 - (-33.8688)) > 0.0001 {
        t.Errorf("Port 1 latitude = %v, want -33.8688", lat1)
    }
    if math.Abs(lon1 - 151.2093) > 0.0001 {
        t.Errorf("Port 1 longitude = %v, want 151.2093", lon1)
    }
    
    // Port 4: Compact GPS with s24
    payload4, _ := hex.DecodeString("1F0AFAE1F505287801")
    result4, err := s.DecodeWithPort(payload4, 4)
    if err != nil {
        t.Fatalf("Error decoding Oyster port 4: %v", err)
    }
    
    lat4 := mustNum(result4["latitude"])
    lon4 := mustNum(result4["longitude"])
    if math.Abs(lat4 - (-10.0)) > 0.01 {
        t.Errorf("Port 4 latitude = %v, want -10.0", lat4)
    }
    if math.Abs(lon4 - 10.0) > 0.01 {
        t.Errorf("Port 4 longitude = %v, want 10.0", lon4)
    }
    
    t.Logf("Port 1 Sydney: lat=%v, lon=%v", lat1, lon1)
    t.Logf("Port 4 Compact: lat=%v, lon=%v", lat4, lon4)
}
