'use strict';
/*
 * Standalone adapter for cron-parser 5.x, executed by an explicitly selected Node.js.
 *
 * It is a thin wrapper over the public API (CronExpressionParser.parse and next()). It
 * does not parse expressions, repair results, install packages, or use the network.
 * Stdout is NDJSON protocol v1, the same records as the Python worker; diagnostics go to stderr.
 *
 * The library is loaded only from <installation>/node_modules. Neither NODE_PATH, global
 * folders, nor a node_modules directory above the installation can supply it.
 */

const fs = require('fs');
const path = require('path');

// cron-parser 5.x raises this plain Error when its bounded search gives up. It is an
// unresolved search, never proof that no occurrence exists.
const LOOP_LIMIT_MESSAGE = 'Invalid expression, loop limit exceeded';
const REQUEST_LIMIT = 64 * 1024;

class Terminal extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

async function emit(kind, fields) {
  const line = JSON.stringify({ protocol_version: 1, type: kind, ...fields }) + '\n';
  if (!process.stdout.write(line)) {
    await new Promise((resolve) => process.stdout.once('drain', resolve));
  }
}

async function readRequest() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > REQUEST_LIMIT) throw new Error('request exceeds 64 KiB');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function isDirectory(target) {
  try {
    return fs.statSync(target).isDirectory();
  } catch {
    return false;
  }
}

function readPackage(directory, name) {
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
  } catch (error) {
    throw new Terminal('missing_dependency', `${name} is not installed in ${directory}: ${error.code || error.message}`);
  }
  if (manifest.name !== name || typeof manifest.version !== 'string') {
    throw new Terminal('missing_dependency', `${directory}/package.json is not ${name}`);
  }
  return manifest;
}

function inside(root, target) {
  return target === root || target.startsWith(root + path.sep);
}

// Return real paths of the selected installation, cron-parser and the Luxon it will load.
function locate(installation) {
  if (!isDirectory(installation)) {
    throw new Terminal('missing_dependency', `installation directory does not exist: ${installation}`);
  }
  const root = fs.realpathSync(installation);
  const library = path.join(root, 'node_modules', 'cron-parser');
  const manifest = readPackage(library, 'cron-parser');
  const libraryReal = fs.realpathSync(library);
  // Node resolves a dependency from the package directory upward. Only the nested and
  // installation-level folders are acceptable; the first existing one is what Node picks.
  const candidates = [path.join(libraryReal, 'node_modules', 'luxon'), path.join(root, 'node_modules', 'luxon')];
  const luxonDir = candidates.find((candidate) => isDirectory(candidate));
  if (!luxonDir) {
    throw new Terminal('missing_dependency', 'luxon is not installed in the selected installation');
  }
  const luxonReal = fs.realpathSync(luxonDir);
  const luxonManifest = readPackage(luxonDir, 'luxon');
  for (const target of [libraryReal, luxonReal]) {
    if (!inside(root, target)) {
      throw new Terminal('missing_dependency', `${target} resolves outside the selected installation`);
    }
  }
  return { root, libraryReal, luxonReal, manifest, luxonManifest };
}

function load(found) {
  const library = require(found.libraryReal);
  // Verify the module actually loaded, not just the directory that was expected.
  const loaded = Object.keys(require.cache).filter((file) => /[\\/]node_modules[\\/]luxon[\\/]/.test(file));
  if (!loaded.length || !loaded.every((file) => inside(found.luxonReal, file))) {
    throw new Terminal('missing_dependency', 'cron-parser loaded luxon from outside the selected installation');
  }
  const luxon = require(found.luxonReal);
  return { library, luxon, entry: require.resolve(found.libraryReal), luxonEntry: require.resolve(found.luxonReal) };
}

// Time zones must be IANA keys. Offset zones, "system", and "local" are never accepted.
function resolveZone(luxon, key) {
  let resolved;
  try {
    resolved = new Intl.DateTimeFormat('en-US', { timeZone: key }).resolvedOptions().timeZone;
  } catch {
    return null;
  }
  const probe = luxon.DateTime.fromMillis(0, { zone: key });
  if (!/^[A-Za-z]/.test(resolved) || !probe.isValid) return null;
  return { resolved, luxonName: probe.zoneName, luxonType: probe.zone.type };
}

function stringVersions() {
  const result = {};
  for (const [name, value] of Object.entries(process.versions)) {
    if (typeof value === 'string') result[name] = value;
  }
  return result;
}

function runtimeProvenance() {
  const versions = stringVersions();
  return {
    implementation: 'Node.js',
    version: process.versions.node,
    versions,
    platform: process.platform,
    arch: process.arch,
    executable: process.execPath,
    executable_realpath: fs.realpathSync(process.execPath),
    // Names only: values can contain private paths.
    icu_environment: ['NODE_ICU_DATA', 'ICU_TIMEZONE_FILES_DIR', 'TZ'].filter((name) => name in process.env),
  };
}

