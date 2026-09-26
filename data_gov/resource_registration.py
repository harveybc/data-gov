"""The resource registry: what the catalog knows about a resource, and WHAT IT DOES NOT.

`docs/00_CONTRATO.md` §5 is fail-closed: *sin fila de inventario no hay autorización ni accounting de ese recurso*,
and *el núcleo no adivina recursos*. A file that a study reads while the catalog knows nothing about it is an
ungoverned input, whatever the study's own document says about it. This module is where such a resource is written
down -- including the facts about it that are MISSING, because the absence is the governing fact far more often than
the presence is.

Why an absence has to be part of the registration
-------------------------------------------------
An economic-calendar resource is used to answer "what was the surprise in this release, and when did it become
public". A resource that carries a consensus but no publication instant can answer the first half and nothing of the
second; a resource that carries an observed publication instant but no consensus can answer the second half and
nothing of the first. Registering only what each one HAS makes those two look like halves of one usable dataset. They
are not, and a study that joins them discovers it in an analysis instead of being refused by the catalog. So every
registration carries a named `absences` list, and a consumer matches on the codes.

The key discipline, restated from `financial-data/_scripts/lib/point_in_time_store.py`
--------------------------------------------------------------------------------------
A cross-repository import is not available, so the rules are restated rather than shared:

* a key is a digest THIS module computes from what the row is, never a name that arrived from outside;
* `registered_at` -- our receipt clock -- is deliberately **outside** the key. Re-reading a resource that has not
  moved and registering it again is one registration seen twice: a `DUPLICATE`, not a revision. A receipt clock
  inside the key would make every sweep of the catalog manufacture a revision of everything;
* a row is never rewritten. Facts that changed are a NEW row with a higher `revision_index`; the earlier row keeps
  its bytes, its digest and its receipt clock. `write_row` refuses an attempt by name, `ROW_REWRITE_REFUSED`;
* `known_at(T)` is the catalog as it stood at T: for each resource, the last valid row registered no later than T.
  Rows registered after T are ABSENT from that answer, not merely ranked lower, because the question is what a
  decision made at T could have been refused by;
* a row is validated against the key it was found under as well as against its own digest -- a whole, self-consistent
  registration of ANOTHER resource answers the self-hash question perfectly -- and a row that fails is quarantined
  under a name carrying its own bytes rather than deleted or re-signed.

Two keys, not one. `catalog_key` says WHICH resource (its lake and its path): the catalog slot, stable across
re-acquisitions of the same file. `registration_identity` says WHAT WAS REGISTERED about it (the digest of the bytes
measured, and the digest of the declared facts). New bytes under the same path are a revision of that slot, not a new
slot, because a consumer asks the catalog about a path.

Nothing here reads the network, holds a credential, or authorizes anything. Registering a resource does not open it:
`governed_delivery.state` is a fact about the resource, and a resource with no availability contract stays closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "data_gov.resource_registration.v1"

QUARANTINE = "quarantine"

#: WHICH resource a registration is about: the catalog slot. `lake` is the data-gov `lake_id` that serves the bytes,
#: or the sentinel below when no configured lake root contains them -- which is itself a registrable absence.
CATALOG_IDENTITY = ("lake", "resource")

#: WHAT was registered about it. `registered_at` is absent on purpose: see the module docstring.
REGISTRATION_IDENTITY = ("content_sha256", "facts_sha256")

NOT_IN_ANY_LAKE_ROOT = "NOT_IN_ANY_LAKE_ROOT"

#: the facts a registration must carry. Each one is required: a registration that omits any of them is refused,
#: because an omitted field reads as "nothing to say" and the absences are exactly what must be said.
REQUIRED_FACTS = (
    "what_it_is",            # one sentence a reader can act on, in the words of the resource's own producer where it has any
    "physical_location",     # where the bytes are, and under which lake root if any
    "coverage",              # the window the rows span, in the resource's own clock, and what that clock is
    "clock",                 # declared clock, era boundaries, and whether it was measured or documented
    "publication_instant",   # observed, assumed, or absent -- never inferred from a column resembling a familiar name
    "carries",               # consensus, actuals, or only a schedule
    "absences",              # named, with the consequence of each
    "governed_delivery",     # whether data-gov can serve it today, and the one fact that would open it
    "measurement_provenance",  # the artifacts these facts were measured from, by digest
)

#: every absence a registration may declare. A code is added here before it is used, so a consumer can enumerate what
#: the catalog is able to refuse on. Free text in `why` explains an absence; it never introduces one.
ABSENCE_CODES = {
    "NO_PUBLICATION_CLOCK":
        "no column in these bytes records when a value became public; any release instant taken from this resource is "
        "assumed, never observed",
    "NO_CONSENSUS_PUBLICATION_CLOCK":
        "this resource carries a consensus and records no instant at which that consensus became public, so a surprise "
        "computed from it cannot be shown to have been pre-release information",
    "NO_CONSENSUS_AT_ALL":
        "no column carries an expectation, so no surprise can be formed from this resource alone",
    "CONSENSUS_COLUMN_PRESENT_BUT_EMPTY":
        "a consensus column exists and every row is null, which is the same absence as a missing column for any use "
        "that needs a value, and a worse one because the schema looks complete",
    "NO_REVISION_HISTORY":
        "at the finest key this resource offers every key carries one value, so no earlier version of any field "
        "survives and a point-in-time view cannot be reconstructed from these bytes",
    "REVISION_HISTORY_UNDECIDABLE":
        "values disagree at the finest key and the resource carries neither an observation clock nor a version field, "
        "so nothing in the bytes can tell a revision of one release from two releases sharing a key",
    "NO_PER_ROW_RECEIPT_CLOCK":
        "the only receipt clock is at FILE grain (the download's `acquired_at`), so it cannot order two releases "
        "inside the file and is never read as a per-release receipt",
    "NO_TIMEZONE_DECLARED_BY_THE_SOURCE":
        "the producer states no time zone for this clock column; whatever zone is used downstream is a declaration by "
        "this catalog and not a fact of the source",
    "CLOCK_MEASURED_NOT_DOCUMENTED":
        "the clock this registration declares was MEASURED from the bytes against an independent observed-instant "
        "archive; the source documents none of it, and the measurement can be re-run and can be wrong",
    "CLOCK_ERA_UNDETERMINED":
        "one or more eras of this resource's span have no determined offset; rows inside them are excluded by name "
        "rather than localized by a neighbouring era's offset",
    "NO_REFERENCE_PERIOD":
        "no column says which period a value refers to, so two series cannot be checked for comparability",
    "NO_UNIT":
        "nothing in this resource says what its numbers are measured in, so a difference across two of them is "
        "arithmetic of unknown units",
    "NO_CANCELLATION_STATE":
        "nothing distinguishes a release that did not happen from one this resource simply does not carry",
    "NO_PROVENANCE_SIDECAR":
        "no `provenance.json` accompanies these bytes: no declared source, no declared digest and no acquisition clock",
    "NOT_IN_ANY_LAKE_ROOT":
        "no configured lake root contains these bytes, so data-gov cannot serve, hash or account for a delivery of "
        "them at all; a study reads them off the filesystem outside governance",
    "NO_AVAILABILITY_CONTRACT":
        "no availability contract is installed for this resource, so a governed download is refused "
        "`resource availability contract required` (fail-closed, `docs/00_CONTRATO.md` §5)",
    "AVAILABILITY_VOCABULARY_CANNOT_EXPRESS_THIS_CLOCK":
        "the installed availability-contract vocabulary has no value for the clock evidence this resource actually "
        "has, so no honest contract can be written for it until that vocabulary is extended",
    "UNKNOWN":
        "the inventory cannot tell; `why` names what was looked for and what was found instead",
}

#: what a publication instant IS here. The words are the ones the event-study artifacts already carry
#: (`feature_eng_m5phet.events.publication_clock_block`), so a registration and a study cannot disagree in vocabulary.
PUBLICATION_KINDS = {
    "OBSERVED_ACTUAL_PUBLICATION":
        "the resource records the instant the ACTUAL was published and somebody observed it",
    "ASSUMED_SCHEDULED_PUBLICATION":
        "the resource records only a scheduled instant; treating it as the publication instant is an operator's "
        "assumption and nothing estimated under it is identified",
    "ASSUMED_SCHEDULED_PUBLICATION_LOCALIZED":
        "as above, over a scheduled wall clock localized by MEASURED per-era offsets: a correct anchor for an assumed "
        "clock is still an assumed clock",
    "NO_PUBLICATION_INSTANT_OF_ANY_KIND":
        "no column of this resource is an instant at which anything was published, scheduled or otherwise",
}

#: the states a resource can be in with respect to a governed delivery. None of them is granted by registering it.
DELIVERY_STATES = {
    "CLOSED_NO_AVAILABILITY_CONTRACT":
        "inventoried by a lake and refused on download until a contract derived from producer evidence is installed",
    "CLOSED_NOT_IN_ANY_LAKE_ROOT":
        "outside every configured lake root: data-gov cannot serve it at all",
    "OPEN_AVAILABILITY_CONTRACT_INSTALLED":
        "a contract is installed and the lake executes it",
}


class RegistrationRefusal(ValueError):
    """A registration this module will not make, carrying the reason. No fact is invented to avoid one."""


# --- primitives -----------------------------------------------------------------------------------------------------

def canonical(value):
    """Strict canonical JSON. `default=str` is deliberately NOT used: a value this cannot serialize is a field whose
    identity nobody defined, and stringifying it would give two different registrations one digest."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value, field):
    """An unambiguous instant, as UTC. A naive timestamp is refused, never localized for you: during a daylight-saving
    fold one wall clock names two instants, and choosing one is a guess about when something was knowable."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise RegistrationRefusal(f"NAIVE_TIMESTAMP: {field} carries no offset, so it names no instant")
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise RegistrationRefusal(f"TIMESTAMP_REQUIRED: {field} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistrationRefusal(f"UNREADABLE_TIMESTAMP: {field}={value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegistrationRefusal(f"AMBIGUOUS_LOCAL_TIME: {field}={value!r} carries no offset")
    return parsed.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc)


def _hex64(value, field):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RegistrationRefusal(f"NOT_A_SHA256: {field}={value!r}")
    return value


def catalog_identity(row):
    return {field: row.get(field) for field in CATALOG_IDENTITY}


def catalog_key(row):
    """The opaque key of a catalog slot, computed here from the slot's own fields. Never a provider's identifier,
    which means nothing outside the provider that issued it and is not a safe path component."""
    return digest({"schema": SCHEMA, "catalog": catalog_identity(row)})


def registration_identity(row):
    return {field: row.get(field) for field in REGISTRATION_IDENTITY}


def row_identity(row):
    """The store key: this resource, registered to be this. Two registrations of the same facts about the same bytes
    collide on it by construction, which is exactly why re-reading an unchanged resource manufactures no revision."""
    return digest({"schema": SCHEMA, "catalog_key": catalog_key(row),
                   "registration": registration_identity(row)})


# --- validation -----------------------------------------------------------------------------------------------------

def validate_absences(absences):
    """Every absence, checked against the declared vocabulary. An unknown code is a refusal, not a warning: a
    consumer that matches on codes cannot refuse on one it has never been told about."""
    if not isinstance(absences, list):
        raise RegistrationRefusal("ABSENCES_MUST_BE_A_LIST")
    out, seen = [], set()
    for item in absences:
        if not isinstance(item, dict):
            raise RegistrationRefusal("ABSENCE_MUST_BE_A_MAPPING")
        code = item.get("code")
        if code not in ABSENCE_CODES:
            raise RegistrationRefusal(
                f"UNKNOWN_ABSENCE_CODE: {code!r}; add it to ABSENCE_CODES with its meaning before declaring it")
        if not item.get("why"):
            raise RegistrationRefusal(f"ABSENCE_WITHOUT_A_REASON: {code} must say what was looked for")
        marker = (code, item.get("field"))
        if marker in seen:
            raise RegistrationRefusal(f"DUPLICATE_ABSENCE: {code} declared twice for the same field")
        seen.add(marker)
        out.append({"code": code, "field": item.get("field"), "why": item["why"],
                    "meaning": ABSENCE_CODES[code],
                    "blocks": sorted(item.get("blocks") or [])})
    return sorted(out, key=lambda a: (a["code"], a["field"] or ""))


def validate_registration(registration):
    """One registration, checked before it can enter the registry. Returns a normalized copy; refuses rather than
    repairs. `facts_sha256` is computed here from the facts themselves, so a caller cannot declare a digest of
    something other than what it registered."""
    if not isinstance(registration, dict):
        raise RegistrationRefusal("REGISTRATION_MUST_BE_A_MAPPING")
    lake = registration.get("lake")
    resource = registration.get("resource")
    if not isinstance(lake, str) or not lake:
        raise RegistrationRefusal("LAKE_REQUIRED: name the lake_id, or the sentinel NOT_IN_ANY_LAKE_ROOT")
    if not isinstance(resource, str) or not resource or resource.startswith("/") or ".." in resource.split("/"):
        raise RegistrationRefusal(f"INVALID_RESOURCE_PATH: {resource!r} must be a relative path inside its root")
    content = _hex64(registration.get("content_sha256"), "content_sha256")
    facts = registration.get("facts")
    if not isinstance(facts, dict):
        raise RegistrationRefusal("FACTS_MUST_BE_A_MAPPING")
    missing = [field for field in REQUIRED_FACTS if field not in facts]
    if missing:
        raise RegistrationRefusal(f"FACTS_MISSING: {', '.join(missing)}")

    publication = facts.get("publication_instant") or {}
    kind = publication.get("kind")
    if kind not in PUBLICATION_KINDS:
        raise RegistrationRefusal(
            f"UNKNOWN_PUBLICATION_KIND: {kind!r} is not one of {sorted(PUBLICATION_KINDS)}")
    if not isinstance(publication.get("observed"), bool):
        raise RegistrationRefusal("PUBLICATION_OBSERVED_MUST_BE_A_BOOLEAN: assumed and observed never share a word")
    if publication["observed"] != (kind == "OBSERVED_ACTUAL_PUBLICATION"):
        raise RegistrationRefusal(
            f"PUBLICATION_OBSERVED_CONTRADICTS_KIND: {kind} with observed={publication['observed']}")

    delivery = facts.get("governed_delivery") or {}
    if delivery.get("state") not in DELIVERY_STATES:
        raise RegistrationRefusal(
            f"UNKNOWN_DELIVERY_STATE: {delivery.get('state')!r} is not one of {sorted(DELIVERY_STATES)}")

    clean_facts = dict(facts)
    clean_facts["absences"] = validate_absences(facts.get("absences"))
    if not clean_facts["absences"]:
        raise RegistrationRefusal(
            "NO_ABSENCES_DECLARED: a resource with nothing missing must say so with an explicit empty-absence "
            "statement, which this vocabulary does not yet have; a silently empty list is how an absence is dropped")
    clean_facts["publication_instant"] = dict(publication,
                                              meaning=PUBLICATION_KINDS[kind])
    clean_facts["governed_delivery"] = dict(delivery, meaning=DELIVERY_STATES[delivery["state"]])

    out = {"schema": SCHEMA, "lake": lake, "resource": resource, "content_sha256": content,
           "facts": clean_facts,
           "facts_sha256": digest({"schema": SCHEMA, "facts": clean_facts}),
           "registered_at": instant(registration.get("registered_at") or now(), "registered_at").isoformat(),
           "registered_by": registration.get("registered_by") or None}
    if not out["registered_by"]:
        raise RegistrationRefusal("REGISTERED_BY_REQUIRED: a registration says who made it")
    return out


# --- the registry ---------------------------------------------------------------------------------------------------

class ResourceRegistry:
    """A directory of registration rows. Append-only by construction: the only write is an exclusive create."""

    def __init__(self, directory):
        self.root = Path(directory)
        self._index = None

    def refresh(self):
        """Drop the in-process index of what is on disk. The index only ever serves the WRITE path (it is how a new
        registration learns which registrations of its slot preceded it); every reader re-reads and re-validates, so a
        stale index cannot become a stale answer. Correctness under a second writer does not depend on it either: the
        write is an exclusive create, so a key another process already owns is read back, never overwritten."""
        self._index = None
        return self

    def _slot_index(self):
        if self._index is None:
            index = {}
            for row in self.rows():
                index.setdefault(row.get("catalog_key"), []).append(row)
            self._index = index
        return self._index

    # --- containment ------------------------------------------------------------------------------------------------
    def _resolved_root(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root.resolve(strict=True)

    def _path(self, row_id):
        if not isinstance(row_id, str) or len(row_id) != 64 or any(c not in "0123456789abcdef" for c in row_id):
            raise RegistrationRefusal(
                "INVALID_ROW_KEY: a registry key is a digest this registry computed, never a supplied name")
        root = self._resolved_root()
        path = root / row_id[:2] / f"{row_id}.json"
        self._contained(root, path)
        return path

    @staticmethod
    def _contained(root, path):
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        resolved = probe.resolve()
        if resolved != root and root not in resolved.parents:
            raise RegistrationRefusal(f"REGISTRY_ESCAPE_REFUSED: {path.name} resolves outside the registry root")

    # --- reading ----------------------------------------------------------------------------------------------------
    def _read(self, path, *, expected_key=None):
        """A torn row is reported as a failure of THAT key. It must not make the registry unreadable, and it must
        never be mistaken for an unregistered resource."""
        if expected_key is None and path.parent.name != QUARANTINE:
            expected_key = path.stem
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"row_sha256": None, "row_id": expected_key, "integrity": "FAILED", "unreadable": True,
                    "integrity_problems": [f"UNREADABLE_ROW: {path.name}: {exc.__class__.__name__}"],
                    "catalog_key": None, "registered_at": None}
        if not isinstance(row, dict):
            return {"row_sha256": None, "row_id": expected_key, "integrity": "FAILED", "unreadable": True,
                    "integrity_problems": ["ROW_NOT_A_MAPPING"], "catalog_key": None, "registered_at": None}
        return self._checked(row, expected_key=expected_key)

    @staticmethod
    def _checked(row, expected_key=None):
        """Four questions, not one. Does the row hash to its own digest? Do the facts hash to the `facts_sha256` it
        publishes? Do its own fields derive the key it carries? And is that the key it was found under -- the question
        a copied file answers wrongly and silently."""
        row = dict(row)
        recomputed = digest({k: v for k, v in row.items() if k != "row_sha256"})
        problems = []
        if row.get("row_sha256") != recomputed:
            problems.append("CONTENT_DIGEST_MISMATCH: the row does not hash to its own recorded digest")
        if isinstance(row.get("facts"), dict):
            refacts = digest({"schema": SCHEMA, "facts": row["facts"]})
            if row.get("facts_sha256") != refacts:
                problems.append("FACTS_DIGEST_MISMATCH: the registered facts do not hash to the published digest, so "
                                "the digest names something other than what is in the row")
        else:
            problems.append("FACTS_ABSENT: a registration with no facts registers nothing")
        try:
            derived = row_identity(row)
        except (TypeError, ValueError):
            derived = None
            problems.append("IDENTITY_NOT_DERIVABLE: this row's fields do not form an identity")
        if derived is not None and row.get("row_id") != derived:
            problems.append(f"IDENTITY_MISMATCH: the row's own fields derive key {derived[:12]} and it carries "
                            f"{str(row.get('row_id'))[:12]}")
        if expected_key is not None and derived is not None and derived != expected_key:
            problems.append(f"MISPLACED_ROW: this row belongs under key {derived[:12]} and was found under "
                            f"{expected_key[:12]}; a true registration of another resource is not this one's record")
        row["integrity"] = "OK" if not problems else "FAILED"
        row["integrity_problems"] = problems
        row["recomputed_sha256"] = recomputed
        row["derived_row_id"] = derived
        return row

    def rows(self, *, include_quarantine=False):
        """Every live row, oldest receipt first. Quarantined rows stay out of every count unless asked for."""
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.rglob("*.json")):
            if path.is_symlink():
                continue
            quarantined = path.parent.name == QUARANTINE
            if quarantined and not include_quarantine:
                continue
            row = self._read(path)
            row["quarantined"] = quarantined
            out.append(row)
        return sorted(out, key=lambda r: (r.get("registered_at") or "", r.get("row_sha256") or ""))

    def rows_for(self, key):
        """Every retained registration of one catalog slot, whatever it said."""
        return [r for r in self.rows() if r.get("catalog_key") == key]

    def known_at(self, as_of, *, slot=None):
        """The catalog as it stood at T: for each slot, the last VALID row registered no later than T.

        A row registered after T is not merely ranked lower here -- it is absent, because the question is what a study
        run at T could have been refused by, and a refusal cannot rest on a registration that had not been made.
        """
        cutoff = instant(as_of, "as_of").isoformat()
        latest = {}
        for row in self.rows():
            if row.get("integrity") != "OK":
                continue
            registered = row.get("registered_at")
            if registered is None or registered > cutoff:
                continue
            if slot is not None and row.get("catalog_key") != slot:
                continue
            key = row.get("catalog_key")
            current = latest.get(key)
            if current is None or (registered, row.get("row_sha256") or "") > (
                    current["registered_at"], current.get("row_sha256") or ""):
                latest[key] = row
        return [latest[k] for k in sorted(latest)]

    def find_row(self, reference):
        path = self._path(reference) if isinstance(reference, str) and len(reference) == 64 else None
        if path is not None and path.is_file():
            return self._read(path)
        for row in self.rows(include_quarantine=True):
            if row.get("row_sha256") == reference or row.get("row_id") == reference:
                return row
        return None

    # --- writing ----------------------------------------------------------------------------------------------------
    def append(self, registration):
        """Register one resource. Returns (row, disposition): STORED, REVISION, DUPLICATE or REPLACED_INVALID.

        DUPLICATE is returned only when this exact registration is already on disk AND that stored row still
        validates. Its bytes are returned unchanged -- re-reading a resource that has not moved adds nothing to the
        catalog and rewrites nothing in it.
        """
        registration = validate_registration(registration)
        key = catalog_key(registration)
        row_id = row_identity(registration)
        path = self._path(row_id)
        prior = list(self._slot_index().get(key, []))
        existing = self._read(path, expected_key=row_id) if path.is_file() else None
        if existing is not None and existing.get("integrity") == "OK":
            return existing, "DUPLICATE"
        invalid_existing = existing is not None

        row = dict(registration)
        row["row_id"] = row_id
        row["catalog_key"] = key
        row["catalog_identity"] = catalog_identity(registration)
        row["registration_identity"] = registration_identity(registration)
        row["execution_authorized"] = False
        row["registration_grants"] = (
            "NOTHING: registering a resource records what is known and missing about it. It does not authorize a "
            "delivery, does not install an availability contract and does not make an absent fact present")
        row["receipt_clock_reading"] = (
            "`registered_at` is OUTSIDE the key: it dates this reading of the resource, not the resource. Reading the "
            "same bytes and declaring the same facts again collides on the key and is a DUPLICATE, so a catalog sweep "
            "never manufactures revisions")
        # the revision index counts the DISTINCT registrations of this slot that preceded this one. A row that only
        # repeats what the catalog already says never reaches this line, so the index moves when the facts move and
        # not when the registration tool runs.
        row["registration_sha256"] = digest({"schema": SCHEMA,
                                             "registration": registration_identity(registration)})
        seen = {p.get("registration_sha256") for p in prior}
        row["revision_index"] = len(seen - {row["registration_sha256"]})
        row["supersedes"] = sorted(p["row_sha256"] for p in prior
                                   if p.get("registration_sha256") != row["registration_sha256"]
                                   and p.get("row_sha256"))
        row["supersedes_reading"] = (
            "later knowledge about the same resource. The superseded rows are retained and stay true of the moment "
            "they were registered, which is what `known_at` reads them back for")
        if invalid_existing:
            row["replaces_invalid_row"] = row_id
            row["quarantined_predecessor"] = self._quarantine(path, row_id)
        row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})

        created = self._write(path, row)
        if not created:
            # another writer won the race for this key. Their row is the row: two callers must not walk away holding
            # different digests for one registration.
            raced = self._read(path, expected_key=row_id)
            if raced.get("integrity") == "OK":
                return raced, "DUPLICATE"
            self._quarantine(path, row_id)
            self._write(path, row)
        self._slot_index().setdefault(key, []).append(self._checked(row, expected_key=row_id))
        if invalid_existing:
            return row, "REPLACED_INVALID"
        return row, ("STORED" if not prior else "REVISION")

    def write_row(self, row):
        """The low-level write, exposed so that an attempt to change a past registration is REFUSED BY NAME rather
        than merely failing to happen. A caller that has forged a key and wants to put different facts under it is the
        case this registry exists to make impossible."""
        row_id = row.get("row_id")
        path = self._path(row_id)
        if path.is_file():
            stored = self._read(path, expected_key=row_id)
            if stored.get("registration_sha256") != row.get("registration_sha256"):
                raise RegistrationRefusal(
                    f"ROW_REWRITE_REFUSED: {row_id[:12]} already registers "
                    f"{str(stored.get('registration_sha256'))[:12]}. Facts that changed are a NEW row with a higher "
                    f"revision; rewriting this one would delete what the catalog said at "
                    f"{stored.get('registered_at')} and no reader could ever tell")
            return stored, "DUPLICATE"
        return (row, "STORED") if self._write(path, row) else (self._read(path, expected_key=row_id), "DUPLICATE")

    def _quarantine(self, path, row_id):
        """A row that failed a check is moved aside, not deleted and never re-signed: deleting it erases what went
        wrong, and re-signing it would recompute the digest over corrupted content and make the corruption credible.
        The quarantine name carries the quarantined BYTES, so a second bad version cannot overwrite the first."""
        root = self._resolved_root()
        try:
            content = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:
            content = "unreadable"
        target = root / QUARANTINE / f"{row_id}.{content}.json"
        self._contained(root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target.relative_to(root).as_posix()          # these exact bytes are already preserved
        os.replace(path, target)
        return target.relative_to(root).as_posix()

    def _write(self, path, row) -> bool:
        """Exclusive create, never a replace. `os.link` fails if the key exists, so the first writer owns it and every
        later one reads it back. There is no code path in this module that replaces a live row."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".writing-", suffix=".json.tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, indent=1, sort_keys=True))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
                return True
            except FileExistsError:
                return False
        finally:
            Path(temporary).unlink(missing_ok=True)

    # --- replay -----------------------------------------------------------------------------------------------------
    def replay(self):
        """Read every row back and re-derive its identity. A row that fails is REPORTED, never repaired."""
        rows = self.rows(include_quarantine=True)
        failed = [r for r in rows if r.get("integrity") != "OK" and not r.get("quarantined")]
        return {"schema": SCHEMA, "rows": len(rows),
                "live": len([r for r in rows if not r.get("quarantined")]),
                "quarantined": len([r for r in rows if r.get("quarantined")]),
                "slots": len({r.get("catalog_key") for r in rows if not r.get("quarantined")}),
                "failed": [{"row_id": r.get("row_id"), "problems": r.get("integrity_problems")} for r in failed],
                "verdict": "ALL_ROWS_RE_DERIVED" if not failed else "ROWS_FAILED_RE_DERIVATION",
                "reading": ("every live row was read back, re-hashed and re-keyed from its own fields; a failure is "
                            "reported here and the row is left exactly as it is on disk")}


