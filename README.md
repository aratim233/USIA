# USIA: SBOM Vault

**Unified SBOM Integration and Analysis Framework for Multi-Products**: M.Tech dissertation prototype (BITS Pilani WILP, SEZG628T).

Stores SBOMs (SPDX and CycloneDX) per product version and microservice as **long-lived evidentiary records**:
the original bytes are preserved with a SHA-256 hash, components are deduplicated on a canonical PURL (every uploaded component must declare a PURL; an SBOM missing one is rejected),
SBOMs are consolidated per product and per customer solution, and each product version's
retention period follows the **customer**: each customer falls under the regulations of its market, and every
product version delivered to it is kept under all of them. EU CRA Art. 13(13) (at least 10 years, or the support
period if longer) is created by default; administrators add further compliance policies (EU MDR, JC-STAR,
CERT-In, internal rules ...) on the Retention & Compliance page and choose them per customer.
Each solution has major versions (1.0, 2.0 ...) with the support end agreed with the customer. Delivering a
version freezes the consolidated SBOM of what was delivered, in both CycloneDX 1.5 and SPDX 2.3, and locks it; a minor update (a newer product
version swapped in) is delivered again under the same solution version, so the earlier delivery stays locked
and the retention date is unchanged. Delivered SBOMs are kept until the customer's support end or the
regulations require, whichever is later. The clock can start
at placing on market, delivery to the customer, or end of support. The latest date across all of them is binding.
Deleting an SBOM is refused while any applicable retention period is still running. Every change is written to an audit log.

## Stack
| Layer | Technology |
|---|---|
| Frontend | React 18 + Vite + React Router |
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2 |
| Database | PostgreSQL 16 |
| Auth | JWT (PyJWT) + bcrypt, roles `admin` / `viewer` |
| SBOM tooling | packageurl-python (PURL normalisation); SBOMs come from existing generator tools |

## Project layout
```
backend/app/main.py           assembles the API: plugs in the routers, runs start-up (OpenAPI docs at /docs)
backend/app/routers/          one file per area: login, dashboard, products, sboms, customers, retention, search, audit_log
backend/app/schemas.py        the request bodies the API accepts
backend/app/presenters.py     how database rows are turned into the JSON the screens receive
backend/app/services.py       service layer: upload, consolidation, retention, integrity, impact search, audit logging
backend/app/sbom_core.py      processing layer (no database): format detection, parsing, PURL normalisation, export, retention rule
backend/app/models.py         16 tables (14 domain tables in 4 zones, plus app_user and audit_log)
backend/app/startup.py        creates/upgrades the database and the demo users on start-up
backend/app/auth.py, db.py    login tokens and roles; database connection
backend/tests/                pytest tests
frontend/src/api.js           every call to the backend
frontend/src/format.js        readable dates, statuses and audit text
frontend/src/components.jsx   small shared pieces (message bar, status badge, keep-until date)
frontend/src/pages/           Dashboard, Products, Version detail, Customers & Solutions, Retention & Compliance, Impact Search, Audit
scripts/products.json      the 20 test cases (P01..P20): names, versions, CRA dates
scripts/seed.py            loads the test cases and your existing SBOMs into the running app
scripts/evaluate.py        produces the evaluation metrics tables
sample-sboms/              two small hand-written SBOMs (one CycloneDX, one SPDX) for a quick smoke test
```

## 1. Install the prerequisites (once)
- Python 3.11+, Node.js 20+, Git
- Docker Desktop (for PostgreSQL). Or install PostgreSQL 16 locally and create the user/db `usia`/`usia`

## 2. Run it
```bash
# Terminal 1: database
docker compose up -d

# Terminal 2: backend
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# API docs: http://localhost:8000/docs

# Terminal 3: frontend
cd frontend
npm install
npm run dev
# App: http://localhost:5173   login admin / admin123  (read-only user: viewer / viewer123)
```
## API summary
| Method | Path | Purpose |
|---|---|---|
| POST | /api/auth/login | Get a JWT |
| GET/POST | /api/products | List / create products (product ID P01, P02 … generated when no code is sent) |
| POST | /api/products/{id}/versions | Create a version (with placed-on-market and support-end dates) |
| GET/PATCH | /api/versions/{id} | Version detail / update lifecycle dates |
| POST | /api/versions/{id}/sboms | Upload an SBOM (multipart: microservice, file) |
| GET | /api/versions/{id}/consolidated?format=cyclonedx\|spdx | Consolidated SBOM for a product version |
| POST | /api/versions/{id}/integrity | Re-verify the SHA-256 of every stored original |
| GET | /api/sboms/{id}/original | Download the original, byte for byte |
| DELETE | /api/sboms/{id} | Refused while the SBOM is under retention |
| GET/POST | /api/customers, /api/customers/{id}/solutions, /api/solutions/{id}/versions | Customer side |
| POST | /api/solution-versions/{id}/items | Add a product version to a solution |
| GET | /api/solution-versions/{id}/consolidated | Consolidated SBOM for a customer solution |
| GET | /api/retention | Retention records: one per product version per applicable regulation |
| GET/POST | /api/retention/policies | List / create compliance retention policies |
| PUT/DELETE | /api/retention/policies/{id} | Edit a policy (all records recomputed) / delete an unused one |
| PUT | /api/customers/{id}/policies | Choose which regulations apply to a customer |
| POST | /api/solution-versions/{id}/deliveries | Deliver the working list: freeze and lock its consolidated SBOM (delivery 1, 2 ... = minor updates) |
| GET | /api/deliveries/{id}/sbom?format=cyclonedx\|spdx | Download a delivery's locked SBOM, byte for byte, in either format |
| POST | /api/deliveries/{id}/verify | Re-check both locked files of a delivery against their SHA-256 |
| PUT/DELETE | /api/customers/{id}, /api/solutions/{id}, /api/solution-versions/{id} | Edit / delete customer-side records (admin) |
| GET | /api/search?q= | Impact query: which products and solutions contain this component |
| GET | /api/audit | Audit log |
