"""
USIA: SBOM Vault - REST API.

This file only assembles the application. The actual endpoints live in the
routers/ folder, one file per area of the app:

    routers/login.py       sign in
    routers/dashboard.py   dashboard numbers
    routers/products.py    products and product versions
    routers/sboms.py       SBOM upload, download, deletion lock, exports, integrity check
    routers/customers.py   customers, solutions, deliveries, customer regulations
    routers/retention.py   compliance policies and retention records
    routers/search.py      impact search
    routers/audit_log.py   audit log

Interactive API documentation: http://localhost:8000/docs
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .routers import audit_log, customers, dashboard, login, products, retention, sboms, search
from .startup import prepare_database

app = FastAPI(
    title="USIA: SBOM Vault",
    version="1.0",
    description="Unified SBOM Integration and Analysis Framework for Multi-Products",
)

# The React development server runs on another port, so the browser needs permission to call us.
allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router_module in (login, dashboard, products, sboms, customers, retention, search, audit_log):
    app.include_router(router_module.router)


@app.on_event("startup")
def on_startup() -> None:
    prepare_database()


@app.get("/", include_in_schema=False)
def root():
    """Opening the backend address in a browser shows the API documentation."""
    return RedirectResponse("/docs")
