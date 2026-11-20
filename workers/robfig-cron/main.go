// Command robfig-cron implements CronDelta's bounded NDJSON worker protocol.
package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/debug"
	"strings"
	"time"
	_ "time/tzdata" // Match the Kubernetes fallback when host timezone data is absent.

	"github.com/robfig/cron/v3"
)

const (
	modulePath   = "github.com/robfig/cron/v3"
	version      = "v3.0.1"
	requestLimit = 64 * 1024
	tzifLimit    = 10 * 1024 * 1024
	utcLayout    = "2006-01-02T15:04:05.000000Z"
)

var zoneKey = regexp.MustCompile(`^[A-Za-z0-9_+/-]+$`)

type profile struct {
	Engine     string         `json:"engine"`
	Expression string         `json:"expression"`
	Options    map[string]any `json:"options"`
	Worker     string         `json:"worker"`
	Version    string         `json:"version"`
	TZifPath   *string        `json:"tzif_path"`
	TZifSHA256 *string        `json:"tzif_sha256"`
}

type request struct {
	ProtocolVersion int       `json:"protocol_version"`
	Profile         profile   `json:"profile"`
	Timezone        string    `json:"timezone"`
	Start           time.Time `json:"start"`
	End             time.Time `json:"end"`
	MaxOccurrences  int       `json:"max_occurrences"`
}

type timezoneProvenance struct {
	Provider      string   `json:"provider"`
	Source        *string  `json:"source"`
	Key           string   `json:"key"`
	TZdataVersion *string  `json:"tzdata_version"`
	TZifSHA256    *string  `json:"tzif_sha256"`
	Unavailable   []string `json:"unavailable"`
}

type occurrence struct {
	Local            string `json:"local"`
	UTC              string `json:"utc"`
	OffsetSeconds    int    `json:"offset_seconds"`
	Fold             int    `json:"fold"`
	RoundtripLocal   string `json:"roundtrip_local"`
	RoundtripFold    int    `json:"roundtrip_fold"`
	NonexistentLocal bool   `json:"nonexistent_local"`
}

type worker struct {
	output *json.Encoder
}

func (w worker) emit(kind string, fields map[string]any) error {
	if fields == nil {
		fields = make(map[string]any)
	}
	fields["protocol_version"] = 1
	fields["type"] = kind
	return w.output.Encode(fields)
}

func (w worker) end(status, detail string) error {
	return w.emit("end", map[string]any{"status": status, "detail": detail})
}

func readBounded(reader io.Reader, limit int) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(reader, int64(limit)+1))
	if err == nil && len(data) > limit {
		return nil, fmt.Errorf("input exceeds %d bytes", limit)
	}
	return data, err
}

func readTZif(path, key string) (*time.Location, string, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, "", err
	}
	defer file.Close()
	data, err := readBounded(file, tzifLimit)
	if err != nil {
		return nil, "", err
	}
	loc, err := time.LoadLocationFromTZData(key, data)
	if err != nil {
		return nil, "", err
	}
	return loc, fmt.Sprintf("%x", sha256.Sum256(data)), nil
}

