"""
SBOM processing layer.

This module only works with plain Python data: no database, no web framework.
That keeps the core rules easy to unit-test on their own (see tests/test_core.py).

What lives here:
    detect_format()      - is this file CycloneDX JSON or SPDX JSON?
    parse_sbom()         - read the components out of an SBOM file
    require_purls()      - uploads must declare a PURL for every component
    normalise_purl()     - the canonical package URL we use to spot duplicates
    build_cyclonedx()    - write a consolidated SBOM as CycloneDX 1.5
    build_spdx()         - write a consolidated SBOM as SPDX 2.3
    compute_retention()  - work out how long an SBOM has to be kept
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

# packageurl-python gives us a proper PURL parser. If it isn't installed we fall
# back to a small hand-written normaliser further down, so the app still works.
try:
    from packageurl import PackageURL
except ImportError:  # pragma: no cover
    PackageURL = None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    """Fingerprint of a file. Stored with every original so tampering can be detected later."""
    return hashlib.sha256(data).hexdigest()


def normalise_purl(purl: str | None, name: str, version: str | None) -> tuple[str, bool]:
    """Return (canonical_key, sbom_had_a_real_purl).

    Two SBOMs often describe the same library slightly differently
    (upper/lower case type, qualifiers in a different order...). We turn every
    PURL into one standard spelling so the same library is stored only once.

    Uploaded SBOMs must declare a PURL for every component (see require_purls),
    so the fallback below is only reached when reading the app's own exports,
    whose top-level product entry has no PURL.
    """
    if not purl:
        safe_name = name.strip().replace(" ", "-").lower() or "unknown"
        if version:
            return f"pkg:generic/{safe_name}@{version}", False
        return f"pkg:generic/{safe_name}", False

    purl = purl.strip()

    # Preferred route: let the packageurl library parse and re-print it.
    if PackageURL is not None:
        try:
            return PackageURL.from_string(purl).to_string(), True
        except ValueError:
            pass  # not a valid PURL according to the library; try our own rules

    # Fallback: lower-case the package type and sort the qualifiers.
    if purl.startswith("pkg:") and "/" in purl:
        without_prefix = purl[len("pkg:"):]
        main_part, _, subpath = without_prefix.partition("#")
        main_part, _, qualifiers = main_part.partition("?")
        package_type, rest = main_part.split("/", 1)

        canonical = f"pkg:{package_type.lower()}/{rest}"
        if qualifiers:
            sorted_pairs = sorted(pair for pair in qualifiers.split("&") if pair)
            canonical += "?" + "&".join(sorted_pairs)
        if subpath:
            canonical += "#" + subpath
        return canonical, True

    # Something PURL-like that we can't improve on: keep it as it is.
    return purl, True


# --------------------------------------------------------------------------
# What a parsed SBOM looks like
# --------------------------------------------------------------------------

@dataclass
class ParsedComponent:
    name: str
    version: str | None
    purl: str | None            # exactly as written in the SBOM (may be missing)
    canonical_purl: str         # our standard spelling, used to remove duplicates
    has_purl: bool              # False only for the app's own export entries (uploads always have one)
    licenses: list[str] = field(default_factory=list)
    supplier: str | None = None
    component_type: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedSbom:
    format: str                 # "CycloneDX" or "SPDX"
    spec_version: str
    serial: str | None          # CycloneDX serialNumber or SPDX documentNamespace
    name: str | None
    created: str | None
    tool: str | None            # the tool that generated the SBOM, e.g. "syft"
    components: list[ParsedComponent]


# --------------------------------------------------------------------------
# Detecting the format
# --------------------------------------------------------------------------

def detect_format(doc: dict[str, Any]) -> tuple[str, str]:
    """Return (format, spec_version), or raise ValueError for anything we don't support."""
    if doc.get("bomFormat") == "CycloneDX":
        return "CycloneDX", str(doc.get("specVersion", "unknown"))

    if "spdxVersion" in doc:
        return "SPDX", str(doc["spdxVersion"]).replace("SPDX-", "")

    raise ValueError("Unsupported SBOM: expected CycloneDX JSON or SPDX JSON")


# --------------------------------------------------------------------------
# Reading CycloneDX
# --------------------------------------------------------------------------

