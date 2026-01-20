import os
import csv
import json
import time
import argparse
import logging
import re
from urllib.parse import urlparse, urljoin

import requests

PLACES_BASE = "https://places.googleapis.com/v1"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Domains that still count as "no real website" (you can add/remove)
LOW_EFFORT_DOMAINS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "yelp.com",
    "www.yelp.com",
    "linktr.ee",
    "www.linktr.ee",
    "goo.gl",
    "maps.app.goo.gl",
}

# Email + contact page heuristics
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
CONTACT_HINTS = ("contact", "contact-us", "get-in-touch", "about", "about-us", "impressum", "support")


# ----------------------------
# .env loading (no dependency)
# ----------------------------
def load_dotenv(dotenv_path: str = ".env") -> None:
    """
    Minimal .env loader.
    Supports KEY=VALUE lines; ignores blanks and comments (# ...).
    Doesn't override env vars already set.
    """
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def require_api_key() -> str:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "Missing GOOGLE_MAPS_API_KEY.\n"
            "Fix: create a .env file next to this script with:\n"
            "  GOOGLE_MAPS_API_KEY=YOUR_REAL_API_KEY\n"
            "Or export it as an environment variable."
        )
    return api_key


# ----------------------------
# Utilities
# ----------------------------
def domain_of(url: str) -> str:
    """Extract domain from a URL safely."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_low_effort_or_missing_website(website_uri: str | None) -> tuple[bool, str]:
    """
    Return (is_lead, reason).
    Lead if website is missing or is a low-effort directory/social link.
    """
    if not website_uri:
        return True, "missing_website"
    d = domain_of(website_uri)
    if d in LOW_EFFORT_DOMAINS:
        return True, f"low_effort_domain:{d}"
    return False, f"has_website:{d or 'unknown'}"


def pretty_json(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)


# ----------------------------
# Email scraping helpers
# ----------------------------
def extract_emails_from_html(html: str) -> set[str]:
    """Return a set of emails found in HTML text."""
    if not html:
        return set()
    return set(re.findall(EMAIL_REGEX, html))


def fetch_html(url: str, *, dry_run: bool, timeout: int = 12) -> str:
    """
    Fetch HTML from a URL.
    - Follows redirects
    - Ensures Content-Type is HTML
    """
    if dry_run:
        return ""

    try:
        headers = {
            # Use a realistic UA. Some sites block Python default UAs.
            "User-Agent": "Mozilla/5.0 (compatible; LeadFinderBot/1.0; +https://example.com/bot)"
        }
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return ""
        content_type = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type:
            return ""
        return r.text or ""
    except Exception:
        return ""


def find_candidate_pages(base_url: str, html: str) -> list[str]:
    """
    Extract candidate pages (contact/about/etc.) from <a href="..."> links.
    Returns absolute URLs, deduped, ordered by appearance, with a small cap.
    """
    if not html:
        return []

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)

    candidates: list[str] = []
    for href in hrefs:
        href_lower = href.lower()

        # Skip non-http + junk links
        if href_lower.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        if any(hint in href_lower for hint in CONTACT_HINTS):
            candidates.append(urljoin(base_url, href))

    # Also try common paths even if not linked in nav/footer
    for path in ("contact", "contact-us", "about", "about-us", "impressum", "support"):
        candidates.append(urljoin(base_url.rstrip("/") + "/", path))

    # Deduplicate while preserving order
    seen = set()
    out = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)

    return out[:6]


def extract_email_from_website(website: str, *, dry_run: bool, debug: bool) -> tuple[str, str]:
    """
    Attempts to extract a contact email from:
    - homepage
    - best candidate contact/about pages

    Returns (email, email_source).
    """
    if not website or dry_run:
        return "", ""

    # 1) homepage
    html = fetch_html(website, dry_run=dry_run)
    emails = extract_emails_from_html(html)
    if emails:
        email = sorted(emails)[0]
        return email, "homepage"

    # 2) linked candidate pages + common paths
    for page_url in find_candidate_pages(website, html):
        page_html = fetch_html(page_url, dry_run=dry_run)
        emails = extract_emails_from_html(page_html)
        if emails:
            email = sorted(emails)[0]
            return email, f"page:{page_url}"

    if debug:
        logging.debug("No email found on website=%s", website)

    return "", ""


# ----------------------------
# HTTP wrapper with debug logs
# ----------------------------
def http_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    dry_run: bool = False,
) -> dict:
    """GET wrapper. In dry_run mode returns request info only."""
    if dry_run:
        return {"DRY_RUN": True, "method": "GET", "url": url, "params": params, "headers": headers}

    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def http_post(
    url: str,
    *,
    json_body: dict,
    headers: dict | None = None,
    timeout: int = 30,
    dry_run: bool = False,
) -> dict:
    """POST wrapper. In dry_run mode returns request info only."""
    if dry_run:
        return {"DRY_RUN": True, "method": "POST", "url": url, "json": json_body, "headers": headers}

    r = requests.post(url, json=json_body, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def geocode_city_country(api_key: str, city_country: str, *, dry_run: bool, debug: bool) -> tuple[float, float]:
    """Forward geocode 'City, Country' -> (lat, lng) using Geocoding API."""
    params = {"address": city_country, "key": api_key}
    data = http_get(GEOCODE_URL, params=params, timeout=30, dry_run=dry_run)

    if debug:
        logging.debug("Geocode response:\n%s", pretty_json(data))

    if dry_run:
        return 51.5074, -0.1278  # London placeholder

    if data.get("status") != "OK" or not data.get("results"):
        raise SystemExit(f"Geocoding failed for '{city_country}'. Response status: {data.get('status')}")

    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def places_post(api_key: str, path: str, body: dict, field_mask: str, *, dry_run: bool, debug: bool) -> dict:
    """POST to Places API (New) endpoints with FieldMask."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }
    url = f"{PLACES_BASE}/{path}"
    data = http_post(url, json_body=body, headers=headers, timeout=30, dry_run=dry_run)

    if debug:
        logging.debug("Places POST %s request body:\n%s", path, pretty_json(body))
        logging.debug("Places POST %s response:\n%s", path, pretty_json(data))

    return data