function timezoneProvenance(key, zone) {
  const versions = process.versions;
  const unavailable = ['tzdata_package_version', 'tzif_sha256'];
  const fields = { icu: 'icu_version', tz: 'tz_data_version', cldr: 'cldr_version', unicode: 'unicode_version' };
  for (const [name, field] of Object.entries(fields)) {
    if (typeof versions[name] !== 'string') unavailable.push(field);
  }
  return {
    provider: 'node-intl',
    source: 'Intl.DateTimeFormat (ICU) through Luxon',
    key,
    resolved_key: zone.resolved,
    luxon_zone_name: zone.luxonName,
    luxon_zone_type: zone.luxonType,
    icu_version: versions.icu ?? null,
    tz_data_version: versions.tz ?? null,
    cldr_version: versions.cldr ?? null,
    unicode_version: versions.unicode ?? null,
    tzdata_package_version: null,
    tzif_sha256: null,
    unavailable,
  };
}

const pad = (value, width) => String(value).padStart(width, '0');

function utcMs(year, month, day, hour, minute, second, millisecond) {
  // Date.UTC would map years 0..99 to 1900..1999.
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, millisecond);
  return date.getTime();
}

// Request timestamps are UTC with up to six decimals; keep the sub-millisecond part exact.
function parseInstant(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/.exec(text);
  if (!match) throw new Error(`invalid request timestamp: ${text}`);
  const fraction = (match[7] || '').padEnd(6, '0');
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  return {
    ms: utcMs(year, month, day, hour, minute, second, Number(fraction.slice(0, 3))),
    sub: Number(fraction.slice(3)),
  };
}

// An occurrence has whole-millisecond precision, so it equals a bound only when sub is 0.
const notBefore = (ms, bound) => ms > bound.ms || (ms === bound.ms && bound.sub === 0);
const before = (ms, bound) => ms < bound.ms || (ms === bound.ms && bound.sub > 0);

function localIso(fields, offsetSeconds) {
  const sign = offsetSeconds < 0 ? '-' : '+';
  const absolute = Math.abs(offsetSeconds);
  const seconds = absolute % 60;
  const zone =
    `${sign}${pad(Math.floor(absolute / 3600), 2)}:${pad(Math.floor((absolute % 3600) / 60), 2)}` +
    (seconds ? `:${pad(seconds, 2)}` : '');
  return (
    `${pad(fields.year, 4)}-${pad(fields.month, 2)}-${pad(fields.day, 2)}T` +
    `${pad(fields.hour, 2)}:${pad(fields.minute, 2)}:${pad(fields.second, 2)}.` +
    `${pad(fields.millisecond, 3)}000${zone}`
  );
}

function wallClock(fields) {
  return localIso(fields, 0).slice(0, 23);
}

// JavaScript dates have no fold attribute. The flag is derived from the instant and the zone
// rules only: 1 means the same wall-clock reading occurred earlier with a larger offset.
function secondOccurrence(luxon, key, ms, offset) {
  for (const hours of [1, 2, 3, 6, 12, 24, 36]) {
    const earlier = luxon.DateTime.fromMillis(ms - hours * 3600000, { zone: key }).offset;
    if (earlier <= offset) continue;
    const candidate = ms - Math.round((earlier - offset) * 60000);
    if (candidate < ms && luxon.DateTime.fromMillis(candidate, { zone: key }).offset === earlier) return 1;
  }
  return 0;
}

function evidence(luxon, key, result) {
  const ms = result.toDate().getTime();
  if (!Number.isFinite(ms)) throw new Error('engine returned an invalid Date');
  const engine = {
    year: result.getFullYear(),
    month: result.getMonth() + 1,
    day: result.getDate(),
    hour: result.getHours(),
    minute: result.getMinutes(),
    second: result.getSeconds(),
    millisecond: result.getMilliseconds(),
  };
  const engineOffset = result.getUTCOffset();
  const offsetSeconds = Math.round(engineOffset * 60);
  const wall = utcMs(engine.year, engine.month, engine.day, engine.hour, engine.minute, engine.second, engine.millisecond);
  if (wall - offsetSeconds * 1000 !== ms) {
    throw new Error(`engine wall clock ${wallClock(engine)} with offset ${engineOffset} min disagrees with instant ${ms}`);
  }
  const trip = luxon.DateTime.fromMillis(ms, { zone: key });
  const tripFields = {
    year: trip.year,
    month: trip.month,
    day: trip.day,
    hour: trip.hour,
    minute: trip.minute,
    second: trip.second,
    millisecond: trip.millisecond,
  };
  return {
    local: localIso(engine, offsetSeconds),
    utc: new Date(ms).toISOString().replace(/\.(\d{3})Z$/, '.$1000Z'),
    offset_seconds: offsetSeconds,
    fold: secondOccurrence(luxon, key, ms, engineOffset),
    roundtrip_local: localIso(tripFields, Math.round(trip.offset * 60)),
    roundtrip_fold: secondOccurrence(luxon, key, ms, trip.offset),
    nonexistent_local: wallClock(engine) !== wallClock(tripFields),
  };
}