def _cyclonedx_licenses(component: dict) -> list[str]:
    """CycloneDX allows either a licence expression or a licence id/name per entry."""
    found = []
    for entry in component.get("licenses") or []:
        if "expression" in entry:
            found.append(entry["expression"])

        licence = entry.get("license") or {}
        licence_text = licence.get("id") or licence.get("name")
        if licence_text:
            found.append(licence_text)
    return found


def _all_cyclonedx_components(components: list[dict]):
    """CycloneDX components can contain sub-components; walk the whole tree."""
    for component in components or []:
        yield component
        yield from _all_cyclonedx_components(component.get("components", []))


def _cyclonedx_tool_name(metadata: dict) -> str | None:
    """The generator tool is listed differently in CycloneDX 1.4 (a list) and 1.5 (an object)."""
    tools = metadata.get("tools")
    if isinstance(tools, list) and tools:
        return tools[0].get("name")
    if isinstance(tools, dict) and tools.get("components"):
        return tools["components"][0].get("name")
    return None


def _parse_cyclonedx(doc: dict, spec_version: str) -> ParsedSbom:
    metadata = doc.get("metadata") or {}

    components = []
    for item in _all_cyclonedx_components(doc.get("components", [])):
        name = item.get("name") or "unknown"
        version = item.get("version")
        canonical, has_purl = normalise_purl(item.get("purl"), name, version)
        supplier = (item.get("supplier") or {}).get("name") or item.get("publisher")
        hashes = {h.get("alg"): h.get("content") for h in item.get("hashes") or []}

        components.append(ParsedComponent(
            name=name,
            version=version,
            purl=item.get("purl"),
            canonical_purl=canonical,
            has_purl=has_purl,
            licenses=_cyclonedx_licenses(item),
            supplier=supplier,
            component_type=item.get("type"),
            hashes=hashes,
        ))

    return ParsedSbom(
        format="CycloneDX",
        spec_version=spec_version,
        serial=doc.get("serialNumber"),
        name=(metadata.get("component") or {}).get("name"),
        created=metadata.get("timestamp"),
        tool=_cyclonedx_tool_name(metadata),
        components=components,
    )


# --------------------------------------------------------------------------
# Reading SPDX
# --------------------------------------------------------------------------

def _spdx_tool_name(creation_info: dict) -> str | None:
    """SPDX lists creators as strings like 'Tool: syft-1.0'."""
    for creator in creation_info.get("creators", []):
        if creator.startswith("Tool:"):
            return creator.split(":", 1)[1].strip()
    return None


def _spdx_purl(package: dict) -> str | None:
    """In SPDX the PURL is one of the package's external references."""
    for ref in package.get("externalRefs") or []:
        if ref.get("referenceType") == "purl":
            return ref.get("referenceLocator")
    return None


def _spdx_supplier(package: dict) -> str | None:
    """SPDX writes 'Organization: Acme' - keep just 'Acme'."""
    supplier = package.get("supplier")
    if supplier and supplier.startswith(("Organization:", "Person:")):
        return supplier.split(":", 1)[1].strip()
    return supplier


def _parse_spdx(doc: dict, spec_version: str) -> ParsedSbom:
    creation_info = doc.get("creationInfo") or {}

    components = []
    for package in doc.get("packages") or []:
        name = package.get("name") or "unknown"
        version = package.get("versionInfo")
        purl = _spdx_purl(package)
        canonical, has_purl = normalise_purl(purl, name, version)

        # Keep concluded and declared licences, skipping SPDX's "don't know" markers
        # and listing each licence only once.
        licences = []
        for licence in (package.get("licenseConcluded"), package.get("licenseDeclared")):
            if licence and licence not in ("NOASSERTION", "NONE") and licence not in licences:
                licences.append(licence)

        hashes = {c.get("algorithm"): c.get("checksumValue") for c in package.get("checksums") or []}

        components.append(ParsedComponent(
            name=name,
            version=version,
            purl=purl,
            canonical_purl=canonical,
            has_purl=has_purl,
            licenses=licences,
            supplier=_spdx_supplier(package),
            component_type=package.get("primaryPackagePurpose"),
            hashes=hashes,
        ))

    return ParsedSbom(
        format="SPDX",
        spec_version=spec_version,
        serial=doc.get("documentNamespace"),
        name=doc.get("name"),
        created=creation_info.get("created"),
        tool=_spdx_tool_name(creation_info),
        components=components,
    )