def places_get_details(api_key: str, place_id: str, field_mask: str, *, dry_run: bool, debug: bool) -> dict:
    """GET Place Details (New) with FieldMask."""
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }
    url = f"{PLACES_BASE}/places/{place_id}"
    data = http_get(url, headers=headers, timeout=30, dry_run=dry_run)

    if debug:
        logging.debug("Place Details request for %s", place_id)
        logging.debug("Place Details response:\n%s", pretty_json(data))

    return data


# ----------------------------
# Search modes
# ----------------------------
def text_search(api_key: str, niche: str, location: str, max_pages: int, *, dry_run: bool, debug: bool) -> list[dict]:
    """Places Text Search (New): searches by textQuery like 'barbers in London, UK'."""
    text_query = f"{niche} in {location}"

    field_mask = (
        "places.id,"
        "places.displayName,"
        "places.formattedAddress,"
        "places.rating,"
        "places.userRatingCount,"
        "places.nationalPhoneNumber"
    )

    results: list[dict] = []
    next_token = None

    for _ in range(max_pages):
        body = {"textQuery": text_query}
        if next_token:
            body["pageToken"] = next_token

        data = places_post(api_key, "places:searchText", body, field_mask, dry_run=dry_run, debug=debug)

        if dry_run:
            data = {
                "places": [
                    {
                        "id": "DRY_PLACE_1",
                        "displayName": {"text": f"{niche.title()} Example One"},
                        "formattedAddress": f"{location}",
                        "rating": 4.6,
                        "userRatingCount": 128,
                        "nationalPhoneNumber": "+44 20 0000 0001",
                    },
                    {
                        "id": "DRY_PLACE_2",
                        "displayName": {"text": f"{niche.title()} Example Two"},
                        "formattedAddress": f"{location}",
                        "rating": 4.3,
                        "userRatingCount": 34,
                        "nationalPhoneNumber": "+44 20 0000 0002",
                    },
                ]
            }

        results.extend(data.get("places", []))
        next_token = data.get("nextPageToken")

        if not next_token:
            break

        time.sleep(2)  # allow nextPageToken to become valid

    return results


