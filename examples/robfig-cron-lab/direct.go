// Independent robfig/cron control. It does not run CronDelta or the Go worker.
package main

import (
	"encoding/json"
	"errors"
	"log"
	"os"
	"time"

	"github.com/robfig/cron/v3"
)

type comparisonCase struct {
	Name     string
	Timezone string
	Start    time.Time
	End      time.Time
	Right    struct {
		Expression string
		TZifPath   string `json:"tzif_path"`
	}
}

func occurrences(c comparisonCase) ([]string, error) {
	tzif, err := os.ReadFile(c.Right.TZifPath)
	if err != nil {
		return nil, err
	}
	zone, err := time.LoadLocationFromTZData(c.Timezone, tzif)
	if err != nil {
		return nil, err
	}
	parsed, err := cron.ParseStandard(c.Right.Expression)
	if err != nil {
		return nil, err
	}
	schedule, ok := parsed.(*cron.SpecSchedule)
	if !ok {
		return nil, errors.New("expected a five-field cron schedule")
	}
	schedule.Location = zone
	dates := []string{}
	for cursor := c.Start.Add(-time.Nanosecond); ; {
		next := schedule.Next(cursor)
		if next.IsZero() {
			return nil, errors.New("Schedule.Next exhausted its search horizon")
		}
		if !next.After(cursor) {
			return nil, errors.New("Schedule.Next did not advance in UTC")
		}
		if !next.Before(c.End) {
			return dates, nil
		}
		dates = append(dates, next.UTC().Format(time.RFC3339))
		cursor = next
	}
}

func main() {
	var manifest struct{ Cases []comparisonCase }
	if err := json.NewDecoder(os.Stdin).Decode(&manifest); err != nil {
		log.Fatal(err)
	}
	result := make(map[string][]string, len(manifest.Cases))
	for _, c := range manifest.Cases {
		dates, err := occurrences(c)
		if err != nil {
			log.Fatalf("%s: %v", c.Name, err)
		}
		result[c.Name] = dates
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		log.Fatal(err)
	}
}
