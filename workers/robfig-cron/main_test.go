package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"testing"
	"time"

	"github.com/robfig/cron/v3"
)

func TestEnumerate(t *testing.T) {
	const minuteZero = "2024-01-01T00:00:00.000000Z"
	const minuteOne = "2024-01-01T00:01:00.000000Z"
	const fractionAfterZero = "2024-01-01T00:00:00.000001Z"
	const fractionAfterOne = "2024-01-01T00:01:00.000001Z"
	for _, test := range []struct {
		name, expression, start, end, status string
		limit                                int
		want                                 []string
	}{
		{"exact bounds", "* * * * *", minuteZero, minuteOne, "complete", 1, []string{minuteZero}},
		{"fractional bounds", "* * * * *", fractionAfterZero, fractionAfterOne, "complete", 1, []string{minuteOne}},
		{"empty window", "* * * * *", fractionAfterZero, minuteOne, "complete", 1, nil},
		{"capped", "* * * * *", minuteZero, fractionAfterOne, "occurrence_cap", 1, []string{minuteZero}},
		{"search horizon", "0 0 31 2 *", minuteZero, minuteOne, "search_limit", 1, nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			schedule, err := cron.ParseStandard("TZ=UTC " + test.expression)
			if err != nil {
				t.Fatal(err)
			}
			var output bytes.Buffer
			w := worker{json.NewEncoder(&output)}
			if err := w.enumerate(schedule, time.UTC, parseTime(t, test.start), parseTime(t, test.end), test.limit); err != nil {
				t.Fatal(err)
			}
			var got []string
			var status string
			decoder := json.NewDecoder(&output)
			for {
				var record struct {
					Type       string
					Status     string
					Occurrence occurrence
				}
				if err := decoder.Decode(&record); err != nil {
					if !errors.Is(err, io.EOF) {
						t.Fatal(err)
					}
					break
				}
				switch record.Type {
				case "occurrence":
					got = append(got, record.Occurrence.UTC)
				case "end":
					status = record.Status
				default:
					t.Fatalf("unexpected record type: %s", record.Type)
				}
			}
			if !reflect.DeepEqual(got, test.want) || status != test.status {
				t.Errorf("occurrences %v, status %q; want %v, %q", got, status, test.want, test.status)
			}
		})
	}
}

func TestFoldEvidence(t *testing.T) {
	location, err := time.LoadLocation("Australia/Lord_Howe")
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		utc, local string
		fold       int
	}{
		{"2024-04-06T14:45:00Z", "2024-04-07T01:45:00.000000+11:00", 0},
		{"2024-04-06T15:15:00Z", "2024-04-07T01:45:00.000000+10:30", 1},
		{"2024-04-06T15:30:00Z", "2024-04-07T02:00:00.000000+10:30", 0},
	} {
		got := evidence(parseTime(t, test.utc), location)
		if got.Local != test.local || got.Fold != test.fold || got.RoundtripFold != test.fold || got.NonexistentLocal {
			t.Errorf("%s: got %+v, want local %s and fold %d", test.utc, got, test.local, test.fold)
		}
	}
}

func TestEvidencePreservesOffsetSeconds(t *testing.T) {
	timezone := time.FixedZone("LMT", 560)
	got := evidence(parseTime(t, "1900-01-01T00:00:00Z"), timezone)
	if got.Local != "1900-01-01T00:09:20.000000+00:09:20" || got.OffsetSeconds != 560 {
		t.Errorf("wrong historical offset: %+v", got)
	}
}

func parseTime(t *testing.T, text string) time.Time {
	t.Helper()
	value, err := time.Parse(time.RFC3339Nano, text)
	if err != nil {
		t.Fatal(err)
	}
	return value
}