def radius_search_via_text_bias(
    api_key: str,
    niche: str,
    lat: float,
    lng: float,
    radius_m: int,
    max_pages: int,
    *,
    dry_run: bool,
    debug: bool,
) -> list[dict]:
    """Radius-ish search: Text Search with locationBias circle around lat/lng."""
    field_mask = (
        "places.id,"
        "places.displayName,"
        "places.formattedAddress,"
        "places.rating,"
        "places.userRatingCount,"
        "places.nationalPhoneNumber"
    )

    results: list[dict] = []
    next_token = None

    for _ in range(max_pages):
        body = {
            "textQuery": niche,
            "locationBias": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(radius_m)}
            },
        }
        if next_token:
            body["pageToken"] = next_token

        data = places_post(api_key, "places:searchText", body, field_mask, dry_run=dry_run, debug=debug)

        if dry_run:
            data = {
                "places": [
                    {
                        "id": "DRY_RADIUS_1",
                        "displayName": {"text": f"{niche.title()} Nearby One"},
                        "formattedAddress": "Near your chosen radius",
                        "rating": 4.7,
                        "userRatingCount": 210,
                        "nationalPhoneNumber": "+44 20 0000 0101",
                    }
                ]
            }

        results.extend(data.get("places", []))
        next_token = data.get("nextPageToken")

        if not next_token:
            break

        time.sleep(2)

    return results


# ----------------------------
# Main pipeline
# ----------------------------
def run_pipeline(
    api_key: str,
    niche: str,
    location: str,
    mode: str,
    min_rating: float,
    min_reviews: int,
    max_pages: int,
    radius_m: int | None,
    *,
    dry_run: bool,
    debug: bool,
) -> list[dict]:
    # 1) Search
    if mode == "text":
        places = text_search(api_key, niche, location, max_pages, dry_run=dry_run, debug=debug)
    else:
        lat, lng = geocode_city_country(api_key, location, dry_run=dry_run, debug=debug)
        places = radius_search_via_text_bias(
            api_key,
            niche,
            lat,
            lng,
            radius_m=radius_m or 3000,
            max_pages=max_pages,
            dry_run=dry_run,
            debug=debug,
        )

    # 2) Deduplicate by place id
    by_id: dict[str, dict] = {}
    for p in places:
        pid = p.get("id")
        if pid and pid not in by_id:
            by_id[pid] = p

    logging.info("Found %d unique places (before filtering).", len(by_id))

    # 3) Details + filter leads
    details_mask = (
        "id,"
        "displayName,"
        "formattedAddress,"
        "rating,"
        "userRatingCount,"
        "nationalPhoneNumber,"
        "websiteUri,"
        "googleMapsUri"
    )

    leads: list[dict] = []

    for pid, p in by_id.items():
        rating = float(p.get("rating") or 0.0)
        count = int(p.get("userRatingCount") or 0)

        if rating < min_rating or count < min_reviews:
            continue

        d = places_get_details(api_key, pid, details_mask, dry_run=dry_run, debug=debug)

        if dry_run:
            fake_site = "" if "1" in pid else "https://www.facebook.com/example"
            d = {
                "id": pid,
                "displayName": {"text": (p.get("displayName") or {}).get("text", "")},
                "formattedAddress": p.get("formattedAddress", ""),
                "rating": rating,
                "userRatingCount": count,
                "nationalPhoneNumber": p.get("nationalPhoneNumber", ""),
                "websiteUri": fake_site or None,
                "googleMapsUri": f"https://maps.google.com/?q=place_id:{pid}",
            }

        website = d.get("websiteUri")
        email, email_source = extract_email_from_website(website or "", dry_run=dry_run, debug=debug)

        if debug:
            logging.debug("Email extraction: website=%s email=%s source=%s", website, email, email_source)

        is_lead, reason = is_low_effort_or_missing_website(website)

        if is_lead:
            leads.append(
                {
                    "name": (d.get("displayName") or {}).get("text", ""),
                    "rating": d.get("rating", ""),
                    "reviews": d.get("userRatingCount", ""),
                    "phone": d.get("nationalPhoneNumber", ""),
                    "email": email,
                    "email_source": email_source,
                    "address": d.get("formattedAddress", ""),
                    "website": website or "",
                    "maps_url": d.get("googleMapsUri", ""),
                    "reason": reason,
                    "place_id": d.get("id", pid),
                }
            )

        if not dry_run:
            time.sleep(0.1)

    return leads


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def prompt_if_missing(args: argparse.Namespace) -> argparse.Namespace:
    if not args.niche:
        args.niche = input("Niche (e.g., barbers, electricians): ").strip()
    if not args.location:
        args.location = input("Location (City, Country) (e.g., London, UK): ").strip()
    if not args.mode:
        choice = input("Search type: [1] Text Search  [2] Radius-based  (enter 1 or 2): ").strip()
        args.mode = "text" if choice == "1" else "radius"
    if args.min_rating is None:
        args.min_rating = float(input("Minimum rating (e.g., 4.2): ").strip() or "4.2")
    if args.min_reviews is None:
        args.min_reviews = int(input("Minimum review count (e.g., 20): ").strip() or "20")
    if args.max_pages is None:
        args.max_pages = int(input("Max pages to fetch (1-5 recommended): ").strip() or "2")
    if args.mode == "radius" and args.radius_m is None:
        args.radius_m = int(input("Radius in meters (e.g., 3000): ").strip() or "3000")
    return args