def require_purls(parsed: ParsedSbom) -> None:
    """The PURL is a component's identity, and the app never invents one.

    An upload is rejected if any component has no PURL in the file, so that
    every identity in the database comes from the SBOM itself.
    """
    missing = [c for c in parsed.components if not c.purl]
    if not missing:
        return

    examples = ", ".join(f"{c.name}@{c.version}" if c.version else c.name for c in missing[:5])
    if len(missing) > 5:
        examples += f" and {len(missing) - 5} more"
    raise ValueError(
        f"SBOM rejected: {len(missing)} of {len(parsed.components)} components have no PURL "
        f"({examples}). Every component must declare a PURL."
    )


def parse_sbom(raw: bytes) -> ParsedSbom:
    """Read an uploaded file. Raises ValueError if it isn't a supported SBOM."""
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"File is not valid JSON: {error}") from error

    sbom_format, spec_version = detect_format(doc)
    if sbom_format == "CycloneDX":
        return _parse_cyclonedx(doc, spec_version)
    return _parse_spdx(doc, spec_version)


# --------------------------------------------------------------------------
# Writing the consolidated SBOM
# --------------------------------------------------------------------------

def _now_iso() -> str:
    """Current UTC time as 2026-11-09T10:42:00Z."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def build_cyclonedx(subject_name: str, subject_version: str, components: list[dict],
                    source_docs: list[dict]) -> dict:
    """Consolidated CycloneDX 1.5 document.

    `components` are the merged, de-duplicated components (dicts with name,
    version, purl, licenses, supplier, type). `source_docs` are the original
    SBOMs they came from; each one is recorded with its SHA-256 so the export
    can always be traced back to the evidence.
    """
    subject_ref = f"subject:{subject_name}@{subject_version}"

    exported = []
    for comp in components:
        entry = {
            "type": comp.get("type") or "library",
            "bom-ref": comp["purl"],
            "name": comp["name"],
        }
        if comp.get("version"):
            entry["version"] = comp["version"]
        entry["purl"] = comp["purl"]
        if comp.get("supplier"):
            entry["supplier"] = {"name": comp["supplier"]}
        if comp.get("licenses"):
            entry["licenses"] = [{"expression": licence} for licence in comp["licenses"]]
        exported.append(entry)

    source_list = [
        {"name": "usia:source-sbom", "value": f"{doc['file_name']} sha256:{doc['sha256']}"}
        for doc in source_docs
    ]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": _now_iso(),
            "tools": {"components": [{"type": "application", "name": "USIA: SBOM Vault", "version": "1.0"}]},
            "component": {
                "type": "application",
                "name": subject_name,
                "version": subject_version,
                "bom-ref": subject_ref,
            },
            "properties": source_list,
        },
        "components": exported,
        "dependencies": [{"ref": subject_ref, "dependsOn": [c["purl"] for c in components]}],
    }


def build_spdx(subject_name: str, subject_version: str, components: list[dict]) -> dict:
    """Consolidated SPDX 2.3 document: one package for the product, one per component."""
    packages = [{
        "SPDXID": "SPDXRef-Subject",
        "name": subject_name,
        "versionInfo": subject_version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
    }]
    relationships = [{
        "spdxElementId": "SPDXRef-DOCUMENT",
        "relationshipType": "DESCRIBES",
        "relatedSpdxElement": "SPDXRef-Subject",
    }]

    for number, comp in enumerate(components, start=1):
        package_id = f"SPDXRef-Package-{number}"
        packages.append({
            "SPDXID": package_id,
            "name": comp["name"],
            "versionInfo": comp.get("version") or "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "supplier": f"Organization: {comp['supplier']}" if comp.get("supplier") else "NOASSERTION",
            "licenseConcluded": " AND ".join(comp["licenses"]) if comp.get("licenses") else "NOASSERTION",
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": comp["purl"],
            }],
        })
        relationships.append({
            "spdxElementId": "SPDXRef-Subject",
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": package_id,
        })

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{subject_name}-{subject_version}-consolidated",
        "documentNamespace": f"https://usia.local/spdx/{uuid.uuid4()}",
        "creationInfo": {"created": _now_iso(), "creators": ["Tool: USIA-SBOM-Vault-1.0"]},
        "packages": packages,
        "relationships": relationships,
    }


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------

def add_years(start: date, years: int) -> date:
    """Same day N years later. 29 February becomes 28 February in a non-leap year."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def compute_retention(placed_on_market: date | None, support_period_end: date | None,
                      min_years: int = 10, today: date | None = None,
                      warn_days: int = 365, clock_start: str = "placed_on_market",
                      use_support_period_if_longer: bool = True,
                      delivered_on: date | None = None,
                      customer_support_end: date | None = None) -> dict:
    """Work out how long an SBOM must be kept under one regulation.

    The rule is the same for every regulation, only the numbers change:

        keep until = clock start + min_years
        ...or the end of support, if that is later and the policy says so.

    The clock can start when the product is placed on the market, when it is
    delivered to the customer, or when support ends. With the defaults this is
    exactly EU CRA Article 13(13): 10 years after placing on the market, or the
    support period if that is longer.

    customer_support_end is the support end agreed with the customer for the
    solution version the product was delivered in. The SBOM is kept until that
    date too, if it is later than the regulation's date.

    Returns retain_until, a status (PENDING / ACTIVE / EXPIRING / EXPIRED),
    a one-line explanation (basis) and days_left.
    """
    today = today or date.today()

    regulation = _regulation_date(placed_on_market, support_period_end, min_years, clock_start,
                                  use_support_period_if_longer, delivered_on)

    # The customer's support end extends the period when it is later (or when
    # the regulation's clock has not started yet).
    if customer_support_end and (regulation["retain_until"] is None
                                 or customer_support_end > regulation["retain_until"]):
        regulation = {"retain_until": customer_support_end, "basis": "Customer support end"}

    if regulation["retain_until"] is None:
        return {"retain_until": None, "status": "PENDING", "basis": regulation["basis"]}

    return _with_status(regulation["retain_until"], regulation["basis"], today, warn_days)


