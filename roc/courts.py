"""Offline court registry. Listing a court is not evidence of working retrieval.

Origins and IDs come from PACER's public directory, never from a suffix wildcard.
No network request or authentication is performed by this module.
"""
from dataclasses import dataclass
from importlib.resources import files
import json
import re
from urllib.parse import urlparse

from .common import RocError, clean


@dataclass(frozen=True)
class CourtProfile:
    court_id: str
    directory_name: str
    district: str
    origin: str
    adapter: str
    heading_aliases: tuple[str, ...]
    validation_status: str
    validation_date: str = ""
    verified_case_type: str = ""
    verified_samples: int = 0
    verified_roles: tuple[str, ...] = ()
    validation_notes: tuple[str, ...] = ()
    evidence_reference: str = ""

    @property
    def sample_verified(self):
        return self.validation_status == "sample-verified"

    def matches_heading(self, heading):
        text = clean(heading).casefold()
        # Some CM/ECF reports put the state first (observed in NDCA).
        # Derive only the exact alternate order of the registered district;
        # retain the direction, state, and word boundaries in every match.
        aliases = [self.district, *self.heading_aliases]
        directional = re.fullmatch(r"(Northern|Southern|Eastern|Western|Middle|Central) District of (.+)", self.district)
        if directional:
            aliases.append(f"{directional[2]} {directional[1]} District")
        return any(re.search(r"(?<!\w)" + re.escape(clean(alias).casefold()) + r"(?!\w)", text)
                   for alias in aliases)

    def summary(self):
        return {"courtId": self.court_id, "district": self.district, "origin": self.origin,
                "adapter": self.adapter, "validationStatus": self.validation_status,
                "validationDate": self.validation_date, "verifiedCaseType": self.verified_case_type,
                "verifiedSamples": self.verified_samples, "verifiedRoles": list(self.verified_roles),
                "validationNotes": list(self.validation_notes), "evidenceReference": self.evidence_reference}


def _load_registry():
    data = json.loads(files("roc").joinpath("district_courts.json").read_text(encoding="utf-8"))
    profiles = {}
    origins = set()
    for row in data["courts"]:
        status = row["validation"]
        origin = row["origin"]
        parsed = urlparse(origin)
        if (not re.fullmatch(r"[a-z]+dc", row["courtId"]) or row["courtId"] in profiles
                or origin in origins or parsed.scheme != "https"
                or not parsed.hostname or not parsed.hostname.endswith(".uscourts.gov")
                or origin != "https://" + parsed.hostname
                or status["status"] not in ("sample-verified", "unverified")):
            raise RocError("Invalid district-court registry entry.")
        profile = CourtProfile(row["courtId"], row["directoryName"], row["district"], origin,
                               row["adapter"], tuple(row.get("headingAliases", [])), status["status"],
                               status.get("date", ""), status.get("caseType", ""), status.get("sampleCount", 0),
                               tuple(status.get("rolesExercised", [])), tuple(status.get("notes", [])),
                               status.get("evidence", ""))
        profiles[profile.court_id] = profile
        origins.add(origin)
    if len(profiles) != 94:
        raise RocError("The district-court registry must contain 94 primary court systems.")
    return data, profiles


REGISTRY_METADATA, DISTRICT_COURTS = _load_registry()
BY_HOST = {urlparse(c.origin).hostname: c for c in DISTRICT_COURTS.values()}


def court_profile(court_id):
    profile = DISTRICT_COURTS.get(str(court_id).lower())
    if profile is None:
        raise RocError(f"Court {court_id} is outside the registered primary district-court systems.")
    return profile


def profile_for_url(url):
    try:
        parsed = urlparse(url)
        profile = BY_HOST.get(parsed.hostname)
        if (parsed.scheme != "https" or not profile or parsed.username is not None
                or parsed.password is not None or parsed.port is not None):
            raise ValueError()
    except (TypeError, ValueError):
        raise RocError("Court URL is not a registered HTTPS district-court origin.") from None
    return profile


def profile_for_case(case):
    profile = court_profile(case["courtId"])
    linked = profile_for_url(case.get("pacerLink", ""))
    if linked.court_id != profile.court_id:
        raise RocError("Case court ID and court website disagree; no report submitted.")
    return profile


def require_enabled(profile, allow_unverified=False):
    if profile.adapter != "district-cmecf":
        raise RocError("Court has no implemented retrieval adapter.")
    if not profile.sample_verified and not allow_unverified:
        raise RocError(f"{profile.court_id}: registered but not live-verified; allowUnverifiedCourts must be true for a controlled test.")


def validate_policy(config):
    if type(config.get("allowUnverifiedCourts", False)) is not bool:
        raise RocError("allowUnverifiedCourts must be true or false.")


def registry_summary():
    return {"source": REGISTRY_METADATA["source"], "retrievedDate": REGISTRY_METADATA["retrievedDate"],
            "registeredDistrictCourts": len(DISTRICT_COURTS),
            "sampleVerifiedCourts": sum(c.sample_verified for c in DISTRICT_COURTS.values()),
            "courts": [c.summary() for c in DISTRICT_COURTS.values()]}