# --- a CLI to read the catalog back ---------------------------------------------------------------------------------

def _summary(row):
    facts = row.get("facts") or {}
    publication = facts.get("publication_instant") or {}
    carries = facts.get("carries") or {}
    return {"lake": row.get("lake"), "resource": row.get("resource"),
            "content_sha256": row.get("content_sha256"), "facts_sha256": row.get("facts_sha256"),
            "row_sha256": row.get("row_sha256"), "registered_at": row.get("registered_at"),
            "revision_index": row.get("revision_index"),
            "publication_instant": publication.get("kind"),
            "publication_observed": publication.get("observed"),
            "carries_consensus": carries.get("consensus"),
            "governed_delivery": (facts.get("governed_delivery") or {}).get("state"),
            "absences": [a.get("code") for a in facts.get("absences") or []]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read the local data-gov resource registry back.")
    parser.add_argument("--registry", required=True, help="the registry root directory")
    parser.add_argument("--known-at", default=None,
                        help="an instant with an offset; print the catalog as it stood then (default: every live row)")
    parser.add_argument("--replay", action="store_true", help="re-derive every row's identity and report failures")
    parser.add_argument("--json", action="store_true", help="print the full rows instead of a summary")
    args = parser.parse_args(argv)
    registry = ResourceRegistry(args.registry)
    if args.replay:
        print(json.dumps(registry.replay(), indent=2, sort_keys=True))
        return 0
    rows = registry.known_at(args.known_at) if args.known_at else registry.rows()
    payload = rows if args.json else [_summary(r) for r in rows]
    print(json.dumps({"schema": SCHEMA, "registry": str(args.registry),
                      "known_at": args.known_at, "rows": len(payload), "registrations": payload},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":                                      # pragma: no cover - a CLI
    sys.exit(main())