def support_end_only(customer_support_end: date | None, today: date | None = None,
                     warn_days: int = 365) -> dict:
    """Retention when a customer has a support end date but no regulation policy."""
    if customer_support_end is None:
        return {"retain_until": None, "status": "PENDING", "basis": "No support end date recorded"}
    return _with_status(customer_support_end, "Customer support end", today or date.today(), warn_days)


def _with_status(retain_until: date, basis: str, today: date, warn_days: int) -> dict:
    """Status, so the UI can warn before a period runs out."""
    days_left = (retain_until - today).days
    if days_left < 0:
        status = "EXPIRED"
    elif days_left <= warn_days:
        status = "EXPIRING"
    else:
        status = "ACTIVE"
    return {"retain_until": retain_until, "status": status, "basis": basis, "days_left": days_left}


def _regulation_date(placed_on_market, support_period_end, min_years, clock_start,
                     use_support_period_if_longer, delivered_on) -> dict:
    """The date one regulation's rule gives, or retain_until None if its clock has not started."""
    # 1. Find the date the clock starts from. If it isn't known yet, the clock
    #    hasn't started.
    if clock_start == "delivered_to_customer":
        if delivered_on is None:
            return {"retain_until": None,
                    "basis": "No delivery date recorded for the customer's solution; clock not started"}
        start, start_label = delivered_on, "delivery to the customer"

    elif placed_on_market is None:
        return {"retain_until": None,
                "basis": "Product version not yet placed on market; retention clock not started"}

    elif clock_start == "support_period_end":
        if support_period_end is None:
            return {"retain_until": None,
                    "basis": "Support period end not set; this policy's clock starts at end of support"}
        start, start_label = support_period_end, "end of support"

    else:
        start, start_label = placed_on_market, "placing on market"

    # 2. Minimum period, extended to the product's end of support when that is later.
    minimum_end = add_years(start, min_years)
    support_is_later = support_period_end is not None and support_period_end > minimum_end

    if use_support_period_if_longer and support_is_later:
        return {"retain_until": support_period_end,
                "basis": f"Support period end (longer than {min_years} years)"}
    return {"retain_until": minimum_end, "basis": f"{min_years} years after {start_label}"}
