# Business Finder

Find businesses near a location (or within a search area) with simple, developer-friendly setup.

> **Status:** Early/Starter repo — expand sections as features land.

---

## What is this?

**Business Finder** is a small project intended to help you:
- search for businesses by keyword/category
- filter/sort results (distance, rating, open now, etc.)
- optionally display results on a map
- provide clean, reusable modules for “search → normalize → present”

If you’re building an MVP, this repo is designed to be easy to extend into:
- a CLI tool
- a REST API
- a web app

---

## Features (planned)

- [ ] Search by query (e.g., “coffee”, “pharmacy”, “electrician”)
- [ ] Location-based results (lat/lng + radius)
- [ ] Result normalization (consistent fields regardless of provider)
- [ ] Filtering/sorting (distance, rating, price level, open now)
- [ ] Optional map view
- [ ] Caching + rate-limit protection
- [ ] Tests + linting

---

## Tech Stack

Fill in what you’re using (examples):
- Backend: Python (FastAPI/Flask) / Node.js
- Frontend: React / Next.js / plain HTML
- Maps/Places provider: Google Places / Yelp / OpenStreetMap (Nominatim/Overpass)

> If you’re not sure yet, keep this section minimal until the first implementation lands.

---

## Getting Started

### Prerequisites
List what’s required to run the project (edit as needed):
- Git
- (Optional) Docker
- (Optional) API key for your chosen provider (Google Places / Yelp / etc.)

### Installation

Clone the repo:
```bash
git clone https://github.com/sidibos/business-finder.git
cd business-finder