function isParserRejection(error) {
  // cron-parser reports every invalid expression as a plain Error. Subclasses such as
  // TypeError indicate a defect and stay engine errors.
  return error instanceof Error && Object.getPrototypeOf(error) === Error.prototype;
}

async function run(request) {
  if (request.protocol_version !== 1) throw new Error('unsupported request protocol');
  const profile = request.profile;
  const key = request.timezone;
  const found = locate(profile.installation);
  let modules;
  try {
    modules = load(found);
  } catch (error) {
    if (error instanceof Terminal) throw error;
    if (error?.code === 'MODULE_NOT_FOUND') {
      throw new Terminal('missing_dependency', String(error.message).slice(0, 1000));
    }
    throw error;
  }
  const { library, luxon } = modules;
  const zone = resolveZone(luxon, key);
  if (zone === null) {
    throw new Terminal('invalid_timezone', `timezone ${JSON.stringify(key)} is not an IANA key known to this Node.js runtime`);
  }
  const version = found.manifest.version;
  await emit('profile', {
    profile: {
      ...profile,
      requested_version: profile.version,
      actual_version: version,
      interpreter: process.execPath,
      runtime: runtimeProvenance(),
      installation_realpath: found.root,
      loaded: {
        'cron-parser': { version, path: modules.entry },
        luxon: { version: found.luxonManifest.version, path: modules.luxonEntry },
      },
      timezone: timezoneProvenance(key, zone),
    },
  });
  if (profile.version !== null && profile.version !== version) {
    throw new Terminal('version_mismatch', 'requested engine version is not installed');
  }
  if (version.split('.')[0] !== '5' || typeof library.CronExpressionParser?.parse !== 'function') {
    throw new Terminal('unsupported', 'only cron-parser 5.x (CronExpressionParser.parse) is supported');
  }

  const start = parseInstant(request.start);
  const end = parseInstant(request.end);
  // Five fields have minute resolution and currentDate is exclusive for next(), so anchor at the
  // whole UTC minute before start and filter on the exact start. No epsilon is used. Windows
  // inside the first minute are still handled because the filter is exact.
  const minute = 60000;
  const anchor = start.ms - (((start.ms % minute) + minute) % minute) - minute;
  let expression;
  try {
    expression = library.CronExpressionParser.parse(profile.expression, { currentDate: new Date(anchor), tz: key });
  } catch (error) {
    if (!isParserRejection(error)) throw error;
    await emit('end', { status: 'rejected', detail: String(error.message).slice(0, 1000) });
    return;
  }
  await emit('accepted', {});
  let count = 0;
  let previous = null;
  for (;;) {
    let result;
    try {
      result = expression.next();
    } catch (error) {
      if (error?.message === LOOP_LIMIT_MESSAGE) {
        await emit('end', { status: 'search_limit', detail: error.message });
        return;
      }
      throw error;
    }
    const ms = result.toDate().getTime();
    if (!Number.isFinite(ms)) throw new Error('engine returned an invalid Date');
    if (previous !== null && ms <= previous) {
      const status = ms === previous ? 'non_advancing' : 'non_monotonic';
      await emit('end', { status, detail: `${new Date(previous).toISOString()} -> ${new Date(ms).toISOString()}` });
      return;
    }
    previous = ms;
    if (!before(ms, end)) {
      await emit('end', { status: 'complete', detail: 'next occurrence is at or beyond exclusive end' });
      return;
    }
    if (!notBefore(ms, start)) continue;
    if (count === request.max_occurrences) {
      await emit('end', { status: 'occurrence_cap', detail: 'additional occurrence exists within window' });
      return;
    }
    await emit('occurrence', { occurrence: evidence(luxon, key, result) });
    count += 1;
  }
}

async function main() {
  try {
    await run(await readRequest());
  } catch (error) {
    if (error instanceof Terminal) {
      await emit('end', { status: error.status, detail: error.message.slice(0, 1000) });
      return;
    }
    console.error(error?.stack ? error.stack : String(error));
    const name = error?.constructor ? error.constructor.name : 'Error';
    await emit('end', { status: 'engine_error', detail: `${name}: ${error?.message ?? error}`.slice(0, 1000) });
  }
}

main();