func explicitLocation(key string) bool {
	if strings.EqualFold(key, "Local") || !zoneKey.MatchString(key) {
		return false
	}
	for _, part := range strings.Split(key, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

func timezoneSource(key string) timezoneProvenance {
	result := timezoneProvenance{Provider: "go", Key: key, Unavailable: []string{"tzdata_version", "tzif_sha256"}}
	if key == "UTC" {
		source := "Go time.UTC"
		result.Source = &source
	} else {
		// LoadLocation does not expose which of its fallback sources provided the data.
		result.Unavailable = append(result.Unavailable, "source")
	}
	return result
}

func moduleProvenance() (*debug.Module, error) {
	info, ok := debug.ReadBuildInfo()
	if ok {
		for _, dependency := range info.Deps {
			if dependency.Path == modulePath && dependency.Replace == nil && dependency.Version != "" && dependency.Sum != "" {
				return dependency, nil
			}
		}
	}
	return nil, fmt.Errorf("build provenance unavailable for %s", modulePath)
}

func (w worker) emitProfile(requested profile, tz timezoneProvenance, dependency *debug.Module) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	realpath, err := filepath.EvalSymlinks(executable)
	if err != nil {
		return err
	}
	return w.emit("profile", map[string]any{"profile": struct {
		profile
		RequestedVersion string             `json:"requested_version"`
		ActualVersion    string             `json:"actual_version"`
		Interpreter      string             `json:"interpreter"`
		Runtime          map[string]string  `json:"runtime"`
		Loaded           map[string]any     `json:"loaded"`
		Timezone         timezoneProvenance `json:"timezone"`
	}{
		profile:          requested,
		RequestedVersion: requested.Version,
		ActualVersion:    dependency.Version,
		Interpreter:      executable,
		Runtime: map[string]string{
			"implementation":      "Go",
			"version":             runtime.Version(),
			"platform":            runtime.GOOS,
			"arch":                runtime.GOARCH,
			"executable":          executable,
			"executable_realpath": realpath,
		},
		Loaded: map[string]any{modulePath: map[string]string{
			"version": dependency.Version,
			"sum":     dependency.Sum,
		}},
		Timezone: tz,
	}})
}

func fiveFields(expression string) bool {
	fields := strings.Fields(expression)
	if len(fields) != 5 || strings.HasPrefix(fields[0], "@") {
		return false
	}
	for _, field := range fields {
		if strings.HasPrefix(field, "TZ=") || strings.HasPrefix(field, "CRON_TZ=") {
			return false
		}
	}
	return true
}

func (w worker) run(req request) error {
	requested := req.Profile
	if req.ProtocolVersion != 1 || requested.Engine != "robfig/cron" ||
		!req.Start.Before(req.End) || req.MaxOccurrences < 1 ||
		(requested.TZifPath == nil) != (requested.TZifSHA256 == nil) {
		return fmt.Errorf("invalid worker request")
	}
	if !explicitLocation(req.Timezone) {
		return w.end("invalid_timezone", fmt.Sprintf("timezone %q is not an explicit IANA key", req.Timezone))
	}
	if _, err := time.LoadLocation(req.Timezone); err != nil {
		return w.end("invalid_timezone", err.Error())
	}
	var pinned *time.Location
	tz := timezoneSource(req.Timezone)
	if requested.TZifPath != nil {
		loc, hash, err := readTZif(*requested.TZifPath, req.Timezone)
		if err != nil {
			return fmt.Errorf("load requested TZif: %w", err)
		}
		pinned = loc
		source := "Go time.LoadLocationFromTZData"
		tz.Source, tz.TZifSHA256 = &source, &hash
		tz.Unavailable = []string{"tzdata_version"}
	}
	dependency, err := moduleProvenance()
	if err != nil {
		return err
	}
	if err := w.emitProfile(requested, tz, dependency); err != nil {
		return err
	}
	if dependency.Version != requested.Version || dependency.Version != version {
		return w.end("version_mismatch", "worker was not built with the requested module version")
	}
	if pinned != nil && *tz.TZifSHA256 != *requested.TZifSHA256 {
		return w.end("tzdata_mismatch", "loaded TZif bytes do not match the requested SHA-256")
	}
	if !fiveFields(requested.Expression) || len(requested.Options) != 0 {
		return w.end("unsupported", "only five cron fields and the separate IANA zone are supported")
	}
	expression := requested.Expression
	if pinned == nil {
		// Kubernetes v1.34.0's formatSchedule supplies .spec.timeZone using this prefix.
		expression = "TZ=" + req.Timezone + " " + expression
	}
	schedule, err := cron.ParseStandard(expression)
	if err != nil {
		return w.end("rejected", err.Error())
	}
	spec, ok := schedule.(*cron.SpecSchedule)
	if !ok {
		return fmt.Errorf("ParseStandard returned %T for a five-field schedule", schedule)
	}
	if pinned != nil {
		// Keep the parsed schedule and use the location decoded from the exact hashed bytes.
		spec.Location = pinned
	}
	if err := w.emit("accepted", nil); err != nil {
		return err
	}
	return w.enumerate(schedule, spec.Location, req.Start.UTC(), req.End.UTC(), req.MaxOccurrences)
}

func (w worker) enumerate(schedule cron.Schedule, location *time.Location, start, end time.Time, cap int) error {
	// Next starts at the following whole second. Subtract a nanosecond so a
	// whole-second occurrence exactly at start can be returned by the engine.
	previous := start.Add(-time.Nanosecond)
	for count := 0; ; count++ {
		next := schedule.Next(previous)
		switch {
		case next.IsZero():
			return w.end("search_limit", "Schedule.Next returned zero at its search horizon")
		case next.Equal(previous):
			return w.end("non_advancing", fmt.Sprintf("occurrence repeated at %s", next))
		case next.Before(previous):
			return w.end("non_monotonic", fmt.Sprintf("occurrence moved backward: %s -> %s", previous, next))
		case !next.Before(end):
			return w.end("complete", "next occurrence is at or beyond exclusive end")
		case count == cap:
			return w.end("occurrence_cap", "additional occurrence exists within window")
		}
		if err := w.emit("occurrence", map[string]any{"occurrence": evidence(next, location)}); err != nil {
			return err
		}
		previous = next
	}
}

func evidence(instant time.Time, location *time.Location) occurrence {
	local := instant.In(location)
	_, offset := local.Zone()
	layout := "2006-01-02T15:04:05.000000-07:00"
	if offset%60 != 0 {
		layout += ":00" // Historical IANA offsets can include seconds.
	}
	formatted := local.Format(layout)
	fold := repeated(local, offset)
	return occurrence{
		Local: formatted, UTC: instant.UTC().Format(utcLayout), OffsetSeconds: offset,
		Fold: fold, RoundtripLocal: formatted, RoundtripFold: fold,
	}
}

func repeated(local time.Time, offset int) int {
	start, _ := local.ZoneBounds()
	if start.IsZero() {
		return 0
	}
	_, priorOffset := start.Add(-time.Nanosecond).Zone()
	if priorOffset > offset {
		// The offset difference maps the same wall time to its possible earlier instant.
		prior := local.Add(-time.Duration(priorOffset-offset) * time.Second)
		if _, candidateOffset := prior.Zone(); candidateOffset == priorOffset {
			return 1
		}
	}
	return 0
}

func main() {
	w := worker{json.NewEncoder(os.Stdout)}
	payload, err := readBounded(os.Stdin, requestLimit)
	if err == nil {
		var req request
		if err = json.Unmarshal(payload, &req); err == nil {
			err = w.run(req)
		}
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		if w.end("engine_error", err.Error()) != nil {
			os.Exit(1)
		}
	}
}