def main():
    parser = argparse.ArgumentParser(description="Find well-reviewed businesses with no (or low-effort) website.")
    parser.add_argument("--niche", help="Business niche, e.g. 'barbers'")
    parser.add_argument("--location", help="City, Country e.g. 'London, UK'")
    parser.add_argument("--mode", choices=["text", "radius"], help="Search mode: text or radius")
    parser.add_argument("--radius-m", type=int, dest="radius_m", help="Radius in meters (radius mode only)")
    parser.add_argument("--min-rating", type=float, dest="min_rating", help="Minimum rating threshold")
    parser.add_argument("--min-reviews", type=int, dest="min_reviews", help="Minimum review count threshold")
    parser.add_argument("--max-pages", type=int, dest="max_pages", help="Max pages to fetch")
    parser.add_argument("--out", default="leads_no_website.csv", help="Output CSV filename")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs (prints raw Google JSON + email steps)")
    parser.add_argument("--dry-run", action="store_true", help="No API calls; shows structure and writes sample CSV")
    parser.add_argument("--dotenv", default=".env", help="Path to .env file (default: .env)")
    args = parser.parse_args()

    configure_logging(args.debug)

    load_dotenv(args.dotenv)

    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not args.dry_run:
        api_key = require_api_key()
    else:
        api_key = api_key or "DRY_RUN_KEY"

    args = prompt_if_missing(args)

    logging.info("Mode=%s  Niche=%s  Location=%s  DryRun=%s", args.mode, args.niche, args.location, args.dry_run)

    leads = run_pipeline(
        api_key=api_key,
        niche=args.niche,
        location=args.location,
        mode=args.mode,
        min_rating=args.min_rating,
        min_reviews=args.min_reviews,
        max_pages=args.max_pages,
        radius_m=args.radius_m,
        dry_run=args.dry_run,
        debug=args.debug,
    )

    logging.info("Leads found (no/low-effort website): %d", len(leads))

    output_filename = args.niche + '_' + args.out
    output_filename = output_filename.replace(" ", "_")

    with open(output_filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name",
                "rating",
                "reviews",
                "phone",
                "email",
                "email_source",
                "address",
                "website",
                "maps_url",
                "reason",
                "place_id",
            ],
        )
        writer.writeheader()
        writer.writerows(leads)

    logging.info("Saved CSV: %s", output_filename)

    if leads:
        print("\nPreview (first 5 leads):")
        for row in leads[:5]:
            print(
                f"- {row['name']} | {row['rating']} ({row['reviews']} reviews) | "
                f"email={row['email'] or '—'} | {row['reason']} | {row['maps_url']}"
            )
    else:
        print("\nNo leads matched your thresholds.")


if __name__ == "__main__":
    main()