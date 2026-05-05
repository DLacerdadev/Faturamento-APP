from app.routers.customers import router as customers_router
from app.routers.uploads import router as uploads_router
from app.routers.reports import router as reports_router
from app.routers.integrations import router as integrations_router

__all__ = ["customers_router", "uploads_router", "reports_router", "integrations_router"]
